"""Parse only the explicitly delimited model input, never the surrounding annotations."""
import hashlib
import io
import json
import re
import os
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from app.db import db, dumps
from app.settings import HOME, settings
from app.services.images import validate_image


def parse_markdown(text, source):
    text = text.replace('\r\n', '\n')
    headers = list(re.finditer(r'^#{3,4} (?:核心问题|子问题)[^\n]*L[123][^\n]*$', text, re.M))
    blocks = list(re.finditer(r'```text\n(.*?)\n```', text, re.S))
    if len(headers) != len(blocks):
        raise ValueError(f'{source}: QA 标题和输入块数量不一致')
    result = []
    for i, header in enumerate(headers):
        end = headers[i+1].start() if i+1 < len(headers) else len(text)
        segment = text[header.end():end]
        identity = re.search(r'评测ID[：:]\s*`([^`]+)`', segment)
        block = re.search(r'```text\n(.*?)\n```', segment, re.S)
        gt = re.search(r'\*\*GT[^\n]*?[：:]([A-H、,，\s]+)。?\*\*', segment)
        if not all((identity, block, gt)):
            raise ValueError(f'{source}: 标题 {i+1} 缺少 ID / 输入块 / GT')
        # Metadata is taken from the enclosing sample, not inferred from answers.
        before = text[:header.start()]
        replay = list(re.finditer(r'复盘[：:]\s*`([^`]+)`', before))
        step = list(re.finditer(r'图像数据step[：:]\s*(\d+)', before))
        if not replay or not step:
            raise ValueError('缺少复盘或 step 元数据')
        qid = identity[1]
        role = 'main' if header[0].startswith('### ') else 'sub'
        answers = re.findall('[A-H]', gt[1])
        level = re.search(r'L[123]', header[0])[0]
        model_input = block[1]  # Preserve every character inside the input fence.
        options = re.findall(r'^([A-H])[.．、]', model_input, re.M)
        if not answers or not set(answers) <= set(options):
            raise ValueError(f'{qid}: 答案格式或选项字段不完整')
        result.append(dict(question_id=qid, parent_id=None if role == 'main' else qid.rsplit('-', 1)[0]+'-CORE',
                           replay_id=replay[-1][1], image_step=int(step[-1][1]), image_path=None,
                           level=level, role=role,
                           question_type='multiple_choice' if '多选' in header[0] else 'single_choice',
                           input_text=model_input, gt_answer=answers, options=options,
                           reference_text=segment[block.end():], source=source,
                           input_sha256=hashlib.sha256(model_input.encode()).hexdigest()))
    return result


def import_zip(content):
    rows = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 50_000_000:
            raise ValueError('ZIP 解压大小超过 50MB')
        for member in sorted(archive.namelist()):
            if member.lower().endswith('.md'):
                rows.extend(parse_markdown(archive.read(member).decode('utf-8-sig'), member))
    ids = {q['question_id'] for q in rows}
    if not rows or len(ids) != len(rows):
        raise ValueError('无题目或存在重复 ID')
    if any(q['parent_id'] and q['parent_id'] not in ids for q in rows):
        raise ValueError('子题关联的主问题缺失')
    counts = dict(Counter(q['level']+'_'+q['role'] for q in rows))
    expected = settings()['dataset']['expected_counts']
    if counts != expected:
        raise ValueError(f'导入数量不符：实际 {counts}，预期 {expected}；未写入数据库')
    with db() as c:
        existing = {r['question_id']: json.loads(r['body']) for r in c.execute('SELECT question_id,body FROM questions')}
        if existing and existing != {q['question_id']: q for q in rows}:
            raise ValueError('已有数据不同，禁止覆盖已审核 QA；请使用独立数据目录')
        for q in rows:
            c.execute('INSERT OR IGNORE INTO questions VALUES (?,?,?,?,?,?,?)',
                      (q['question_id'], q['parent_id'], q['replay_id'], q['image_step'], q['level'], q['role'], dumps(q)))
    digest = hashlib.sha256(content).hexdigest()
    (HOME / 'raw' / f'{digest}.zip').write_bytes(content)
    (HOME / 'processed' / 'questions.jsonl').write_text('\n'.join(dumps(q) for q in rows), encoding='utf-8')
    return dict(total=len(rows), counts=counts, archive_sha256=digest)


def _json_questions(content):
    """Translate qa_all.json while keeping reference material out of model input."""
    try:
        data = json.loads(content.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('qa_all.json 必须是有效的 UTF-8 JSON 文件') from None
    if not isinstance(data, list) or not data or len(data) > 10000:
        raise ValueError('qa_all.json 顶层必须是非空题目数组，最多 10000 题')
    rows, samples, seen = [], {}, set()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError('题目数组中包含无效记录')
        qid, sid = item.get('id'), item.get('sample_id')
        if not isinstance(qid, str) or not qid or qid in seen:
            raise ValueError('题目缺少有效 ID 或存在重复 ID')
        seen.add(qid)
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', sid):
            raise ValueError(f'{qid}: sample_id 无效')
        if not qid.startswith(sid + '-'):
            raise ValueError(f'{qid}: ID 与 sample_id 不一致')
        raw_role = item.get('role')
        role = {'core': 'main', 'subquestion': 'sub'}.get(raw_role) if isinstance(raw_role, str) else None
        level, question_type = item.get('level'), item.get('type')
        if role is None or level not in ('L1', 'L2', 'L3'):
            raise ValueError(f'{qid}: role / level 无效')
        if question_type not in ('single_choice', 'multiple_choice'):
            raise ValueError(f'{qid}: 不支持的题型')
        replay, image = item.get('replay'), item.get('image')
        if (not isinstance(replay, dict) or not isinstance(replay.get('id'), str)
                or not replay['id'] or type(replay.get('step')) is not int or replay['step'] < 0):
            raise ValueError(f'{qid}: 缺少有效的 replay.id / replay.step')
        if not isinstance(image, dict) or not isinstance(image.get('path'), str) or not image['path']:
            raise ValueError(f'{qid}: 缺少 image.path')
        relative = PurePosixPath(image['path'].replace('\\', '/'))
        if (relative.is_absolute() or '..' in relative.parts or ':' in image['path']
                or relative.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp')):
            raise ValueError(f'{qid}: image.path 必须是 images 内的安全相对路径')
        model_input, options, answer = item.get('model_input'), item.get('options'), item.get('answer')
        if not isinstance(model_input, str) or not model_input.strip():
            raise ValueError(f'{qid}: model_input 为空')
        if (not isinstance(options, dict) or len(options) < 2
                or any(not re.fullmatch(r'[A-H]', key) or not isinstance(value, str) or not value.strip()
                       for key, value in options.items())):
            raise ValueError(f'{qid}: options 无效')
        input_options = re.findall(r'^([A-H])[.．、]', model_input, re.M)
        if len(input_options) != len(options) or set(input_options) != set(options):
            raise ValueError(f'{qid}: options 与 model_input 中的选项不一致')
        option_texts = re.findall(r'^([A-H])[.．、]\s*(.*?)(?=^[A-H][.．、]|\Z)', model_input, re.M | re.S)
        if any(text.strip() != options[label].strip() for label, text in option_texts):
            raise ValueError(f'{qid}: options 文字与 model_input 不一致')
        labels = answer.get('labels') if isinstance(answer, dict) else None
        if (not isinstance(labels, list) or not labels or any(not isinstance(v, str) for v in labels)
                or len(set(labels)) != len(labels) or not set(labels) <= set(options)
                or (question_type == 'single_choice' and len(labels) != 1)):
            raise ValueError(f'{qid}: GT 答案与题型 / 选项不一致')
        if 'label' in answer:
            label_text = answer['label']
            if (not isinstance(label_text, str) or not re.fullmatch(r'[A-H、,，\s]+', label_text)
                    or set(re.findall(r'[A-H]', label_text)) != set(labels)):
                raise ValueError(f'{qid}: answer.label 与 answer.labels 不一致')
        source = item.get('source')
        if not isinstance(source, dict):
            raise ValueError(f'{qid}: source 无效')
        digest = hashlib.sha256(model_input.encode('utf-8')).hexdigest()
        if source.get('model_input_sha256') != digest:
            raise ValueError(f'{qid}: model_input SHA256 与来源记录不一致')
        reference = answer.get('source_markdown', '')
        review = item.get('review_markdown', '')
        if not isinstance(reference, str) or not isinstance(review, str):
            raise ValueError(f'{qid}: 参考标注必须是文本')
        metadata = dict(replay=replay, image_path=image['path'], group_id=item.get('group_id'),
                        source_sample_id=item.get('source_sample_id'))
        sample = samples.setdefault(sid, dict(metadata=metadata, rows=[]))
        if sample['metadata'] != metadata:
            raise ValueError(f'{sid}: 主子题的回放、step 或图像关联不一致')
        row = dict(question_id=qid, parent_id=None, sample_id=sid,
                   replay_id=replay['id'], image_step=replay['step'], image_path=image['path'],
                   level=level, role=role, question_type=question_type,
                   input_text=model_input, gt_answer=labels, options=input_options,
                   reference_text=reference + review, source=source.get('file', 'qa_all.json'),
                   input_sha256=digest)
        sample['rows'].append(row)
        rows.append(row)
    for sid, sample in samples.items():
        parents = [q for q in sample['rows'] if q['role'] == 'main']
        if len(parents) != 1:
            raise ValueError(f'{sid}: 每个样本必须包含且仅包含一道主问题')
        for row in sample['rows']:
            if row['role'] == 'sub':
                row['parent_id'] = parents[0]['question_id']
    counts = dict(Counter(q['level'] + '_' + q['role'] for q in rows))
    expected = settings()['dataset']['expected_counts']
    if counts != expected:
        raise ValueError(f'导入数量不符：实际 {counts}，预期 {expected}；未写入数据库')
    return rows, samples, counts


def _match_local_images(samples, images_dir):
    files = sorted(path for path in images_dir.rglob('*')
                   if path.is_file() and path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'))
    if not files:
        raise ValueError('图像目录中没有 PNG / JPEG / WebP 图片')
    replay_frames = {}
    for sample in samples.values():
        replay = sample['metadata']['replay']
        replay_frames.setdefault(replay['id'], set()).add(replay['step'])
    uuid = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    result, used = {}, {}
    for sid, sample in samples.items():
        metadata, candidates = sample['metadata'], set()
        replay = metadata['replay']
        # image.path is dataset metadata, never an arbitrary path to read outside images_dir.
        logical = PurePosixPath(metadata['image_path'].replace('\\', '/'))
        if logical.is_absolute() or '..' in logical.parts or (logical.parts and ':' in logical.parts[0]):
            raise ValueError(f'{sid}: image.path 必须是安全的相对路径')
        relative = Path(*logical.parts)
        exact_paths = {images_dir / relative}
        if logical.parts and logical.parts[0].lower() == 'images':
            exact_paths.add(images_dir / Path(*logical.parts[1:]))
        exact_paths.add(images_dir / logical.name)
        for path in exact_paths:
            if path in files:
                candidates.add(path)
        # The local screenshot exporter names a frame with sample, step and an export UUID.
        escaped_sid, escaped_replay = re.escape(sid), re.escape(replay['id'])
        step = replay['step']
        patterns = [rf'{escaped_sid}_{escaped_sid}_step{step}_{uuid}',
                    rf'{escaped_sid}_step{step}(?:_{uuid})?',
                    rf'{escaped_replay}_step{step}(?:_{uuid})?']
        if len(replay_frames[replay['id']]) == 1:
            patterns.append(escaped_replay)
        for path in files:
            if any(re.fullmatch(pattern, path.stem, re.I) for pattern in patterns):
                candidates.add(path)
        if len(candidates) != 1:
            reason = '缺少对应图片' if not candidates else f'匹配到 {len(candidates)} 张图片，关联有歧义'
            raise ValueError(f'{sid} / step{step}: {reason}；未写入数据库')
        path = candidates.pop()
        resolved = path.resolve()
        if not resolved.is_relative_to(images_dir):
            raise ValueError(f'{sid}: 图片不能通过链接指向目录外')
        frame = (replay['id'], step)
        if resolved in used and used[resolved] != frame:
            raise ValueError(f'{sid}: 同一图片被关联到不同回放或 step')
        used[resolved] = frame
        result[sid] = path
    return result


def _write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def import_local(qa_path, images_dir):
    """Import the complete local QA file and uniquely associated screenshots in one transaction."""
    qa_path, images_dir = Path(qa_path).expanduser(), Path(images_dir).expanduser().resolve()
    if not qa_path.is_file():
        raise ValueError('qa_all.json 路径不存在或不是文件')
    if not images_dir.is_dir():
        raise ValueError('图像目录不存在')
    if qa_path.stat().st_size > 50_000_000:
        raise ValueError('QA JSON 超过 50MB')
    content = qa_path.read_bytes()
    rows, samples, counts = _json_questions(content)
    matches = _match_local_images(samples, images_dir)
    image_records, mappings, manifest = {}, {}, []
    for sid, path in matches.items():
        if path.stat().st_size > 20_000_000:
            raise ValueError(f'{sid}: 图片超过 20MB')
        image_content = path.read_bytes()
        try:
            image_id, mime = validate_image(image_content)
        except ValueError as exc:
            raise ValueError(f'{sid}: {exc}') from None
        image_records[image_id] = (mime, image_content)
        replay = samples[sid]['metadata']['replay']
        frame = (replay['id'], replay['step'])
        if frame in mappings and mappings[frame] != image_id:
            raise ValueError(f'{sid}: 同一回放与 step 对应不同图片')
        mappings[frame] = image_id
        manifest.append(dict(sample_id=sid, replay_id=frame[0], image_step=frame[1],
                             source_image=path.relative_to(images_dir).as_posix(), image_id=image_id))
    digest = hashlib.sha256(content).hexdigest()
    desired = {q['question_id']: q for q in rows}
    # Check all state before writing. A failed batch never adds questions or image mappings.
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM runs WHERE status IN ('queued','running','stopping') LIMIT 1").fetchone():
            raise ValueError('有测评任务正在运行或排队，请结束后再导入数据集')
        existing = {r['question_id']: json.loads(r['body']) for r in c.execute('SELECT question_id,body FROM questions')}
        if existing and existing != desired:
            raise ValueError('已有数据不同，禁止覆盖已审核 QA；请使用独立数据目录')
        existing_maps = {(r['replay_id'], r['image_step']): r['image_id'] for r in c.execute('SELECT * FROM image_map')}
        if any(frame in existing_maps and existing_maps[frame] != image_id for frame, image_id in mappings.items()):
            raise ValueError('已有截图关联不同，禁止覆盖；请使用独立数据目录')
        for image_id in image_records:
            old = c.execute('SELECT real FROM images WHERE id=?', (image_id,)).fetchone()
            if old and not old['real']:
                raise ValueError('该图片已有 Mock 来源声明，禁止转为真实图片')
        already_imported = existing == desired and all(existing_maps.get(frame) == value for frame, value in mappings.items())
        for image_id, (mime, image_content) in image_records.items():
            _write_atomic(HOME / 'images' / image_id, image_content)
            c.execute('INSERT OR IGNORE INTO images VALUES(?,?,1)', (image_id, mime))
        _write_atomic(HOME / 'raw' / f'{digest}.json', content)
        _write_atomic(HOME / 'processed' / 'questions.jsonl', '\n'.join(dumps(q) for q in rows).encode('utf-8'))
        _write_atomic(HOME / 'processed' / 'local_image_map.json', dumps(manifest).encode('utf-8'))
        for q in rows:
            c.execute('INSERT OR IGNORE INTO questions VALUES (?,?,?,?,?,?,?)',
                      (q['question_id'], q['parent_id'], q['replay_id'], q['image_step'], q['level'], q['role'], dumps(q)))
        for (replay_id, step), image_id in mappings.items():
            c.execute('INSERT OR IGNORE INTO image_map VALUES(?,?,?)', (replay_id, step, image_id))
    return dict(total=len(rows), counts=counts, mapped_frames=len(mappings),
                unique_images=len(image_records), archive_sha256=digest, already_imported=already_imported)
