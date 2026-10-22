import asyncio
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4
from app.db import db, dumps, question_rows
from app.settings import HOME, settings
from app.core.model_client import make_client
from app.core.security import credentials, encrypt, decrypt
from app.core.inference import infer, Stopped
from app.core.scorer import score


def now():
    return datetime.now(timezone.utc).isoformat()


def get_images():
    with db() as c:
        return {(r['replay_id'], r['image_step']): {'id': r['id'], 'mime': r['mime'], 'real': bool(r['real'])}
                for r in c.execute('SELECT m.replay_id,m.image_step,i.* FROM image_map m JOIN images i ON i.id=m.image_id')}


def dataset_digest(rows):
    return hashlib.sha256(dumps(rows).encode()).hexdigest()


def prepare_run(data, context=None):
    if data.official and (data.dimension is not None or data.limit is not None or data.question_ids):
        raise ValueError('正式比赛必须全量评测 1,279 道 QA，不允许维度、题数或题目 ID 筛选')
    cfg = context['config'] if context else settings()
    cap = cfg['competition']['max_calls_per_question']
    if not 1 <= data.calls <= cap:
        raise ValueError(f'主办方调用上限为 {cap}')
    with db() as c:
        model_row = c.execute('SELECT * FROM models WHERE id=?', (data.model_id,)).fetchone()
        prompt_row = c.execute('SELECT * FROM prompts WHERE id=?', (data.prompt_id,)).fetchone()
    if not model_row or not prompt_row:
        raise ValueError('模型或 Prompt 配置不存在')
    model = json.loads(model_row['body'])
    if data.official and model['provider'] == 'mock':
        raise ValueError('正式比赛禁止使用 Mock 模型，请选择真实模型')
    prompt = json.loads(prompt_row['body'])
    all_questions = context['questions'] if context else question_rows()
    rows = [dict(q) for q in all_questions if (not data.dimension or q['level']+'_'+q['role'] == data.dimension)
            and (not data.question_ids or q['question_id'] in data.question_ids)]
    if data.question_ids and set(data.question_ids) - {q['question_id'] for q in all_questions}:
        raise ValueError('指定题目 ID 不存在')
    if data.limit:
        rows = rows[:data.limit]
    if not rows:
        raise ValueError('题目范围为空，请先导入数据')
    images = context['images'] if context else get_images()
    from app.services.images import mock_image
    placeholder = (context.get('placeholder') if context else mock_image()) if model['provider'] == 'mock' else None
    for q in rows:
        q['image'] = images.get((q['replay_id'], q['image_step'])) or placeholder
    complete = len(rows) == sum(cfg['dataset']['expected_counts'].values()) and {
        k: sum(q['level']+'_'+q['role'] == k for q in rows) for k in cfg['dataset']['expected_counts']
    } == cfg['dataset']['expected_counts']
    real_images = all(q['image'] and q['image']['real'] and (HOME/'images'/q['image']['id']).is_file() for q in rows)
    if model['provider'] != 'mock' and not real_images:
        raise ValueError('真实模型多模态执行需要每题关联真实截图；缺图请使用 Mock')
    if data.official and (model['provider'] == 'mock' or not complete or not real_images):
        raise ValueError('正式测评须为完整 1279 题、全部关联真实截图、使用真实模型')
    key = credentials(model, model_row['secret']) if model['provider'] != 'mock' else ''
    snapshot = {'name': data.name, 'model': model, 'prompt': prompt, 'mode': data.mode, 'calls': data.calls,
                'early_stop': data.early_stop, 'official': data.official,
                'kind': 'official' if data.official else ('mock' if model['provider'] == 'mock' else 'partial_test'),
                'config': cfg, 'questions': rows, 'dataset_sha256': context['digest'] if context else dataset_digest(all_questions),
                'started_at': None, 'finished_at': None}
    rules = {'config': cfg, 'dataset': snapshot['dataset_sha256'],
             'images': [(q['question_id'], q['image']) for q in rows]}
    snapshot['cohort'] = dataset_digest(rules)[:16]
    return snapshot, encrypt(key)


def create_run(data):
    snapshot, secret = prepare_run(data)
    run_id = uuid4().hex
    with db() as c:
        c.execute('INSERT INTO runs(id,status,created_at,body,secret) VALUES(?,?,?,?,?)',
                  (run_id, 'queued', now(), dumps(snapshot), secret))
    return run_id


def load_run(run_id):
    with db() as c:
        row = c.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
    if not row:
        raise ValueError('任务不存在')
    return dict(row), json.loads(row['body'])


def report(run_id, include_results=True):
    row, body = load_run(run_id)
    with db() as c:
        results = {r['question_id']: json.loads(r['body']) for r in c.execute('SELECT * FROM results WHERE run_id=?', (run_id,))}
        calls = [dict(json.loads(r['body']), question_id=r['question_id']) for r in c.execute(
            'SELECT * FROM calls WHERE run_id=? ORDER BY question_id,ordinal', (run_id,))]
    summary = score(body['questions'], results)
    public_model = {k: body['model'].get(k) for k in ('name', 'model_id', 'provider', 'is_aggregator')}
    total = len(body['questions'])
    failed = sum(r['status'] != 'ok' for r in results.values())
    infrastructure = sum(r['status'] == 'infrastructure_error' for r in results.values())
    eligible = body['official'] and row['status'] == 'completed' and len(results) == total and not infrastructure
    started = body.get('started_at')
    end = body.get('finished_at') or now()
    elapsed = (datetime.fromisoformat(end)-datetime.fromisoformat(started)).total_seconds() if started else 0
    value = dict(run_id=run_id, name=body['name'], status=row['status'], kind=body['kind'],
                 model=public_model, mode=body['mode'], prompt_version=body['prompt']['version'],
                 calls_per_question=body['calls'], early_stop=body['early_stop'], budget=body['config']['competition'],
                 cohort=body['cohort'], dataset_sha256=body['dataset_sha256'],
                 created_at=row['created_at'], started_at=started, finished_at=body.get('finished_at'),
                 total=total, completed=len(results), progress=len(results)/total,
                 error_count=failed, incorrect_count=sum(not r['correct'] for r in results.values()),
                 infrastructure_errors=infrastructure, elapsed_seconds=elapsed, error=row['error'],
                 planned_calls=total*(1 if body['mode']=='single_pass' else body['calls']),
                 actual_calls=len(calls), external_api_calls=len(calls) if body['model']['provider'] != 'mock' else 0,
                 mock_requests=len(calls) if body['model']['provider']=='mock' else 0,
                 token_usage={k: sum(r.get('usage', {}).get(k, 0) for r in calls)
                              for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')},
                 eligible=eligible, **summary)
    value.update({k:body.get(k) for k in ('batch_id','team_id','submission_version')})
    if include_results:
        value['results'] = list(results.values())
        value['calls'] = calls
    return value


class Runner:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.task = None

    async def start(self):
        self.queue = asyncio.Queue()
        self.task = asyncio.create_task(self.worker())

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    def enqueue(self, run_id):
        self.queue.put_nowait(run_id)

    def enqueue_many(self, run_ids):
        self.queue.put_nowait(tuple(run_ids))

    async def worker(self):
        while True:
            packet = await self.queue.get()
            try:
                for run_id in packet if isinstance(packet,tuple) else (packet,):
                    try:
                        await self.execute(run_id)
                    except asyncio.CancelledError:
                        with db() as c:
                            c.execute("UPDATE runs SET status='interrupted',error='主办方进程停止，需统一重跑' WHERE id=?", (run_id,))
                        raise
                    except Exception as exc:
                        with db() as c:
                            c.execute("UPDATE runs SET status='infrastructure_error',error=? WHERE id=?", (f'主办方执行故障 ({type(exc).__name__})', run_id))
            finally:
                self.queue.task_done()

    async def execute(self, run_id):
        row, body = load_run(run_id)
        if row['status'] not in ('queued','stopping'):
            return
        if row['stop']:
            with db() as c:
                c.execute("UPDATE runs SET status='stopped' WHERE id=?", (run_id,))
            return
        body['started_at'] = now()
        with db() as c:
            c.execute("UPDATE runs SET status='running',body=? WHERE id=?", (dumps(body), run_id))
        client = make_client(body['model'], decrypt(row['secret']))
        def should_stop():
            with db() as c:
                return bool(c.execute('SELECT stop FROM runs WHERE id=?', (run_id,)).fetchone()[0])
        for q in body['questions']:
            if should_stop():
                break
            qid = q['question_id']
            def save_call(record):
                with db() as c:
                    c.execute('INSERT OR REPLACE INTO calls VALUES(?,?,?,?)', (run_id, qid, record['ordinal'], dumps(record)))
            # The model path receives only these public fields; private QA stays here for scoring.
            public = {k: q[k] for k in ('input_text', 'question_type', 'options', 'image')}
            try:
                value = await infer(public, body['prompt'], client, body['mode'], body['calls'],
                                    body['config']['competition']['max_calls_per_question'],
                                    body['config']['competition']['timeout_per_question_seconds'],
                                    save_call, should_stop, body['early_stop'])
            except Stopped:
                break
            except (OSError, ValueError) as exc:
                value = {'answer': None, 'status': 'infrastructure_error', 'error': f'本地资源故障 ({type(exc).__name__})', 'calls': 0}
            value['question_id'] = qid
            value['dimension'] = q['level']+'_'+q['role']
            value['correct'] = value['status']=='ok' and value['answer'] is not None and set(value['answer'])==set(q['gt_answer'])
            with db() as c:
                c.execute('INSERT INTO results VALUES(?,?,?)', (run_id, qid, dumps(value)))
            await asyncio.sleep(0)  # yield between even instant Mock requests
        body['finished_at'] = now()
        with db() as c:
            c.execute('UPDATE runs SET status=?,body=? WHERE id=?', ('stopped' if should_stop() else 'completed', dumps(body), run_id))


runner = Runner()
