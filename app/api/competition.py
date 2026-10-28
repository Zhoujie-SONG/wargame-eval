import asyncio
import json
from typing import Literal
from fastapi import APIRouter, UploadFile, File, Query
from fastapi.responses import Response, JSONResponse
from app.schemas import BatchConfig, BatchStart, BatchLaunch, ImportConfirm, OfficialRoster
from app.services import submissions as subs
from app.services import competition as comp
from app.services.competition_report import chart_svg,render_html,DIMENSIONS,CHARTS
from app.db import dumps, db

router=APIRouter(prefix='/api')


@router.get('/submissions/template')
def submission_template(format:Literal['csv','xlsx']='xlsx',mock:bool=False):
    rows=subs.template_rows(mock)
    content=subs.csv_bytes(subs.FIELDS,rows) if format=='csv' else subs.spreadsheet_bytes(subs.FIELDS,rows)
    return Response(content,media_type='text/csv' if format=='csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':f'attachment; filename={"mock-" if mock else ""}submissions.{format}'})


@router.post('/submissions/batch/preview')
async def preview_submissions(file:UploadFile=File(...)):
    content=await file.read(8_000_001)
    return await asyncio.to_thread(subs.preview_import,content,file.filename or '')


@router.post('/submissions/batch/import')
def import_submissions(value:ImportConfirm):return subs.confirm_import(value.preview_id)


@router.get('/submissions')
def submissions():return subs.list_submissions()


@router.get('/competition/status')
def competition_status():
    from app.services.competition_status import compute
    return compute()


@router.get('/submissions/official-roster')
def official_roster():
    with db() as c:
        configured,ids=subs.roster_ids(c)
    return {'configured':configured,'submission_ids':ids}


@router.put('/submissions/official-roster')
def save_official_roster(value:OfficialRoster):
    ids=value.submission_ids
    if len(ids)!=len(set(ids)):raise ValueError('不能重复选择相同方案')
    with db() as c:
        selected=[]
        for sid in ids:
            row=c.execute("""SELECT s.team_id,m.body AS model FROM submissions s
                JOIN models m ON m.id=s.model_id WHERE s.id=?""",(sid,)).fetchone()
            if not row or json.loads(row['model'])['provider']=='mock':
                raise ValueError('正式名单只能选择已导入的真实模型')
            selected.append(row['team_id'])
        if len(set(selected))!=len(selected):raise ValueError('同一队伍只能选择一个方案版本')
        c.execute("""INSERT INTO organizer_state(key,body) VALUES('official_roster',?)
            ON CONFLICT(key) DO UPDATE SET body=excluded.body""",(dumps(ids),))
    return {'submission_ids':ids,'configured':True}


@router.post('/evaluation-batches/preview')
def preview_batch(value:BatchConfig):return comp.preview_batch(value)[1]


@router.post('/evaluation-batches')
def create_batch(value:BatchConfig):return {'batch_id':comp.create_batch(value),'status':'draft'}


@router.get('/evaluation-batches')
def batches():return comp.list_batches()


@router.post('/evaluation-batches/start-new')
async def start_new(value:BatchLaunch):
    if not value.confirm_official:raise ValueError('请主动确认正式比赛规模及 API 费用后启动')
    data=BatchConfig(**value.model_dump(exclude={'confirm_official','preview_fingerprint'}))
    identity=await asyncio.to_thread(comp.create_batch,data,value.preview_fingerprint)
    return dispatch_batch(identity,True)


def dispatch_batch(identity,confirmed):
    try:return comp.start_batch(identity,confirmed)
    except comp.BatchDispatchError as exc:
        return JSONResponse(status_code=503,content={'detail':str(exc),'batch_id':identity,'status':'draft','retryable':True})


@router.get('/evaluation-batches/{batch_id}')
def batch(batch_id:str):return comp.get_batch(batch_id)


@router.get('/evaluation-batches/{batch_id}/progress')
def progress(batch_id:str):return comp.get_batch(batch_id)


@router.post('/evaluation-batches/{batch_id}/start')
async def start(batch_id:str,value:BatchStart|None=None):return dispatch_batch(batch_id,value.confirm_official if value else False)


@router.post('/evaluation-batches/{batch_id}/stop')
def stop(batch_id:str):return comp.stop_batch(batch_id)


@router.get('/evaluation-batches/{batch_id}/accuracy-curves')
def accuracy_curves(batch_id:str):return comp.accuracy_curves(batch_id)


@router.get('/evaluation-batches/{batch_id}/leaderboard')
def leaderboard(batch_id:str):return comp.analytics(batch_id)


@router.get('/evaluation-batches/{batch_id}/analytics')
def analytics(batch_id:str,team_id:list[str]|None=Query(default=None),top:int=Query(default=10,ge=0,le=100)):
    return comp.analytics(batch_id,team_id,top)


@router.get('/evaluation-batches/{batch_id}/charts/{chart}.svg')
def chart(batch_id:str,chart:Literal['ranking','dimensions','radar','efficiency'],group:str,
          team_id:list[str]|None=Query(default=None),top:int=Query(default=10,ge=0,le=100)):
    data=comp.analytics(batch_id,team_id,top)
    selected=next((g for g in data['groups'] if g['id']==group),None)
    if not selected:raise ValueError('当前筛选下没有该比较组')
    return Response(chart_svg(selected,chart),media_type='image/svg+xml',
                    headers={'Content-Disposition':f'attachment; filename={batch_id}-{chart}.svg'})


@router.get('/evaluation-batches/{batch_id}/export')
def export(batch_id:str,format:Literal['html','json','csv','xlsx']='html',audience:Literal['public','admin']='public',
           team_id:list[str]|None=Query(default=None),top:int=Query(default=10,ge=0,le=100),download:bool=True):
    data=comp.analytics(batch_id,team_id,top,audience)
    disposition='inline' if format=='html' and not download else 'attachment'
    headers={'Content-Disposition':f'{disposition}; filename={batch_id}-{audience}.{format}'}
    if format=='html':
        headers['Content-Security-Policy']="default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        return Response(render_html(data),media_type='text/html',headers=headers)
    if format=='json':return Response(dumps(data),media_type='application/json',headers=headers)
    headings=['group_id','rank','team_id','team_name','submission_version','model_id','is_aggregator','kind','mode',
              'L2_main','L3_main','L1_sub','L2_sub','weighted_score','mean_question_seconds','mean_calls','run_id']
    rows=[]
    for group in data['groups']:
        for r in group['entries']:
            rows.append([group['id'],r['rank'],r['team_id'],r['name'],r['submission_version'],r['model']['model_id'],
                         r['model']['is_aggregator'],r['kind'],r['mode'],*[r['dimensions'][key]['accuracy'] for key,_ in DIMENSIONS],
                         r['weighted_score'],r['mean_question_seconds'],r['mean_calls'],r['run_id']])
    content=subs.csv_bytes(headings,rows) if format=='csv' else subs.spreadsheet_bytes(headings,rows,'leaderboard')
    return Response(content,media_type='text/csv' if format=='csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers=headers)
