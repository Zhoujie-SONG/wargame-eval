import asyncio
import copy
import io
import json
import socket
import time
import zipfile
from pathlib import Path
import httpx
import pytest
from PIL import Image
from app.core.answer_parser import parse_answer
from app.core.inference import infer
from app.core.scorer import score
from app.core.model_client import OpenAIClient
from app.core.security import validate_url, SafeNetworkBackend
from app.data.importer import parse_markdown
from app.settings import ROOT


def wait(client,run_id):
    for _ in range(1000):
        r = client.get('/api/runs/'+run_id).json()
        if r['status'] not in ('queued','running','stopping'):
            return r
        time.sleep(.01)
    raise AssertionError('run did not finish')


def launch(client,**kwargs):
    payload=dict(name='测试',model_id='mock-demo',prompt_id='default',limit=5,mode='single_pass',calls=3)
    payload.update(kwargs)
    r=client.post('/api/runs',json=payload)
    assert r.status_code==200,r.text
    return wait(client,r.json()['run_id'])


def png(color='navy'):
    stream=io.BytesIO()
    Image.new('RGB',(20,20),color).save(stream,format='PNG')
    return stream.getvalue()


def test_dataset_exact_and_preserved(imported):
    c=imported
    stats=c.get('/api/dataset/stats').json()
    assert stats['total']==1279
    assert stats['counts']=={'L2_main':179,'L3_main':61,'L1_sub':757,'L2_sub':282}
    assert stats['real_image_questions']==0
    assert c.post('/api/dataset/import-bundled').json()['total']==1279
    with zipfile.ZipFile(ROOT/'selected_240_six_groups.zip') as z:
        for filename in z.namelist():
            if filename.endswith('.md'):
                text=z.read(filename).decode('utf-8-sig')
                for q in parse_markdown(text,filename):
                    assert '```text\n'+q['input_text']+'\n```' in text
                    assert '**GT' not in q['input_text']
                    stored=c.get('/api/questions/'+q['question_id']).json()
                    assert stored['input_text']==q['input_text']
                    assert stored['gt_answer']==q['gt_answer']
    query=c.get('/api/questions?level=L3&role=main&question_id=G01').json()
    assert query['total']==11


def test_import_transaction_failure(imported):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:z.writestr('bad.md','# empty')
    r=imported.post('/api/dataset/import',files={'file':('bad.zip',stream.getvalue())})
    assert r.status_code==400
    assert imported.get('/api/dataset/stats').json()['total']==1279


def test_images_reuse_and_official_gates(imported):
    c=imported
    image=c.post('/api/images',files={'file':('x.png',png())},data={'real':'true'}).json()
    q=c.get('/api/questions/G01-01-CORE').json()
    r=c.post('/api/images/map',json=[{'replay_id':q['replay_id'],'image_step':q['image_step'],'image_id':image['id']}])
    assert r.status_code==200
    assert c.get('/api/questions/G01-01-P1').json()['image']['id']==image['id']
    assert c.get('/api/dataset/stats').json()['real_image_questions']>1
    assert c.post('/api/images',files={'file':('x.png',png())},data={'real':'true'}).json()['id']==image['id']
    assert c.post('/api/images',files={'file':('x.png',png())},data={'real':'false'}).status_code==400
    assert c.post('/api/images',files={'file':('x.svg',b'<script>bad</script>')},data={'real':'true'}).status_code==400
    assert c.post('/api/runs',json=dict(name='illegal',model_id='mock-demo',prompt_id='default',official=True)).status_code==400
    assert 'image_step' in c.get('/api/images/missing.csv').text


@pytest.mark.parametrize('mode,calls',[('single_pass',1),('self_refine',3),('majority_vote',3)])
def test_modes_e2e_and_exports(imported,mode,calls):
    c=imported
    r=launch(c,mode=mode)
    assert r['status']=='completed'
    assert r['completed']==5
    assert r['actual_calls']==5*calls
    assert r['external_api_calls']==0 and r['mock_requests']==5*calls
    assert not r['eligible'] and r['kind']=='mock'
    assert r['token_usage']['total_tokens']==5*calls*101
    assert not c.get('/api/leaderboard').json()
    assert len(c.get('/api/leaderboard?kind=mock').json())==1
    for format in ('json','csv','html'):
        output=c.get(f'/api/runs/{r["run_id"]}/export/{format}')
        assert output.status_code==200
        assert 'api_key' not in output.text and 'system' not in output.text and 'review' not in output.text


def test_full_mock_all_dimensions(imported):
    r=launch(imported,limit=None)
    assert r['status']=='completed' and r['completed']==1279
    d=r['dimensions']
    assert all(v['accuracy'] is not None for v in d.values())
    assert r['weighted_score']==pytest.approx(100*(.35*d['L2_main']['accuracy']+.35*d['L3_main']['accuracy']+.15*d['L1_sub']['accuracy']+.15*d['L2_sub']['accuracy']))
    assert not imported.get('/api/leaderboard').json()


def test_prompt_budget_settings_and_freeze(imported):
    c=imported
    p=c.post('/api/prompts',json={'name':'版本一','system':'system-unique','initial':'initial-unique','review':'review-unique','default_mode':'self_refine','default_calls':2}).json()
    assert p['version']
    assert c.post('/api/runs',json=dict(name='x',model_id='mock-demo',prompt_id=p['id'],calls=4)).status_code==400
    assert c.put('/api/settings',json={'max_calls_per_question':2,'timeout_per_question_seconds':5}).status_code==200
    assert c.post('/api/runs',json=dict(name='x',model_id='mock-demo',prompt_id=p['id'],calls=3)).status_code==400
    r=launch(c,prompt_id=p['id'],mode='majority_vote',calls=2)
    assert r['actual_calls']==10 and r['budget']['max_calls_per_question']==2
    c.put('/api/settings',json={'max_calls_per_question':4,'timeout_per_question_seconds':10})
    r2=launch(c,mode='majority_vote',calls=4)
    assert r2['actual_calls']==20
    frozen=c.get('/api/runs/'+r['run_id']).json()
    assert frozen['budget']['max_calls_per_question']==2
    assert frozen['prompt_version']==p['version']


class FakeClient:
    def __init__(self,outputs):self.outputs=iter(outputs);self.messages=[]
    async def complete(self,messages,timeout):
        self.messages.append(copy.deepcopy(messages))
        v=next(self.outputs)
        if isinstance(v,Exception):raise v
        return {'output':v,'usage':{}}


PUBLIC={'input_text':'Question A. Alpha B. Beta','options':['A','B','C','D'],'question_type':'single_choice','image':None}


def execute(fake,mode='majority_vote',calls=3,cap=3,timeout=2,stop=lambda:False,early=False):
    records=[]
    value=asyncio.run(infer(PUBLIC,{'system':'S','initial':'I','review':'R'},fake,mode,calls,cap,timeout,lambda r:records.append(copy.deepcopy(r)),stop,early))
    return value,records


def test_vote_tie_first_legal_no_extra_request():
    f=FakeClient(['invalid','B','A']);r,_=execute(f)
    assert r['answer']==['B'] and len(f.messages)==3
    assert f.messages[0]==f.messages[1]==f.messages[2]
    f=FakeClient(['A','B','B']);assert execute(f)[0]['answer']==['B']
    with pytest.raises(ValueError):execute(FakeClient(['A']),calls=4)


def test_self_refine_isolation_and_early_stop():
    f=FakeClient(['A','A','B']);r,_=execute(f,mode='self_refine',early=True)
    assert r['calls']==2 and r['answer']==['A']
    assert len(f.messages[1])==len(f.messages[0])+2
    f2=FakeClient(['B']);execute(f2,mode='single_pass')
    assert f2.messages[0]==f.messages[0]


def test_error_requests_consume_budget_timeout_counts_wrong():
    f=FakeClient([httpx.ConnectError('SECRET'),httpx.ConnectError('SECRET'),'A'])
    r,records=execute(f)
    assert r['calls']==3 and len(f.messages)==3
    assert 'SECRET' not in str(records)
    r,_=execute(FakeClient(['A',httpx.ReadTimeout('late'),'A']))
    assert r['status']=='timeout' and r['answer'] is None and r['calls']==2
    class Slow:
        async def complete(self,messages,timeout):await asyncio.sleep(1)
    r,_=execute(Slow(),mode='single_pass',timeout=.01)
    assert r['status']=='timeout'
    req=httpx.Request('POST','https://example.com/v1/chat/completions')
    err=httpx.HTTPStatusError('secret body',request=req,response=httpx.Response(429,request=req))
    # HTTP 429 that never clears is an ordinary API error after the retry delays are used up.
    import app.core.inference as inference
    inference.BACKOFF_SECONDS=(0,0)
    class AlwaysLimited:
        async def complete(self,messages,timeout):raise err
    try:
        r,records=execute(AlwaysLimited())
    finally:
        inference.BACKOFF_SECONDS=(1,2,4,8,16,30,30,30)
    assert r['calls']==3 and r['status']=='api_error'
    assert records[-1]['http_status']==429 and 'secret body' not in str(records)


def test_rate_limit_waits_inside_the_same_call_slot(monkeypatch):
    import app.core.inference as inference
    monkeypatch.setattr(inference,'BACKOFF_SECONDS',(0,0,0))
    req=httpx.Request('POST','https://example.com/v1/chat/completions')
    limited=httpx.HTTPStatusError('x',request=req,response=httpx.Response(429,request=req))
    f=FakeClient([limited,limited,'A'])
    r,records=execute(f,mode='single_pass',calls=1)
    assert r['status']=='ok' and r['answer']==['A'] and r['calls']==1     # one call slot, not three
    assert records[-1]['rate_limit_retries']==2 and records[-1]['status']=='ok'
    assert len(f.messages)==3
    # A retry-after header is honoured but capped; a non-429 error is never retried.
    slow=httpx.HTTPStatusError('x',request=req,response=httpx.Response(503,request=req))
    r,_=execute(FakeClient([slow]),mode='single_pass',calls=1)
    assert r['status']=='api_error'


@pytest.mark.parametrize('raw,kind,expected',[
    ('C','single_choice',['C']),('{"answer":"B"}','single_choice',['B']),
    ('```json\n{"answer":["D","A","C"]}\n```','multiple_choice',['A','C','D']),
    ('A、C、D','multiple_choice',['A','C','D']),('ACD','multiple_choice',['A','C','D']),
    ('A B','single_choice',None),('The answer is A','single_choice',None),
    ('{"answer":["AA"]}','multiple_choice',None),('不可读','single_choice',None),('AA','multiple_choice',None)])
def test_parser(raw,kind,expected):assert parse_answer(raw,kind)==expected


def test_scoring_strict_sets():
    questions=[{'question_id':'a','level':'L2','role':'main'},{'question_id':'b','level':'L1','role':'sub'}]
    s=score(questions,{'a':{'correct':True},'b':{'correct':False}})
    assert s['weighted_score']==35
    assert s['dimensions']['L3_main']['accuracy'] is None


@pytest.mark.parametrize('url',['http://example.com','https://127.0.0.1/v1','https://169.254.169.254/v1','https://[::1]/','https://user:pass@example.com','https://example.com:22','https://example.com?key=secret'])
def test_ssrf_invalid(url):
    with pytest.raises(ValueError):validate_url(url)


def test_dns_rebinding_and_pinning(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('10.0.0.1',443))])
    with pytest.raises(ValueError):validate_url('https://example.com/v1')
    async def rebinding():
        loop=asyncio.get_running_loop()
        async def resolve(*a,**k):return [(2,1,6,'',('127.0.0.1',443))]
        monkeypatch.setattr(loop,'getaddrinfo',resolve)
        with pytest.raises(ValueError):await SafeNetworkBackend().connect_tcp('example.com',443)
    asyncio.run(rebinding())
    async def pinned():
        loop=asyncio.get_running_loop()
        async def resolve(*a,**k):return [(2,1,6,'',('93.184.216.34',443))]
        monkeypatch.setattr(loop,'getaddrinfo',resolve)
        from httpcore._backends.auto import AutoBackend
        received=[]
        async def connect(self,host,port,*args):
            received.append((host,port));return 'stream'
        monkeypatch.setattr(AutoBackend,'connect_tcp',connect)
        assert await SafeNetworkBackend().connect_tcp('example.com',443)=='stream'
        assert received==[('93.184.216.34',443)]
    asyncio.run(pinned())


def test_credentials_crud_and_redaction(client,monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    secret='sk-secret-do-not-echo'
    payload=dict(name='remote',provider='openai',base_url='https://example.com/v1',model_id='model',api_key=secret,is_aggregator=True)
    r=client.post('/api/models',json=payload);assert r.status_code==200
    mid=r.json()['id']
    assert secret not in client.get('/api/models').text
    from app.db import db
    with db() as c:
        saved=c.execute('SELECT secret FROM models WHERE id=?',(mid,)).fetchone()[0]
    assert secret not in saved
    payload['api_key']='';payload['name']='edited'
    assert client.put('/api/models/'+mid,json=payload).status_code==200
    payload['api_key']=secret;payload['temperature']=99
    assert secret not in client.post('/api/models',json=payload).text
    import app.main as main
    monkeypatch.setattr(main,'make_client',lambda *a:FakeClient([httpx.ConnectError('secret')]))
    assert client.post('/api/models/'+mid+'/test').status_code==400
    assert client.delete('/api/models/'+mid).status_code==200


def test_openai_transport_multimodal_errors_and_usage():
    seen=[]
    def handler(request):
        seen.append(json.loads(request.content))
        assert request.headers['authorization']=='Bearer secret'
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"answer":"C"} secret'}}],'usage':{'total_tokens':10,'evil':'secret'}})
    client=OpenAIClient({'provider':'openai','base_url':'https://example.com/v1','model_id':'x','temperature':.5,'max_tokens':100},'secret',httpx.MockTransport(handler))
    messages=[{'role':'system','content':'S'},{'role':'user','content':[{'type':'text','text':'Q'},{'type':'image_url','image_url':{'url':'data:image/png;base64,AA=='}}]}]
    value=asyncio.run(client.complete(messages,1))
    assert seen[0]['messages']==messages and len(seen)==1
    assert 'secret' not in str(value) and value['usage']=={'total_tokens':10}
    redirected=[]
    def redirect(request):
        redirected.append(str(request.url))
        return httpx.Response(302,headers={'Location':'https://127.0.0.1/secret'})
    client=OpenAIClient({'provider':'openai','base_url':'https://example.com/v1','model_id':'x'},'secret',httpx.MockTransport(redirect))
    with pytest.raises(httpx.HTTPStatusError):asyncio.run(client.complete(messages,1))
    assert len(redirected)==1


def test_real_model_missing_images_and_no_gt_payload(imported,monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    c=imported
    mid=c.post('/api/models',json=dict(name='remote',provider='openai',base_url='https://example.com/v1',model_id='x',api_key='s',is_aggregator=False)).json()['id']
    payload=dict(name='real',model_id=mid,prompt_id='default',calls=1,limit=1)
    assert c.post('/api/runs',json=payload).status_code==400
    q=c.get('/api/questions/G01-01-CORE').json()
    image=c.post('/api/images',files={'file':('real.png',png('green'))},data={'real':'true'}).json()
    c.post('/api/images/map',json=[dict(replay_id=q['replay_id'],image_step=q['image_step'],image_id=image['id'])])
    fake=FakeClient(['C'])
    import app.services.runs as runs
    monkeypatch.setattr(runs,'make_client',lambda *a:fake)
    r=launch(c,model_id=mid,limit=1,calls=1,question_ids=['G01-01-CORE'])
    assert r['status']=='completed' and r['kind']=='partial_test' and r['results'][0]['correct']
    text=json.dumps(fake.messages,ensure_ascii=False)
    assert '**GT' not in text and 'reference_text' not in text and '参考结论' not in text
    assert fake.messages[0][-1]['content'][0]['text']==q['input_text']
    assert fake.messages[0][-1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert c.post('/api/runs',json=dict(payload,official=True)).status_code==400


def test_safe_stop_recovery_and_infrastructure_rerun(imported,monkeypatch):
    c=imported
    import app.services.runs as runs
    class Slow:
        def __init__(self):self.n=0
        async def complete(self,messages,timeout):
            self.n+=1;await asyncio.sleep(.15)
            return {'output':'A','usage':{}}
    fake=Slow();monkeypatch.setattr(runs,'make_client',lambda *a:fake)
    payload=dict(name='stop',model_id='mock-demo',prompt_id='default',calls=3,mode='self_refine',limit=20)
    rid=c.post('/api/runs',json=payload).json()['run_id']
    time.sleep(.03)
    assert c.post('/api/runs/'+rid+'/stop').status_code==200
    r=wait(c,rid)
    assert r['status']=='stopped' and r['actual_calls']<=1
    assert c.post('/api/runs/'+rid+'/rerun').status_code==400
    # Simulate a persisted worker crash, including an ambiguously sent request.
    from app.db import db,init_db
    with db() as con:con.execute("UPDATE runs SET status='running' WHERE id=?",(rid,))
    init_db()
    assert c.get('/api/runs/'+rid).json()['status']=='interrupted'
    monkeypatch.setattr(runs,'make_client',lambda *a:FakeClient(['A']*60))
    new=c.post('/api/runs/'+rid+'/rerun').json()['run_id']
    assert wait(c,new)['status']=='completed'
    assert c.get('/api/runs/'+rid).json()['status']=='superseded'
    qid=c.get('/api/runs/'+new).json()['results'][0]['question_id']
    assert c.post('/api/runs/'+new+'/infrastructure',json={'question_ids':[qid],'reason':'主办方截图文件读取故障'}).status_code==200
    assert c.get('/api/runs/'+new).json()['infrastructure_errors']==1


def test_web_security(client):
    r=client.get('/')
    assert r.status_code==200 and '任务监控' in r.text
    assert client.get('/static/app.js').status_code==200
    assert client.get('/docs').status_code==200
    assert '/api/runs' in client.get('/openapi.json').text
    assert 'frame-ancestors' in r.headers['content-security-policy']
    assert client.post('/api/dataset/import-bundled',headers={'origin':'https://evil.example'}).status_code==403
    assert client.get('/api/health',headers={'host':'evil.example'}).status_code==400


def test_official_eligibility_cohorts_freeze_and_multichoice(imported,monkeypatch):
    """Use transport stubs and fixture images; these are isolated temporary test records."""
    c=imported
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    mid=c.post('/api/models',json=dict(name='test-fixture',provider='openai',base_url='https://example.com/v1',model_id='x',api_key='fixture-secret',is_aggregator=True)).json()['id']
    image=c.post('/api/images',files={'file':('fixture.png',png('gold'))},data={'real':'true'}).json()
    from app.db import question_rows,db,dumps
    all_q=question_rows()
    mappings=[dict(replay_id=r,image_step=s,image_id=image['id']) for r,s in {(q['replay_id'],q['image_step']) for q in all_q}]
    assert c.post('/api/images/map',json=mappings).status_code==200
    import app.services.runs as runs
    fake=FakeClient([','.join(q['gt_answer']) for q in all_q])
    monkeypatch.setattr(runs,'make_client',lambda *a:fake)
    r=launch(c,model_id=mid,official=True,limit=None,calls=1)
    assert r['eligible'] and r['weighted_score']==100 and r['completed']==1279
    assert r['external_api_calls']==1279
    assert all(x['correct'] for x in r['results'])
    assert c.get('/api/leaderboard').json()[0]['run_id']==r['run_id']
    # Editing/deleting submitted configs cannot change existing snapshots.
    c.delete('/api/models/'+mid)
    assert c.get('/api/runs/'+r['run_id']).json()['model']['is_aggregator']
    # Verify grouped ranks under distinct frozen rules without running paid calls.
    with db() as con:
        row=con.execute('SELECT * FROM runs WHERE id=?',(r['run_id'],)).fetchone()
        body=json.loads(row['body']);body['cohort']='different-rules'
        con.execute('INSERT INTO runs(id,status,created_at,body) VALUES(?,?,?,?)',('other','completed',row['created_at'],dumps(body)))
        con.execute('INSERT INTO results SELECT ?,question_id,body FROM results WHERE run_id=?',('other',r['run_id']))
    assert [x['rank'] for x in c.get('/api/leaderboard').json()]==[1,1]
    marked=c.post('/api/runs/'+r['run_id']+'/infrastructure',json={'question_ids':[all_q[0]['question_id']],'reason':'测试基础设施故障登记'})
    assert marked.status_code==200
    assert not c.get('/api/runs/'+r['run_id']).json()['eligible']
    assert len(c.get('/api/leaderboard').json())==1
