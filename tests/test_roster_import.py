"""Consistent JSON/CSV version selection and cross-browser organizer persistence."""
import csv
import io
import json
import socket
from app.services import submissions
from app.db import db


def row(team, version):
    values=submissions.template_rows(False)[0]
    values[0]=team;values[1]='队伍 '+team;values[2]=version
    values[4]='fixture-only-not-a-real-key';values[5]=''
    return values


def csv_import(client, data):
    content=submissions.csv_bytes(submissions.FIELDS,[data])
    preview=client.post('/api/submissions/batch/preview',files={'file':('entry.csv',content)})
    assert preview.status_code==200,preview.text
    assert preview.json()['errors']==0,preview.json()
    result=client.post('/api/submissions/batch/import',json={'preview_id':preview.json()['preview_id']})
    assert result.status_code==200,result.text
    return result.json()


def json_import(client, data):
    content={'team_id':data[0],'team_name':data[1],'submission_version':data[2],
        'api':{'base_url':data[3],'api_key':data[4],'model_id':data[6],
               'is_aggregator':False,'provider':'openai'},
        'prompts':{'system':data[8],'initial':data[9],'review':data[10]},
        'inference':{'temperature':0,'max_tokens':1024}}
    result=client.post('/api/submissions/json/import',
        files=[('files',('contestant.json',json.dumps(content,ensure_ascii=False).encode(),'application/json'))])
    assert result.status_code==200,result.text
    assert result.json()['ok'],result.text
    return result.json()


def test_json_and_csv_imports_switch_to_new_version_without_losing_manual_choice(client,monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(2,1,6,'',('93.184.216.34',443))])
    first=csv_import(client,row('team','v1'))
    v1=first['submission_ids'][0]
    roster=client.get('/api/submissions/official-roster').json()
    assert roster['submission_ids']==[v1] and roster['configured']
    second=json_import(client,row('team','v2'))
    v2=second['submission_ids'][0]
    assert second['selection_updates'][0]['submission_version']=='v2'
    assert client.get('/api/submissions/official-roster').json()['submission_ids']==[v2]
    selection=client.put('/api/submissions/official-roster',json={'submission_ids':[v1]})
    assert selection.status_code==200
    assert client.get('/api/submissions/official-roster').json()['submission_ids']==[v1]
    third=csv_import(client,row('team','v3'))
    v3=third['submission_ids'][0]
    assert client.get('/api/submissions/official-roster').json()['submission_ids']==[v3]
    submissions_list=client.get('/api/submissions').json()
    assert submissions_list[0]['is_latest'] and submissions_list[0]['latest_version']=='v3'
    assert not submissions_list[1]['is_latest']
    assert all('api_key' not in entry for entry in submissions_list)
    # Re-import the same version: a no-op, never reset the selected roster.
    assert csv_import(client,row('team','v3'))['duplicates']==1
    assert client.get('/api/submissions/official-roster').json()['submission_ids']==[v3]


def test_persisted_roster_rejects_duplicate_team_versions_and_mock(client,monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(2,1,6,'',('93.184.216.34',443))])
    v1=csv_import(client,row('team','v1'))['submission_ids'][0]
    v2=csv_import(client,row('team','v2'))['submission_ids'][0]
    assert client.put('/api/submissions/official-roster',json={'submission_ids':[v1,v2]}).status_code==400
    assert client.put('/api/submissions/official-roster',json={'submission_ids':['missing']}).status_code==400
    assert client.get('/api/submissions/official-roster').json()['submission_ids']==[v2]
