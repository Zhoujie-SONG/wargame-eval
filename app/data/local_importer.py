"""Import the organizer's qa_all.json and screenshots as one validated dataset."""
import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from uuid import uuid4

from app import settings as S
from app.db import db, dumps
from app.services.images import validate_image


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} 必须是非空字符串')
    return value


def parse_json(content):
    try:
        source = json.loads(content.decode('utf-8-sig'))
    except (UnicodeError, ValueError):
        raise ValueError('qa_all.json 必须是有效的 UTF-8 JSON') from None
    if not isinstance(source, list) or not source or len(source) > 10000:
        raise ValueError('qa_all.json 顶层必须是非空题目数组，最多 10000 题')
    expected = S.settings()['dataset']['expected_counts']
    rows, samples, ids = [], defaultdict(list), set()
    for item in source:
        if not isinstance(item, dict):
            raise ValueError('每道 QA 必须是 JSON 对象')
        qid = _text(item.get('id'), '题目 id')
        if qid in ids:
            raise ValueError(f'{qid}: 重复题目 ID')
        ids.add(qid)
        sample = _text(item.get('sample_id'), f'{qid}: sample_id')
        # sample_id is also a filename component, never a path.
        if not re.fullmatch(r'[A-Za-z0-9-]+', sample):
            raise ValueError(f'{qid}: sample_id 格式无效')
        raw_role = item.get('role')
        role = {'core': 'main', 'subquestion': 'sub'}.get(raw_role) if isinstance(raw_role, str) else None
        level = item.get('level')
        if not role or not isinstance(level, str) or level + '_' + role not in expected:
            raise ValueError(f'{qid}: 不支持的层级或题目角色')
        replay, image = item.get('replay'), item.get('image')
        if not isinstance(replay, dict) or not isinstance(image, dict):
            raise ValueError(f'{qid}: 缺少 replay / image 关联信息')
        replay_id = _text(replay.get('id'), f'{qid}: replay.id')
        step = replay.get('step')
        if type(step) is not int or step < 0:
            raise ValueError(f'{qid}: replay.step 必须是非负整数')
        image_path = _text(image.get('path'), f'{qid}: image.path')
        relative = PurePosixPath(image_path.replace('\\', '/'))
        if (relative.is_absolute() or '..' in relative.parts or ':' in image_path
                or relative.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp')):
            raise ValueError(f'{qid}: image.path 必须是 images 内的安全相对路径')
        text = _text(item.get('model_input'), f'{qid}: model_input')
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        provenance = item.get('source')
        if not isinstance(provenance, dict) or provenance.get('model_input_sha256') != digest:
            raise ValueError(f'{qid}: model_input_sha256 与原始输入不符')
        options, answer = item.get('options'), item.get('answer')
        if (not isinstance(options, dict) or not options
                or any(not re.fullmatch('[A-H]', k) or not isinstance(v, str) for k, v in options.items())
                or set(re.findall(r'^([A-H])[.．、]', text, re.M)) != set(options)):
            raise ValueError(f'{qid}: 选项与 model_input 不一致')
        labels = answer.get('labels') if isinstance(answer, dict) else None
        kind = item.get('type')
        if (not isinstance(labels, list) or not labels or any(not isinstance(v, str) for v in labels)
                or len(set(labels)) != len(labels) or not set(labels) <= set(options)
                or kind not in ('single_choice', 'multiple_choice')
                or (kind == 'single_choice' and len(labels) != 1)):
            raise ValueError(f'{qid}: 答案或题型无效')
        row = dict(question_id=qid, parent_id=None, replay_id=replay_id, image_step=step,
                   image_path=image_path, level=level, role=role, question_type=kind,
                   input_text=text, input_sha256=digest, options=list(options), gt_answer=labels,
                   reference_text=item.get('review_markdown', ''),
                   source='qa_all.json', source_metadata=provenance)
        rows.append(row)
        samples[sample].append(row)
    for sample, group in samples.items():
        main = [q for q in group if q['role'] == 'main']
        if len(main) != 1:
            raise ValueError(f'{sample}: 必须且只能有一个主问题')
        core = main[0]
        for q in group:
            if any(q[k] != core[k] for k in ('replay_id', 'image_step', 'image_path')):
                raise ValueError(f'{q["question_id"]}: 同组 QA 的复盘、step 或图像路径不一致')
            if q['role'] == 'sub':
                q['parent_id'] = core['question_id']
    counts = dict(Counter(q['level'] + '_' + q['role'] for q in rows))
    if counts != expected:
        raise ValueError(f'导入数量不符：实际 {counts}；未写入数据库')
    return rows, samples, counts


def _screenshot(folder, sample, core):
    relative = PurePosixPath(core['image_path'].replace('\\', '/'))
    parts = relative.parts[1:] if relative.parts[0] == 'images' else relative.parts
    exact = folder.joinpath(*parts).resolve()
    if not exact.is_relative_to(folder):
        raise ValueError(f'{sample}: 图片路径越出 images 目录')
    if exact.is_file():
        return exact
    # Exported replay screenshots have a UUID suffix; never match just a substring.
    pattern = re.compile(re.escape(sample) + '_' + re.escape(sample) + '_step'
                         + str(core['image_step']) + r'_[0-9a-fA-F-]+\.(?:png|jpg|jpeg|webp)', re.I)
    matches = [p for p in folder.iterdir() if pattern.fullmatch(p.name) and p.is_file()]
    if len(matches) != 1:
        raise ValueError(f'{sample}: 截图匹配到 {len(matches)} 张，必须唯一；'
                         f'需要 {core["image_path"]} 或 {sample}_{sample}_step{core["image_step"]}_<UUID>.png')
    path = matches[0].resolve()
    if not path.is_relative_to(folder):
        raise ValueError(f'{sample}: 图片路径越出 images 目录')
    return path


def _atomic_write(path, content):
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def import_local(folder, source=None):
    folder = Path(folder).resolve()
    source_path = Path(source) if source else folder / 'qa_all.json'
    if not source_path.is_file():
        raise ValueError('工作区 images/qa_all.json 不存在；请将完整题库和截图放入项目根目录 images 文件夹')
    if source_path.stat().st_size > 20_000_000:
        raise ValueError('qa_all.json 超过 20MB')
    content = source_path.read_bytes()
    rows, samples, counts = parse_json(content)
    home = S.HOME
    for name in ('raw', 'images', 'processed'):
        (home / name).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=home) as temporary:
        staged, mappings, used = {}, {}, set()
        total_bytes = 0
        for sample, group in samples.items():
            core = next(q for q in group if q['role'] == 'main')
            path = _screenshot(folder, sample, core)
            size = path.stat().st_size
            total_bytes += size
            if size > 20_000_000 or total_bytes > 500_000_000:
                raise ValueError('单张截图不能超过 20MB，整批截图不能超过 500MB')
            if path in used:
                raise ValueError(f'{sample}: 不同主问题匹配到同一个截图文件')
            used.add(path)
            image_bytes = path.read_bytes()
            try:
                digest, mime = validate_image(image_bytes)
            except ValueError as exc:
                raise ValueError(f'{sample}: {exc}') from None
            (Path(temporary) / digest).write_bytes(image_bytes)
            staged[digest] = mime
            frame = (core['replay_id'], core['image_step'])
            if frame in mappings and mappings[frame] != digest:
                raise ValueError(f'{sample}: 同一复盘与 step 对应了不同图片')
            mappings[frame] = digest
        desired = {q['question_id']: q for q in rows}
        backup = None
        with db() as c:
            # Serialize dataset changes with task creation, and check state inside the lock.
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM runs WHERE status IN ('queued','running','stopping') LIMIT 1").fetchone():
                raise ValueError('有测评任务正在运行或排队，请结束后再导入数据集')
            for digest in staged:
                old = c.execute('SELECT real FROM images WHERE id=?', (digest,)).fetchone()
                if old and not old['real']:
                    raise ValueError('截图已被声明为 Mock，禁止将 Mock 图片提升为真实图片')
            existing = {r['question_id']: json.loads(r['body']) for r in c.execute('SELECT * FROM questions')}
            old_maps = [dict(r) for r in c.execute('SELECT * FROM image_map')]
            new_maps = [dict(replay_id=r, image_step=s, image_id=d) for (r, s), d in mappings.items()]
            changed = existing != desired or sorted(old_maps, key=dumps) != sorted(new_maps, key=dumps)
            changed_inputs = sum(qid in existing and existing[qid]['input_text'] != q['input_text']
                                 for qid, q in desired.items())
            if changed and existing:
                backup = 'dataset-backup-' + uuid4().hex + '.json'
                _atomic_write(home / 'raw' / backup,
                              dumps(dict(questions=list(existing.values()), image_map=old_maps)).encode('utf-8'))
            # Content-addressed assets are retained for old frozen runs. No model is called.
            for digest, mime in staged.items():
                os.replace(Path(temporary) / digest, home / 'images' / digest)
                c.execute('INSERT OR IGNORE INTO images VALUES(?,?,1)', (digest, mime))
            source_hash = hashlib.sha256(content).hexdigest()
            _atomic_write(home / 'raw' / (source_hash + '.json'), content)
            _atomic_write(home / 'processed' / 'questions.jsonl',
                          '\n'.join(dumps(q) for q in rows).encode('utf-8'))
            if changed:
                c.execute('DELETE FROM image_map')
                c.execute('DELETE FROM questions')
                c.executemany('INSERT INTO questions VALUES(?,?,?,?,?,?,?)',
                              [(q['question_id'], q['parent_id'], q['replay_id'], q['image_step'],
                                q['level'], q['role'], dumps(q)) for q in rows])
                c.executemany('INSERT INTO image_map VALUES(?,?,?)',
                              [(r, s, d) for (r, s), d in mappings.items()])
        return dict(total=len(rows), counts=counts, mapped_frames=len(mappings),
                    real_image_questions=len(rows), source_sha256=source_hash,
                    changed=changed, changed_inputs=changed_inputs, backup=backup)


def import_upload(qa_bytes, images):
    """Manual import: files the organizer picked in the browser, staged then validated exactly like the workspace import.

    images: iterable of (relative_name, bytes); the selected folder's own name is dropped.
    """
    if not qa_bytes:
        raise ValueError('请选择题库 JSON 文件')
    if len(qa_bytes) > 20_000_000:
        raise ValueError('qa_all.json 超过 20MB')
    home = S.HOME
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=home) as temporary:
        folder = Path(temporary) / 'images'
        folder.mkdir()
        count = 0
        for name, data in images:
            parts = [p for p in PurePosixPath(name.replace('\\', '/')).parts if p not in ('/', '.')]
            if len(parts) > 1:
                parts = parts[1:]  # webkitRelativePath starts with the chosen folder's name
            if (not parts or '..' in parts or ':' in parts[0]
                    or PurePosixPath(parts[-1]).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp')):
                continue  # ignore .DS_Store, thumbnails and other non-image files
            target = folder.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            count += 1
        if not count:
            raise ValueError('所选文件夹中没有 png / jpg / webp 截图')
        source = Path(temporary) / 'qa_all.json'
        source.write_bytes(qa_bytes)
        return import_local(folder, source)
