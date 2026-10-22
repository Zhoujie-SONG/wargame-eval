"""Parse only the explicitly delimited model input, never the surrounding annotations."""
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from app.db import db, dumps
from app.settings import HOME, settings


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
