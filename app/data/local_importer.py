"""Import the organizer's qa_all.json and screenshots as one validated dataset."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

from app import settings as S
from app.db import db, dumps
from app.data.importer import _json_questions
from app.services.images import validate_image


def parse_json(content):
    """Keep all import entry points on the existing canonical question schema.

    Reimporting the same JSON must preserve stored question bodies and dataset
    digests, including the sample ID, source filename and private GT annotation.
    The workspace importer still owns its update/backup policy and matching.
    """
    rows, samples, counts = _json_questions(content)
    return rows, {sample: value['rows'] for sample, value in samples.items()}, counts


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
