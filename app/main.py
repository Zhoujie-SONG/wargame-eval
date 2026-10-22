import asyncio
import csv
import html
import io
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
import yaml
from fastapi import FastAPI, File, UploadFile, Form, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.db import db, init_db, dumps, question_rows
from app.settings import ROOT, HOME, CONFIG, settings
from app.schemas import ModelConfig, PromptConfig, RunConfig, Limits, ImageMapping, InfraMark
from app.core.security import encrypt, credentials, validate_url, valid_env
from app.core.prompt_manager import version
from app.core.model_client import make_client
from app.data.importer import import_zip
from app.services.images import store_image, map_images
from app.services.runs import runner, create_run, report, load_run, get_images, now


@asynccontextmanager
async def lifespan(app):
    init_db()
    settings()
    with db() as c:
        if not c.execute('SELECT 1 FROM models').fetchone():
            body = ModelConfig(name='Mock 联调模型', is_aggregator=False).model_dump(exclude={'api_key'})
            c.execute('INSERT INTO models VALUES(?,?,NULL)', ('mock-demo', dumps(body)))
        if not c.execute('SELECT 1 FROM prompts').fetchone():
            body = PromptConfig(name='默认 Prompt').model_dump()
            body['version'] = version(body)
            c.execute('INSERT INTO prompts VALUES(?,?)', ('default', dumps(body)))
    await runner.start()
    yield
    await runner.close()


app = FastAPI(title='兵棋多模态智能体竞赛测评系统', version='1.2.0', lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])


@app.middleware('http')
async def local_protection(request: Request, call_next):
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and (
        request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin.rstrip('/') != str(request.base_url).rstrip('/'))
    ):
        return JSONResponse({'detail': '禁止跨站管理请求'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers.setdefault('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's default error response can echo submitted API keys.
    return JSONResponse({'detail': [{'loc': list(e['loc']), 'msg': e['msg'], 'type': e['type']} for e in exc.errors()]},status_code=422)


@app.get('/api/health')
def health():
    return {'status': 'ok', 'version': '1.2.0'}


@app.get('/docs', include_in_schema=False)
def local_api_docs():
    # No CDN or inline scripts; compatible with the application's strict CSP.
    routes = ''.join('<h2>'+html.escape(method.upper()+' '+path)+'</h2><pre>'+
                     html.escape(json.dumps(operation,ensure_ascii=False,indent=2))+'</pre>'
                     for path,operations in app.openapi()['paths'].items()
                     for method,operation in operations.items())
    return Response('<!doctype html><html lang="zh"><meta charset="utf-8"><title>API 文档</title>'+
                    '<link rel="stylesheet" href="/static/style.css"><h1>兵棋测评 API</h1><a href="/openapi.json">完整 OpenAPI JSON Schema</a>'+routes+'</html>',media_type='text/html')


@app.get('/api/settings')
def get_settings():
    return settings()


@app.put('/api/settings')
def update_settings(value: Limits):
    config = settings()
    config['competition'] = value.model_dump()
    target = CONFIG.with_suffix('.yaml.tmp')
    target.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
    os.replace(target, CONFIG)
    return config


@app.post('/api/dataset/import')
async def import_dataset(file: UploadFile = File(...)):
    content = await file.read(20_000_001)
    if len(content) > 20_000_000:
        raise ValueError('ZIP 上传超过 20MB')
    try:
        return import_zip(content)
    except (UnicodeError, __import__('zipfile').BadZipFile):
        raise ValueError('无法读取 ZIP 或 UTF-8 Markdown') from None


@app.post('/api/dataset/import-bundled')
def import_bundled():
    candidates = sorted(ROOT.glob('selected_240_six_groups*.zip'))
    if not candidates:
        raise ValueError('工作区缺少原始 ZIP，请上传')
    return import_zip(candidates[0].read_bytes())


@app.get('/api/dataset/stats')
def dataset_stats():
    questions = question_rows()
    images = get_images()
    counts = {k: sum(q['level']+'_'+q['role']==k for q in questions) for k in settings()['dataset']['expected_counts']}
    ready = sum(bool((v := images.get((q['replay_id'], q['image_step']))) and v['real'] and (HOME/'images'/v['id']).is_file()) for q in questions)
    frames = {(q['replay_id'],q['image_step']) for q in questions}
    return {'total': len(questions), 'counts': counts, 'expected': settings()['dataset']['expected_counts'],
            'real_image_questions': ready, 'missing_image_questions': len(questions)-ready,
            'unique_frames': len(frames), 'mapped_frames': len(set(images)&frames)}


@app.get('/api/questions')
def questions(question_id: str = '', replay_id: str = '', level: str = '', role: str = '', offset: int = 0, limit: int = 20):
    rows = [q for q in question_rows() if question_id in q['question_id'] and replay_id in q['replay_id']
            and (not level or q['level']==level) and (not role or q['role']==role)]
    images = get_images()
    selected = rows[max(0, offset):max(0, offset)+min(max(1, limit), 200)]
    return {'total': len(rows), 'items': [dict(q, image=images.get((q['replay_id'],q['image_step']))) for q in selected]}


@app.get('/api/questions/{qid}')
def question(qid: str):
    with db() as c:
        row = c.execute('SELECT body FROM questions WHERE question_id=?', (qid,)).fetchone()
    if not row:
        raise HTTPException(404, '题目不存在')
    q = json.loads(row['body'])
    return dict(q, image=get_images().get((q['replay_id'],q['image_step'])))


@app.get('/api/images')
def images():
    with db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM images ORDER BY id')]


@app.post('/api/images')
async def upload_image(file: UploadFile = File(...), real: bool = Form(...)):
    return store_image(await file.read(20_000_001), real)


@app.post('/api/images/map')
def map_image(items: list[ImageMapping]):
    if len(items) > 10000:
        raise ValueError('映射条数过多')
    return map_images(items)


@app.get('/api/images/missing.csv')
def missing_images():
    images = get_images()
    frames = sorted({(q['replay_id'],q['image_step']) for q in question_rows()})
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['replay_id','image_step','image_id'])
    for replay, step in frames:
        if (replay,step) not in images or not images[(replay,step)]['real']:
            writer.writerow([replay,step,''])
    return Response('\ufeff'+stream.getvalue(), media_type='text/csv', headers={'Content-Disposition':'attachment; filename=missing_images.csv'})


@app.get('/api/images/{image_id}')
def get_image(image_id: str):
    with db() as c:
        row = c.execute('SELECT * FROM images WHERE id=?', (image_id,)).fetchone()
    if not row or not (HOME/'images'/row['id']).is_file():
        raise HTTPException(404, '图片不存在')
    return FileResponse(HOME/'images'/row['id'], media_type=row['mime'])


@app.get('/api/models')
def models():
    with db() as c:
        return [dict(json.loads(r['body']), id=r['id'], has_key=bool(r['secret'] or json.loads(r['body']).get('api_key_env')))
                for r in c.execute('SELECT * FROM models')]


def save_model(value, model_id=None):
    body = value.model_dump(exclude={'api_key'})
    if not valid_env(value.api_key_env):
        raise ValueError('环境变量名称格式错误')
    if value.provider == 'openai':
        body['base_url'] = validate_url(value.base_url)
    with db() as c:
        old = c.execute('SELECT secret FROM models WHERE id=?', (model_id,)).fetchone() if model_id else None
        if model_id and not old:
            raise HTTPException(404, '模型不存在')
        secret = encrypt(value.api_key) if value.api_key else (old['secret'] if old else None)
        if value.api_key_env or value.provider == 'mock':
            secret = None
        if value.provider == 'openai' and not secret and not value.api_key_env:
            raise ValueError('请填写 API Key 或凭据环境变量名')
        identity = model_id or uuid4().hex
        c.execute('INSERT OR REPLACE INTO models VALUES(?,?,?)', (identity,dumps(body),secret))
    return {'id': identity}


@app.post('/api/models')
def add_model(value: ModelConfig):
    return save_model(value)


@app.put('/api/models/{model_id}')
def edit_model(model_id: str, value: ModelConfig):
    return save_model(value,model_id)


@app.delete('/api/models/{model_id}')
def delete_model(model_id: str):
    with db() as c:
        c.execute('DELETE FROM models WHERE id=?',(model_id,))
    return {'deleted': model_id}


@app.post('/api/models/{model_id}/test')
async def test_model(model_id: str):
    with db() as c:
        row = c.execute('SELECT * FROM models WHERE id=?', (model_id,)).fetchone()
    if not row:
        raise HTTPException(404, '模型不存在')
    body = json.loads(row['body'])
    try:
        key = credentials(body,row['secret']) if body['provider']!='mock' else ''
        result = await asyncio.wait_for(make_client(body,key).complete([{'role':'user','content':'请仅回复 A'}],15),15)
        return {'ok':True,'usage':result['usage'],'requests':1,'mock':body['provider']=='mock'}
    except Exception as exc:
        return JSONResponse({'ok':False,'detail':f'连接测试失败 ({type(exc).__name__})；请检查凭据和地址','requests':1},status_code=400)


@app.get('/api/prompts')
def prompts():
    with db() as c:
        return [dict(json.loads(r['body']),id=r['id']) for r in c.execute('SELECT * FROM prompts')]


@app.post('/api/prompts')
def add_prompt(value: PromptConfig):
    if value.default_calls > settings()['competition']['max_calls_per_question']:
        raise ValueError('推理次数超过主办方上限')
    body = value.model_dump()
    body['version'] = version(body)
    identity = uuid4().hex
    with db() as c:
        c.execute('INSERT INTO prompts VALUES(?,?)',(identity,dumps(body)))
    return {'id':identity,'version':body['version']}


@app.post('/api/runs')
def start_run(value: RunConfig):
    identity = create_run(value)
    runner.enqueue(identity)
    return {'run_id':identity}


@app.get('/api/runs')
def runs():
    with db() as c:
        ids = [r[0] for r in c.execute('SELECT id FROM runs ORDER BY created_at DESC LIMIT 200')]
    return [report(identity,False) for identity in ids]


@app.get('/api/runs/{run_id}')
def get_run(run_id: str):
    return report(run_id)


@app.post('/api/runs/{run_id}/stop')
def stop_run(run_id: str):
    row,_ = load_run(run_id)
    if row['status'] in ('running','queued','stopping'):
        with db() as c:
            c.execute("UPDATE runs SET stop=1,status='stopping' WHERE id=?", (run_id,))
    return {'status':'stop_requested','detail':'当前请求完成或超时后安全停止，不再发起下一次调用'}


@app.post('/api/runs/{run_id}/infrastructure')
def mark_infrastructure(run_id: str, value: InfraMark):
    row,_ = load_run(run_id)
    if row['status'] in ('running','queued','stopping'):
        raise ValueError('请先安全停止任务')
    with db() as c:
        for qid in value.question_ids:
            r = c.execute('SELECT body FROM results WHERE run_id=? AND question_id=?',(run_id,qid)).fetchone()
            if not r:
                raise ValueError('题目结果不存在')
            body = json.loads(r['body'])
            body.update(status='infrastructure_error',correct=False,infrastructure_reason=value.reason,marked_at=now())
            c.execute('UPDATE results SET body=? WHERE run_id=? AND question_id=?',(dumps(body),run_id,qid))
    return {'marked':len(value.question_ids)}


@app.post('/api/runs/{run_id}/rerun')
def rerun(run_id: str):
    row,body = load_run(run_id)
    if row['status']=='superseded':
        raise ValueError('此任务已有替代运行，不能重复重跑历史任务')
    stats = report(run_id,False)
    if row['status'] not in ('interrupted','infrastructure_error') and not stats['infrastructure_errors']:
        raise ValueError('仅主办方基础设施故障可重跑；普通模型错误计错')
    # A replacement run uses exactly the frozen inputs, model, prompts and rules.
    identity = uuid4().hex
    body['replaces_run_id'] = run_id
    body['started_at'] = body['finished_at'] = None
    with db() as c:
        c.execute('INSERT INTO runs(id,status,created_at,body,secret) VALUES(?,?,?,?,?)', (identity,'queued',now(),dumps(body),row['secret']))
        c.execute("UPDATE runs SET status='superseded' WHERE id=?",(run_id,))
        c.execute('UPDATE batch_entries SET run_id=? WHERE run_id=?',(identity,run_id))
    runner.enqueue(identity)
    return {'run_id':identity}


@app.get('/api/leaderboard')
def leaderboard(kind: str = 'official', cohort: str = ''):
    with db() as c:
        identities = [r[0] for r in c.execute("SELECT id FROM runs WHERE status='completed' ORDER BY created_at")]
    values = [report(identity,False) for identity in identities]
    values = [r for r in values if (r['eligible'] if kind=='official' else r['kind']==kind)]
    if cohort:
        values = [r for r in values if r['cohort']==cohort]
    values.sort(key=lambda r:r['weighted_score'],reverse=True)
    # Different frozen rules are never assigned a shared official rank.
    ranks = {}
    for r in values:
        group = (r.get('batch_id'),r['cohort'],r['mode'],1 if r['mode']=='single_pass' else r['calls_per_question'],r['early_stop'])
        ranks[group] = ranks.get(group,0)+1
        r['rank'] = ranks[group]
        from app.services.runs import dataset_digest
        r['ranking_group'] = dataset_digest(group)[:16]
    return values


@app.get('/api/runs/{run_id}/export/{format}')
def export(run_id: str, format: str):
    value = report(run_id)
    if format=='json':
        content, mime = dumps(value), 'application/json'
    elif format=='csv':
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['run_id','kind','question_id','dimension','answer','correct','status','calls','elapsed_seconds'])
        for r in value['results']:
            cells = [run_id,value['kind'],r['question_id'],r['dimension'],','.join(r['answer'] or []),r['correct'],r['status'],r['calls'],r.get('elapsed_seconds',0)]
            writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in cells])
        content,mime = '\ufeff'+stream.getvalue(),'text/csv'
    elif format=='html':
        content = '<!doctype html><html lang="zh"><meta charset="utf-8"><title>测评报告</title><h1>兵棋测评报告</h1><p>类型：'+html.escape(value['kind'])+'；综合分：'+str(round(value['weighted_score'],4))+'</p><p>Mock / 局部测试成绩不属于正式 Benchmark。</p><pre>'+html.escape(json.dumps(value,ensure_ascii=False,indent=2))+'</pre></html>'
        mime='text/html'
    else:
        raise HTTPException(404,'支持 json / csv / html')
    return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename={run_id}.{format}'})


from app.api.competition import router as competition_router
app.include_router(competition_router)
app.mount('/static',StaticFiles(directory=ROOT/'app/web'),name='static')


@app.get('/')
def index():
    return FileResponse(ROOT/'app/web/index.html')
