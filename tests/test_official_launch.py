import asyncio
import socket
import pytest
from app.db import db,question_rows
from app.services import competition,submissions,runs
from test_competition import import_five,batch_payload,wait_batch
from test_system import png


@pytest.fixture
def formal(imported,monkeypatch):
    c=imported
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    rows=submissions.template_rows(False)[:2]
    for row in rows:row[4]='fixture-not-a-real-key';row[5]=''
    ids=import_five(c,rows=rows)
    image=c.post('/api/images',files={'file':('fixture.png',png('gold'))},data={'real':'true'}).json()
    qs=question_rows()
    mappings=[dict(replay_id=r,image_step=s,image_id=image['id']) for r,s in {(q['replay_id'],q['image_step']) for q in qs}]
    assert c.post('/api/images/map',json=mappings).status_code==200
    payload=batch_payload(ids,official=True,question_ids=[])
    class TransportStub:
        async def complete(self,*args):return {'output':'A','usage':{}}
    monkeypatch.setattr(runs,'make_client',lambda *a:TransportStub())
    return c,payload


def confirmed(c,p):
    r=c.post('/api/evaluation-batches/preview',json=p);assert r.status_code==200,r.text
    plan=r.json()
    assert plan['questions_per_team']==1279 and plan['real_images_complete']
    return dict(p,confirm_official=True,preview_fingerprint=plan['fingerprint'])


def test_start_new_full_formal_pipeline_and_retry(formal):
    c,p=formal;body=confirmed(c,p)
    assert c.get('/api/evaluation-batches').json()==[]
    # Cancel / absent confirmation has no side effects.
    assert c.post('/api/evaluation-batches/start-new',json=dict(body,confirm_official=False)).status_code==400
    assert c.get('/api/evaluation-batches').json()==[]
    r=c.post('/api/evaluation-batches/start-new',json=body);assert r.status_code==200,r.text
    bid=r.json()['batch_id']
    again=c.post('/api/evaluation-batches/start-new',json=body)
    assert again.status_code==200 and again.json()['batch_id']==bid and not again.json()['started']
    b=wait_batch(c,bid)
    assert b['official'] and b['finished']==2 and b['completed']==2558 and b['actual_calls']==2558
    assert all(r['eligible'] for r in b['runs'])
    assert len(c.get('/api/leaderboard').json())==2
    a=c.get('/api/evaluation-batches/'+bid+'/analytics').json()
    assert len(a['groups'])==1 and a['groups'][0]['kind']=='official'
    for fmt in ('html','json','csv','xlsx'):
        assert c.get('/api/evaluation-batches/'+bid+'/export',params={'format':fmt}).status_code==200
    group=a['groups'][0]['id']
    for chart in ('ranking','dimensions','radar','efficiency'):
        assert c.get('/api/evaluation-batches/'+bid+'/charts/'+chart+'.svg',params={'group':group}).status_code==200
    assert len(c.get('/api/evaluation-batches').json())==1


def test_dispatch_failure_rolls_back_and_same_key_resumes(formal,monkeypatch):
    c,p=formal;body=confirmed(c,p);queued=[]
    def unavailable(ids):raise RuntimeError('queue unavailable')
    monkeypatch.setattr(competition.runner,'enqueue_many',unavailable)
    r=c.post('/api/evaluation-batches/start-new',json=body)
    assert r.status_code==503 and r.json()['retryable']
    bid=r.json()['batch_id'];b=c.get('/api/evaluation-batches/'+bid).json()
    assert not b['started'] and b['actual_calls']==0 and all(r['status']=='draft' for r in b['runs'])
    monkeypatch.setattr(competition.runner,'enqueue_many',lambda ids:queued.extend(ids))
    assert c.post('/api/evaluation-batches/start-new',json=body).status_code==200
    assert len(queued)==2
    assert not c.post('/api/evaluation-batches/start-new',json=body).json()['started']
    assert len(queued)==2 and len(c.get('/api/evaluation-batches').json())==1


def test_concurrent_confirmations_enqueue_only_once(formal,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    c,p=formal;body=confirmed(c,p);queued=[]
    monkeypatch.setattr(competition.runner,'enqueue_many',lambda ids:queued.extend(ids))
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses=list(pool.map(lambda _:c.post('/api/evaluation-batches/start-new',json=body),range(3)))
    assert all(r.status_code==200 for r in responses)
    assert len({r.json()['batch_id'] for r in responses})==1
    assert sum(r.json()['started'] for r in responses)==1
    assert len(queued)==2 and len(c.get('/api/evaluation-batches').json())==1


def test_confirmation_fingerprint_and_dynamic_cap(formal,monkeypatch):
    c,p=formal;body=confirmed(c,p)
    # A changed organizer budget must invalidate the confirmation before creating anything.
    assert c.put('/api/settings',json={'max_calls_per_question':5,'timeout_per_question_seconds':60}).status_code==200
    assert c.post('/api/evaluation-batches/start-new',json=body).status_code==400
    assert c.get('/api/evaluation-batches').json()==[]
    for mode in ('self_refine','majority_vote'):
        plan=c.post('/api/evaluation-batches/preview',json=dict(p,mode=mode,calls=5))
        assert plan.status_code==200,plan.text
        assert plan.json()['max_requests']==2*1279*5
        assert c.post('/api/evaluation-batches/preview',json=dict(p,mode=mode,calls=6)).status_code==400
    assert c.post('/api/evaluation-batches/preview',json=dict(p,mode='single_pass',calls=5)).status_code==400


@pytest.mark.parametrize('patch',[{'official':False},{'limit':1},{'dimension':'L2_main'},
                                  {'question_ids':['G01-01-CORE']}])
def test_all_creation_endpoints_enforce_official_scope(formal,patch):
    c,p=formal
    for endpoint in ('/api/evaluation-batches/preview','/api/evaluation-batches','/api/evaluation-batches/start-new'):
        payload=dict(p,**patch)
        if endpoint.endswith('start-new'):payload.update(confirm_official=True,preview_fingerprint='0'*64)
        assert c.post(endpoint,json=payload).status_code==400
    assert c.get('/api/evaluation-batches').json()==[]

