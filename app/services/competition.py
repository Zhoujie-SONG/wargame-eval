import json
from datetime import datetime, timezone
from uuid import uuid4
from app.db import db,dumps,question_rows
from app.settings import settings
from app.schemas import RunConfig
from app.services.runs import prepare_run,report,load_run,now,dataset_digest,get_images,runner
from app.services.images import mock_image


def preview_batch(data):
    if len(data.submission_ids)!=len(set(data.submission_ids)):raise ValueError('参赛方案不可重复')
    if data.mode=='single_pass' and data.calls!=1:raise ValueError('Single-pass 固定一次，请将调用次数设为 1')
    if data.mode!='self_refine' and data.early_stop:raise ValueError('仅 Self-refine 支持提前结束')
    with db() as c:
        submissions={r['id']:dict(r) for r in c.execute('SELECT * FROM submissions')}
    if any(s not in submissions for s in data.submission_ids):raise ValueError('部分参赛方案不存在')
    selected=[submissions[s] for s in data.submission_ids]
    if len({s['team_id'] for s in selected})!=len(selected):raise ValueError('同一批次每支队伍只能选择一个方案版本')
    questions=question_rows();cfg=settings()
    context={'config':cfg,'questions':questions,'digest':dataset_digest(questions),'images':get_images(),'placeholder':mock_image()}
    plans=[]
    for index,s in enumerate(selected,1):
        try:
            run=RunConfig(name=s['name'],model_id=s['model_id'],prompt_id=s['prompt_id'],
                          **data.model_dump(exclude={'request_key','name','submission_ids'}))
            body,secret=prepare_run(run,context)
        except ValueError as exc:raise ValueError(f'第 {index} 个方案：{exc}；本批没有启动任务') from None
        body.update(team_id=s['team_id'],submission_version=s['version'])
        plans.append((s,body,secret))
    count=len(plans[0][1]['questions']);actual=1 if data.mode=='single_pass' else data.calls
    summary={'teams':len(plans),'questions_per_team':count,'total_qa':len(plans)*count,
             'max_requests':len(plans)*count*actual,
             'max_external_requests':sum(count*actual for _,b,_ in plans if b['model']['provider']!='mock'),
             'mode':data.mode,'calls':actual,'official':data.official,'budget':cfg['competition'],
             'real_images_complete':all(q['image'] and q['image']['real'] for _,b,_ in plans for q in b['questions']),
             'dataset_complete':count==sum(cfg['dataset']['expected_counts'].values()),
             'items':[{'team_id':s['team_id'],'team_name':s['name'],'submission_version':s['version'],
                       'model_id':b['model']['model_id'],'prompt_version':b['prompt']['version']} for s,b,_ in plans]}
    return plans,summary


def create_batch(data):
    with db() as c:
        old=c.execute('SELECT id,body FROM evaluation_batches WHERE request_key=?',(data.request_key,)).fetchone()
    request=data.model_dump(exclude={'request_key'})
    if old:
        if json.loads(old['body'])['request']!=request:raise ValueError('幂等键已用于不同配置，请重新预览')
        return old['id']
    plans,summary=preview_batch(data)
    identity=uuid4().hex
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('SELECT id,body FROM evaluation_batches WHERE request_key=?',(data.request_key,)).fetchone()
        if old:
            if json.loads(old['body'])['request']!=request:raise ValueError('幂等键冲突')
            return old['id']
        c.execute('INSERT INTO evaluation_batches(id,request_key,name,created_at,body) VALUES(?,?,?,?,?)',
                  (identity,data.request_key,data.name,now(),dumps({'request':request,'plan':summary})))
        for s,body,secret in plans:
            rid=uuid4().hex;body['batch_id']=identity
            c.execute('INSERT INTO runs(id,status,created_at,body,secret) VALUES(?,?,?,?,?)',(rid,'draft',now(),dumps(body),secret))
            c.execute('INSERT INTO batch_entries VALUES(?,?,?,?)',(identity,s['team_id'],s['id'],rid))
    return identity


def start_batch(identity):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT started,stopped FROM evaluation_batches WHERE id=?',(identity,)).fetchone()
        if not row:raise ValueError('批次不存在')
        if row['stopped']:raise ValueError('批次已停止，请创建新批次')
        if row['started']:return {'batch_id':identity,'started':False,'message':'批次已经启动，不重复排队'}
        ids=[r[0] for r in c.execute('SELECT run_id FROM batch_entries WHERE batch_id=?',(identity,))]
        c.execute('UPDATE evaluation_batches SET started=1 WHERE id=?',(identity,))
        for rid in ids:c.execute("UPDATE runs SET status='queued' WHERE id=? AND status='draft'",(rid,))
    for rid in ids:runner.enqueue(rid)
    return {'batch_id':identity,'started':True,'tasks':len(ids)}


def stop_batch(identity):
    with db() as c:
        if not c.execute('SELECT 1 FROM evaluation_batches WHERE id=?',(identity,)).fetchone():raise ValueError('批次不存在')
        c.execute('UPDATE evaluation_batches SET stopped=1 WHERE id=?',(identity,))
        c.execute("UPDATE runs SET stop=1,status=CASE WHEN status IN ('draft','queued') THEN 'stopped' ELSE 'stopping' END "
                  "WHERE id IN (SELECT run_id FROM batch_entries WHERE batch_id=?) AND status IN ('draft','queued','running','stopping')",(identity,))
    return {'batch_id':identity,'status':'stop_requested'}


def get_batch(identity):
    with db() as c:
        row=c.execute('SELECT * FROM evaluation_batches WHERE id=?',(identity,)).fetchone()
        ids=[r[0] for r in c.execute('SELECT run_id FROM batch_entries WHERE batch_id=? ORDER BY team_id',(identity,))]
    if not row:raise ValueError('批次不存在')
    meta=json.loads(row['body']);runs=[report(rid,False) for rid in ids]
    statuses=[r['status'] for r in runs]
    active=sum(s in ('queued','running','stopping') for s in statuses)
    status='draft' if not row['started'] and not row['stopped'] else 'running' if active else 'completed' if all(s=='completed' for s in statuses) else 'stopped_or_interrupted'
    total=sum(r['total'] for r in runs);completed=sum(r['completed'] for r in runs)
    starts=[r['started_at'] for r in runs if r['started_at']];ends=[r['finished_at'] for r in runs if r['finished_at']]
    elapsed=(datetime.fromisoformat(max(ends) if ends and not active else now())-datetime.fromisoformat(min(starts))).total_seconds() if starts else 0
    return {'batch_id':identity,'name':row['name'],'created_at':row['created_at'],'status':status,'started':bool(row['started']),
            'teams':len(runs),'queued':statuses.count('queued'),'running':statuses.count('running'),
            'finished':statuses.count('completed'),'failed_or_interrupted':sum(s in ('interrupted','infrastructure_error','stopped') for s in statuses),
            'total':total,'completed':completed,'progress':completed/total if total else 0,'elapsed_seconds':elapsed,
            'actual_calls':sum(r['actual_calls'] for r in runs),'external_api_calls':sum(r['external_api_calls'] for r in runs),
            'error_count':sum(r['error_count'] for r in runs),'plan':meta['plan'],'runs':runs}


def list_batches():
    with db() as c:ids=[r[0] for r in c.execute('SELECT id FROM evaluation_batches ORDER BY created_at DESC')]
    return [{k:v for k,v in get_batch(i).items() if k!='runs'} for i in ids]


def analytics(identity,team_ids=None,top=10,audience='public'):
    batch=get_batch(identity);grouped={};excluded=[]
    selected=None if team_ids is None else set(team_ids)
    if selected is not None and not selected<={r['team_id'] for r in batch['runs']}:raise ValueError('筛选队伍不属于此批次')
    for r in batch['runs']:
        if selected is not None and r['team_id'] not in selected:continue
        if r['status']!='completed' or r['completed']!=r['total'] or r['infrastructure_errors'] or (r['kind']=='official' and not r['eligible']):
            excluded.append({'team_id':r['team_id'],'name':r['name'],'status':r['status'],'reason':'未完整完成或存在主办方故障'});continue
        _,snapshot=load_run(r['run_id'])
        # Mode, actual allowance and early stop matter even when legacy cohort matches.
        key=(r['kind'],r['cohort'],r['mode'],1 if r['mode']=='single_pass' else r['calls_per_question'],bool(snapshot['early_stop']))
        with db() as c:
            results=[json.loads(x[0]) for x in c.execute('SELECT body FROM results WHERE run_id=?',(r['run_id'],))]
        times=[v['elapsed_seconds'] for v in results if isinstance(v.get('elapsed_seconds'),(int,float))]
        item={k:r[k] for k in ('team_id','name','submission_version','run_id','model','kind','mode','total','completed','weighted_score',
                                'overall_accuracy','dimensions','actual_calls','external_api_calls','mock_requests','elapsed_seconds')}
        item.update(mean_question_seconds=sum(times)/len(times) if times else None,mean_calls=r['actual_calls']/r['completed'],
                    prompt_version=r['prompt_version'],group_id=dataset_digest(key)[:16])
        if audience=='admin':item.update(token_usage=r['token_usage'],error_count=r['error_count'],dataset_sha256=r['dataset_sha256'],budget=r['budget'])
        grouped.setdefault(key,[]).append(item)
    groups=[]
    for key,entries in grouped.items():
        entries.sort(key=lambda r:(-r['weighted_score'],r['team_id']))
        for rank,r in enumerate(entries,1):r['rank']=rank
        groups.append({'id':dataset_digest(key)[:16],'kind':key[0],'cohort':key[1],'mode':key[2],'calls':key[3],
                       'early_stop':key[4],'entries':entries,'top_entries':entries[:top] if top else entries})
    return {'batch_id':identity,'name':batch['name'],'audience':audience,'created_at':batch['created_at'],'status':batch['status'],
            'teams':batch['teams'],'selected_teams':len(batch['runs']) if selected is None else len(selected),
            'finished':batch['finished'],'qa_completed':batch['completed'],'qa_total':batch['total'],
            'actual_calls':batch['actual_calls'],'external_api_calls':batch['external_api_calls'],
            'elapsed_seconds':batch['elapsed_seconds'],'generated_at':now(),'groups':groups,'excluded':excluded,
            'scope_note':'顶部为整批统计；排行榜和图表仅包含筛选后完整完成且无主办方故障的队伍。不同条件分别排名。',
            'weights':{'L2_main':.35,'L3_main':.35,'L1_sub':.15,'L2_sub':.15}}
