"""Server-side state of the four official steps, per-team error breakdown, import notices."""
import json
import socket
from uuid import uuid4

import pytest

from app.db import db, dumps
from app.services import competition, submissions
from app.services.monitor import ensure

DIMS = ('L2_main', 'L3_main', 'L1_sub', 'L2_sub')
_clock = iter(range(1, 1000))


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('93.184.216.34', 443))])


def row(team, version):
    values = submissions.template_rows(False)[0]
    values[0] = team; values[1] = '队伍 ' + team; values[2] = version
    values[4] = 'fixture-only-not-a-real-key'; values[5] = ''
    return values


def csv_import(client, data):
    content = submissions.csv_bytes(submissions.FIELDS, [data])
    preview = client.post('/api/submissions/batch/preview', files={'file': ('entry.csv', content)})
    assert preview.json()['errors'] == 0, preview.json()
    result = client.post('/api/submissions/batch/import', json={'preview_id': preview.json()['preview_id']})
    assert result.status_code == 200, result.text
    return result.json()


def status(client):
    response = client.get('/api/competition/status')
    assert response.status_code == 200, response.text
    return response.json()


def step(data, key):
    return next(s for s in data['steps'] if s['key'] == key)


def seed_batch(run_states, errors=None):
    """Insert a started official batch whose runs have the given statuses (no model calls)."""
    batch_id = uuid4().hex
    created = f'2026-10-23T00:{next(_clock):02d}:00+00:00'  # later batches sort first
    questions = [dict(question_id=f'{d}-{i}', level=d.split('_')[0], role=d.split('_')[1], gt_answer=['A'])
                 for d in DIMS for i in range(2)]
    with db() as c:
        c.execute('INSERT INTO evaluation_batches(id,request_key,name,created_at,body,started) VALUES(?,?,?,?,?,1)',
                  (batch_id, uuid4().hex, '批次', created,
                   dumps({'request': {}, 'plan': {'official': True}})))
        run_ids = []
        for i, state in enumerate(run_states):
            rid = uuid4().hex; run_ids.append(rid)
            body = dict(name=f'队伍{i}', kind='official', model=dict(name='M', model_id='M1', provider='openai', is_aggregator=False),
                        mode='self_refine', calls=3, early_stop=False, prompt={'version': 'p'},
                        config={'competition': {'max_calls_per_question': 3, 'timeout_per_question_seconds': 120}},
                        cohort='c', dataset_sha256='d', started_at='2026-10-23T00:00:00+00:00',
                        finished_at='2026-10-23T00:00:09+00:00', official=True, questions=questions,
                        batch_id=batch_id, team_id=f't{i}', submission_version='v1')
            c.execute('INSERT INTO runs(id,status,created_at,body) VALUES(?,?,?,?)', (rid, state, '2026-10-23T00:00:00+00:00', dumps(body)))
            ensure(c, rid, body)
            c.execute('INSERT INTO batch_entries VALUES(?,?,?,?)', (batch_id, f't{i}', f's{i}', rid))
            for q in questions if state == 'completed' or errors else []:
                kind = (errors or {}).get(q['question_id'], 'ok')
                c.execute('INSERT INTO results VALUES(?,?,?)', (rid, q['question_id'],
                          dumps(dict(question_id=q['question_id'], dimension=q['level'] + '_' + q['role'],
                                     status=kind, correct=kind == 'ok', answer=['A'], calls=1))))
    return batch_id, run_ids


def test_empty_system_points_to_dataset_first(client):
    data = status(client)
    assert [s['key'] for s in data['steps']] == ['dataset', 'submissions', 'batches', 'analytics']
    assert step(data, 'dataset')['state'] == 'blocked' and '尚未导入题库' in step(data, 'dataset')['missing']
    assert step(data, 'submissions')['state'] == 'todo'
    assert step(data, 'batches')['state'] == 'blocked'
    assert step(data, 'analytics')['state'] == 'todo'
    assert data['current_batch_id'] is None and data['locked'] is False
    assert data['suggested_tab'] == 'dataset'


def test_submissions_step_flags_outdated_empty_and_missing_credentials(client):
    csv_import(client, row('team', 'v1'))
    v1 = client.get('/api/submissions/official-roster').json()['submission_ids'][0]
    csv_import(client, row('team', 'v2'))
    # Manual choice of the older version: usable but flagged.
    client.put('/api/submissions/official-roster', json={'submission_ids': [v1]})
    s = step(status(client), 'submissions')
    assert s['state'] == 'warning' and any('有更新版本未选中' in m and 'v1→v2' in m for m in s['missing'])
    # Newest version: everything is fine, and the other-team note is informational only.
    client.put('/api/submissions/official-roster', json={'submission_ids': []})
    s = step(status(client), 'submissions')
    assert s['state'] == 'blocked' and s['missing'] == ['名单为空，请至少选择一支队伍']
    v2 = next(x['id'] for x in client.get('/api/submissions').json() if x['is_latest'])
    client.put('/api/submissions/official-roster', json={'submission_ids': [v2]})
    s = step(status(client), 'submissions')
    assert s['state'] == 'done' and s['missing'] == []
    with db() as c:
        c.execute('UPDATE models SET secret=NULL')
    s = step(status(client), 'submissions')
    assert s['state'] == 'blocked' and any(m.startswith('未提供凭据') and '队伍 team' in m for m in s['missing'])


def test_unselected_team_is_a_note_not_a_warning(client):
    csv_import(client, row('a', 'v1')); csv_import(client, row('b', 'v1'))
    ids = {x['team_id']: x['id'] for x in client.get('/api/submissions').json()}
    client.put('/api/submissions/official-roster', json={'submission_ids': [ids['a']]})
    s = step(status(client), 'submissions')
    assert s['state'] == 'done' and s['missing'] == []
    assert any(c['level'] == 'info' and '队伍 b' in c['text'] for c in s['checks'])


def test_launch_and_monitor_follow_the_current_batch(client):
    batch_id, _ = seed_batch(['running', 'queued'])
    data = status(client)
    assert data['current_batch_id'] == batch_id and data['locked'] is True
    assert step(data, 'batches')['state'] == 'running'          # running wins over unready earlier steps
    assert step(data, 'analytics')['state'] == 'running' and data['suggested_tab'] == 'analytics'

    batch_id, _ = seed_batch(['completed', 'interrupted'])
    data = status(client)
    a = step(data, 'analytics')
    assert data['locked'] is False and a['state'] == 'warning' and a['missing'] == ['队伍1：已中断']
    assert data['suggested_tab'] == 'analytics'

    seed_batch(['completed', 'completed'])
    data = status(client)
    assert step(data, 'analytics')['state'] == 'done' and step(data, 'analytics')['message'] == '本轮已完成：2 支队伍'


def test_error_breakdown_counts_each_kind_without_changing_run_fields(client):
    errors = {'L2_main-0': 'api_error', 'L2_main-1': 'timeout', 'L3_main-0': 'timeout',
              'L1_sub-0': 'invalid_output', 'L2_sub-0': 'infrastructure_error'}
    batch_id, (rid,) = seed_batch(['completed'], errors)
    run = competition.get_batch(batch_id)['runs'][0]
    assert run['error_breakdown'] == {'api_error': 1, 'timeout': 2, 'invalid_output': 1, 'infrastructure_error': 1}
    assert run['error_count'] == 5 and run['infrastructure_errors'] == 1
    listing = competition.list_batches()[0]
    assert listing == {k: v for k, v in competition.get_batch(batch_id).items() if k != 'runs'}


def test_import_notice_distinguishes_switch_from_new_team(client):
    first = csv_import(client, row('team', 'v1'))['selection_updates'][0]
    assert first['previous_version'] is None
    second = csv_import(client, row('team', 'v2'))['selection_updates'][0]
    assert second['previous_version'] == 'v1' and second['submission_version'] == 'v2'


def test_dataset_stats_endpoint_keeps_its_shape(client):
    data = client.get('/api/dataset/stats').json()
    assert set(data) == {'total', 'counts', 'expected', 'real_image_questions', 'missing_image_questions',
                         'unique_frames', 'mapped_frames'}
