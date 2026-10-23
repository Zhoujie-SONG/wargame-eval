"""Lightweight monitoring counters maintained transactionally by SQLite triggers.

Old databases are backfilled on first access. Normal monitoring never parses
large frozen question arrays or model outputs.
"""
import json
from datetime import datetime, timezone
from app.db import dumps
from app.core.scorer import WEIGHTS, score_counts

DIMS=tuple(WEIGHTS)
FIELDS=('completed','error_count','incorrect_count','infrastructure_errors',
        'actual_calls','prompt_tokens','completion_tokens','total_tokens') + tuple(
        f'{k}_{suffix}' for k in DIMS for suffix in ('completed','correct'))


def metadata(body):
    totals={k:0 for k in DIMS}
    for q in body['questions']:
        totals[q['level']+'_'+q['role']]+=1
    return dict(name=body['name'],kind=body['kind'],
                model={k:body['model'].get(k) for k in ('name','model_id','provider','is_aggregator')},
                mode=body['mode'],prompt_version=body['prompt']['version'],
                calls_per_question=body['calls'],early_stop=body['early_stop'],
                budget=body['config']['competition'],cohort=body['cohort'],
                dataset_sha256=body['dataset_sha256'],started_at=body.get('started_at'),
                finished_at=body.get('finished_at'),dimension_totals=totals,
                official=body['official'],batch_id=body.get('batch_id'),
                team_id=body.get('team_id'),submission_version=body.get('submission_version'))


def _expr(alias,path):
    return f"json_extract({alias}.body,'$.{path}')"


def _result(alias,sign):
    correct=_expr(alias,'correct');status=_expr(alias,'status');dim=_expr(alias,'dimension')
    parts={
        'completed':'1',
        'error_count':f"CASE WHEN {status} != 'ok' THEN 1 ELSE 0 END",
        'incorrect_count':f"CASE WHEN {correct} THEN 0 ELSE 1 END",
        'infrastructure_errors':f"CASE WHEN {status} = 'infrastructure_error' THEN 1 ELSE 0 END",
    }
    for key in DIMS:
        parts[key+'_completed']=f"CASE WHEN {dim} = '{key}' THEN 1 ELSE 0 END"
        parts[key+'_correct']=f"CASE WHEN {dim} = '{key}' AND {correct} THEN 1 ELSE 0 END"
    return {k:f'({sign}) * ({value})' for k,value in parts.items()}


def _call(alias,sign):
    return {'actual_calls':str(sign),**{
        k:f"({sign}) * COALESCE({_expr(alias,'usage.'+k)},0)"
        for k in ('prompt_tokens','completion_tokens','total_tokens')}}


def install(c):
    cols=', '.join(f'"{key}" INTEGER NOT NULL DEFAULT 0' for key in FIELDS)
    c.execute(f'CREATE TABLE IF NOT EXISTS run_monitor (run_id TEXT PRIMARY KEY, meta TEXT NOT NULL, {cols})')
    for table,build in (('results',_result),('calls',_call)):
        for action,parts in (('INSERT',[build('NEW',1)]),
                             ('UPDATE',[build('NEW',1),build('OLD',-1)]),
                             ('DELETE',[build('OLD',-1)])):
            cols=[]
            for col in FIELDS:
                values=[part[col] for part in parts if col in part]
                if values:cols.append(f'"{col}"="{col}"+'+'+'.join(values))
            alias='OLD' if action=='DELETE' else 'NEW'
            sql=(f'CREATE TRIGGER IF NOT EXISTS mon_{table}_{action.lower()} '
                 f'AFTER {action} ON {table} BEGIN UPDATE run_monitor SET {",".join(cols)} '
                 f'WHERE run_id={alias}.run_id; END')
            c.execute(sql)


def ensure(c,run_id,snapshot=None):
    if c.execute('SELECT 1 FROM run_monitor WHERE run_id=?',(run_id,)).fetchone():
        return
    if snapshot is None:
        row=c.execute('SELECT body FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row:raise ValueError('任务不存在')
        snapshot=json.loads(row['body'])
    meta=metadata(snapshot)
    counts={k:0 for k in FIELDS}
    for row in c.execute("""SELECT json_extract(body,'$.dimension') AS dimension, COUNT(*) AS n,
           SUM(CASE WHEN json_extract(body,'$.correct') THEN 1 ELSE 0 END) AS correct,
           SUM(CASE WHEN json_extract(body,'$.correct') THEN 0 ELSE 1 END) AS incorrect,
           SUM(CASE WHEN json_extract(body,'$.status')!='ok' THEN 1 ELSE 0 END) AS failed,
           SUM(CASE WHEN json_extract(body,'$.status')='infrastructure_error' THEN 1 ELSE 0 END) AS infra
           FROM results WHERE run_id=? GROUP BY dimension""",(run_id,)):
        counts['completed']+=row['n']
        counts['error_count']+=row['failed'] or 0
        counts['incorrect_count']+=row['incorrect'] or 0
        counts['infrastructure_errors']+=row['infra'] or 0
        if row['dimension'] in DIMS:
            counts[row['dimension']+'_completed']+=row['n']
            counts[row['dimension']+'_correct']+=row['correct'] or 0
    calls=c.execute("""SELECT COUNT(*) AS n,
           SUM(COALESCE(json_extract(body,'$.usage.prompt_tokens'),0)) AS prompt,
           SUM(COALESCE(json_extract(body,'$.usage.completion_tokens'),0)) AS completion,
           SUM(COALESCE(json_extract(body,'$.usage.total_tokens'),0)) AS total
           FROM calls WHERE run_id=?""",(run_id,)).fetchone()
    counts.update(actual_calls=calls['n'],prompt_tokens=calls['prompt'] or 0,
                  completion_tokens=calls['completion'] or 0,total_tokens=calls['total'] or 0)
    fields=['run_id','meta']+list(FIELDS)
    c.execute('INSERT OR IGNORE INTO run_monitor('+','.join('"'+k+'"' for k in fields)+') VALUES ('+
              ','.join('?' for _ in fields)+')',
              [run_id,dumps(meta)]+[counts[k] for k in FIELDS])


def ensure_batch(c,identity=None):
    clause='' if identity is None else 'AND e.batch_id=?'
    args=() if identity is None else (identity,)
    missing=c.execute("""SELECT r.id,r.body FROM runs r
        JOIN batch_entries e ON e.run_id=r.id
        LEFT JOIN run_monitor m ON m.run_id=r.id
        WHERE m.run_id IS NULL """+clause,args).fetchall()
    for row in missing:ensure(c,row['id'],json.loads(row['body']))


def set_times(c,run_id,started=None,finished=None):
    row=c.execute('SELECT meta FROM run_monitor WHERE run_id=?',(run_id,)).fetchone()
    if not row:return
    meta=json.loads(row['meta'])
    if started is not None:meta['started_at']=started
    if finished is not None:meta['finished_at']=finished
    c.execute('UPDATE run_monitor SET meta=? WHERE run_id=?',(dumps(meta),run_id))


def lightweight_report(run_id):
    from app.db import db
    with db() as c:
        ensure(c,run_id)
        row=c.execute("""SELECT r.status,r.created_at,r.error,m.* FROM runs r
            JOIN run_monitor m ON m.run_id=r.id WHERE r.id=?""",(run_id,)).fetchone()
    m=json.loads(row['meta'])
    dimensions={key:dict(total=m['dimension_totals'][key],
                         correct=row[key+'_correct'],completed=row[key+'_completed'])
                for key in DIMS}
    summary=score_counts(dimensions)
    total=sum(m['dimension_totals'].values())
    started=m.get('started_at');finished=m.get('finished_at')
    elapsed=(datetime.fromisoformat(finished or datetime.now(timezone.utc).isoformat())-
             datetime.fromisoformat(started)).total_seconds() if started else 0
    calls=row['actual_calls'];mock=m['model']['provider']=='mock'
    value=dict(run_id=run_id,name=m['name'],status=row['status'],kind=m['kind'],
               model=m['model'],mode=m['mode'],prompt_version=m['prompt_version'],
               calls_per_question=m['calls_per_question'],early_stop=m['early_stop'],
               budget=m['budget'],cohort=m['cohort'],dataset_sha256=m['dataset_sha256'],
               created_at=row['created_at'],started_at=started,finished_at=finished,
               total=total,completed=row['completed'],progress=row['completed']/total,
               error_count=row['error_count'],incorrect_count=row['incorrect_count'],
               infrastructure_errors=row['infrastructure_errors'],elapsed_seconds=elapsed,
               error=row['error'],planned_calls=total*(1 if m['mode']=='single_pass' else m['calls_per_question']),
               actual_calls=calls,external_api_calls=0 if mock else calls,
               mock_requests=calls if mock else 0,
               token_usage={k:row[k] for k in ('prompt_tokens','completion_tokens','total_tokens')},
               eligible=m['official'] and row['status']=='completed' and
                        row['completed']==total and not row['infrastructure_errors'],**summary)
    value.update({k:m.get(k) for k in ('batch_id','team_id','submission_version')})
    return value
