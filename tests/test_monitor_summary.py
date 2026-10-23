"""Fast monitor regression tests, no private benchmark files or external requests."""
import json
from uuid import uuid4

from app.db import db, dumps
from app.services.monitor import ensure
from app.services.runs import report
from app.services import competition

DIMS=('L2_main','L3_main','L1_sub','L2_sub')


def make_run(c, run_id, batch_id=None, questions_per_dimension=2):
    questions=[]
    for dim in DIMS:
        level,role=dim.split('_')
        for i in range(questions_per_dimension):
            questions.append(dict(question_id=f'{dim}-{i}',level=level,role=role,gt_answer=['A']))
    body=dict(name='合成测试',kind='official',model=dict(name='M',model_id='M1',provider='openai',is_aggregator=False),
         mode='self_refine',calls=3,early_stop=False,prompt={'version':'prompt-1'},
         config={'competition':{'max_calls_per_question':3,'timeout_per_question_seconds':120}},
         cohort='same',dataset_sha256='digest',started_at='2026-10-23T00:00:00+00:00',
         finished_at='2026-10-23T00:00:02+00:00',official=True,questions=questions,
         batch_id=batch_id,team_id=run_id,submission_version='v1')
    c.execute('INSERT INTO runs(id,status,created_at,body) VALUES(?,?,?,?)',
              (run_id,'completed','2026-10-23T00:00:00+00:00',dumps(body)))
    return body


def seed_records(c,run_id,body):
    for i,q in enumerate(body['questions']):
        outcome=dict(question_id=q['question_id'],status='ok' if i%3 else 'api_error',
                     correct=i%2==0,dimension=q['level']+'_'+q['role'],elapsed_seconds=.015)
        c.execute('INSERT INTO results VALUES(?,?,?)',(run_id,q['question_id'],dumps(outcome)))
        for j in range(1,4):
            call=dict(ordinal=j,status='ok',output='A'*400,
                      usage={'prompt_tokens':11+j,'completion_tokens':5,'total_tokens':16+j})
            c.execute('INSERT INTO calls VALUES(?,?,?,?)',
                      (run_id,q['question_id'],j,dumps(call)))


def test_fast_report_bitwise_matches_full_detail(client):
    run_id=uuid4().hex
    with db() as c:
        body=make_run(c,run_id)
        ensure(c,run_id,body)
        seed_records(c,run_id,body)
    detail=report(run_id,True);fast=report(run_id,False)
    assert fast=={k:v for k,v in detail.items() if k not in ('results','calls')}
    assert fast['actual_calls']==24 and fast['token_usage']['total_tokens']>0
    with db() as c:
        qid=body['questions'][0]['question_id']
        original=json.loads(c.execute('SELECT body FROM results WHERE run_id=? AND question_id=?',
                                     (run_id,qid)).fetchone()[0])
        original.update(status='infrastructure_error',correct=False)
        c.execute('UPDATE results SET body=? WHERE run_id=? AND question_id=?',
                  (dumps(original),run_id,qid))
        call={'ordinal':1,'status':'ok','output':'replacement','usage':{'total_tokens':987}}
        c.execute('INSERT INTO calls(run_id,question_id,ordinal,body) VALUES(?,?,?,?) '
                  'ON CONFLICT(run_id,question_id,ordinal) DO UPDATE SET body=excluded.body',
                  (run_id,qid,1,dumps(call)))
    assert report(run_id,False)=={k:v for k,v in report(run_id,True).items() if k not in ('results','calls')}
    assert not report(run_id,False)['eligible']
    with db() as c:
        c.execute('DELETE FROM results WHERE run_id=? AND question_id=?',(run_id,qid))
        c.execute('DELETE FROM calls WHERE run_id=? AND question_id=? AND ordinal=1',(run_id,qid))
    assert report(run_id,False)=={k:v for k,v in report(run_id,True).items() if k not in ('results','calls')}


def test_legacy_backfill_and_batch_list_parity(client):
    bid=uuid4().hex;ids=[uuid4().hex for _ in range(3)]
    with db() as c:
        plan={'official':True,'mode':'self_refine','calls':3}
        c.execute('INSERT INTO evaluation_batches(id,request_key,name,created_at,body,started) VALUES(?,?,?,?,?,1)',
                  (bid,uuid4().hex,'批次','2026-10-23T00:00:00+00:00',
                   dumps({'request':{},'plan':plan})))
        for i,rid in enumerate(ids):
            body=make_run(c,rid,bid)
            c.execute('INSERT INTO batch_entries VALUES(?,?,?,?)',(bid,'team-'+str(i),rid,rid))
            seed_records(c,rid,body) # deliberately omit run_monitor, simulating an old database
    for rid in ids:
        assert report(rid,False)=={k:v for k,v in report(rid,True).items() if k not in ('results','calls')}
    batch=competition.get_batch(bid);listing=competition.list_batches()
    assert len(listing)==1
    assert listing[0]=={k:v for k,v in batch.items() if k!='runs'}
    with db() as c:
        qid='L2_main-0'
        data=json.loads(c.execute('SELECT body FROM results WHERE run_id=? AND question_id=?',(ids[0],qid)).fetchone()[0])
        data.update(status='infrastructure_error',correct=False)
        c.execute('UPDATE results SET body=? WHERE run_id=? AND question_id=?',(dumps(data),ids[0],qid))
    assert competition.list_batches()[0]['error_count']==competition.get_batch(bid)['error_count']
