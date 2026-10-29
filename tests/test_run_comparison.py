import json
import xml.etree.ElementTree as ET

import pytest

from app.db import db, dumps
from app.services.monitor import ensure
from app.services.run_comparison import PUBLIC_FIELDS


DIMS = ('L2_main', 'L3_main', 'L1_sub', 'L2_sub')
WEIGHTS = dict(L2_main=.35, L3_main=.35, L1_sub=.15, L2_sub=.15)
NS = {'s': 'http://www.w3.org/2000/svg'}


@pytest.fixture
def seed(client):
    def create(identity, *, name='同名方案', correct=4, status='completed', missing=None,
               incomplete=False, infrastructure=False, batch=None, legacy=False,
               mode='single_pass', calls=1, early_stop=False, kind='partial_test',
               dataset='same-dataset', concurrency=1, budget=None, image='same-image',
               reverse=False, question_prefix='Q', version='v1', created='2026-10-27T16:20:00+00:00'):
        questions = []
        for index, dim in enumerate(DIMS):
            if dim == missing:
                continue
            level, role = dim.split('_')
            questions.append(dict(question_id=question_prefix + str(index), parent_id=None,
                                  level=level, role=role, question_type='single_choice', options=['A', 'B'],
                                  input_text='PRIVATE_INPUT', input_sha256='same-input',
                                  gt_answer=['A'], reference_text='PRIVATE_GT_REFERENCE',
                                  image={'id': image, 'real': True, 'mime': 'image/png'}))
        if reverse:
            questions.reverse()
        body = dict(name=name, kind=kind, official=kind == 'official',
                    model=dict(name='模型', model_id='fixture-model', provider='mock' if kind == 'mock' else 'openai',
                               api_key='PRIVATE_API_KEY', api_key_env='PRIVATE_KEY_ENV', is_aggregator=False),
                    prompt=dict(version='prompt-' + version, system='PRIVATE_PROMPT', initial='PRIVATE_INITIAL'),
                    mode=mode, calls=calls, early_stop=early_stop, concurrency=concurrency,
                    config={'competition': budget or {'max_calls_per_question': 3, 'timeout_per_question_seconds': 120},
                            'evaluation': {'weights': WEIGHTS}},
                    cohort='same-cohort', dataset_sha256=dataset, questions=questions,
                    started_at=created, finished_at='2026-10-28T00:00:00+00:00',
                    submission_version=version, batch_id=batch, team_id='team-' + identity)
        with db() as connection:
            if batch:
                connection.execute('INSERT OR IGNORE INTO evaluation_batches(id,request_key,name,created_at,body) '
                                   'VALUES(?,?,?,?,?)', (batch, batch, '批次 ' + batch, created, dumps({})))
            connection.execute('INSERT INTO runs(id,status,created_at,body,secret) VALUES(?,?,?,?,?)',
                               (identity, status, created, dumps(body), 'PRIVATE_ENCRYPTED_SECRET'))
            if not legacy:
                ensure(connection, identity, body)
            if batch:
                connection.execute('INSERT INTO batch_entries VALUES(?,?,?,?)',
                                   (batch, 'team-' + identity, 'submission-' + identity, identity))
            for index, question in enumerate(questions[:-1] if incomplete else questions):
                result = dict(question_id=question['question_id'], dimension=question['level'] + '_' + question['role'],
                              correct=index < correct, status='infrastructure_error' if infrastructure and index == 0 else 'ok',
                              answer=['A'], elapsed_seconds=.1, output='PRIVATE_OUTPUT')
                connection.execute('INSERT INTO results VALUES(?,?,?)', (identity, question['question_id'], dumps(result)))
        return identity
    return create


def selected(client, identities, path='/api/run-comparisons'):
    return client.get(path, params=[('run_id', identity) for identity in identities])


def database_rows():
    with db() as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: [tuple(row) for row in connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                for name in names}


def test_candidates_include_all_batches_and_standalone_beyond_200(client, seed):
    ids = [seed(f'candidate-{index:03}', batch='first' if index < 100 else ('second' if index < 200 else None))
           for index in range(205)]
    data = client.get('/api/run-comparisons/candidates').json()['candidates']
    assert len(data) == 205 and {item['run_id'] for item in data} == set(ids)
    assert {item['batch_name'] for item in data} == {'批次 first', '批次 second', '独立测评'}
    assert len({item['label'] for item in data}) == 205
    assert all(item['selectable'] and not item['reason'] for item in data)


def test_cross_batch_and_standalone_scores_ties_and_allowlist(client, seed):
    ids = [seed('same-one', batch='one', correct=4), seed('same-two', batch='two', correct=2),
           seed('same-three', correct=4, version='v2')]
    response = selected(client, ids)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['selection_count'] == 3 and data['warnings'] == []
    assert '不属于正式排名' in data['scope_note']
    assert [row['weighted_score'] for row in data['entries']] == [100, 100, 70]
    assert [row['rank'] for row in data['entries']] == [1, 1, 3]
    assert len({row['label'] for row in data['entries']}) == 3
    assert all('2026-10-28 08:00:00' in row['label'] for row in data['entries'])
    assert {row['batch_name'] for row in data['entries']} == {'批次 one', '批次 two', '独立测评'}
    expected = set(PUBLIC_FIELDS) | {'batch_name', 'model_id', 'dimensions', 'label', 'rank', 'color'}
    assert all(set(row) == expected for row in data['entries'])
    for row in data['entries']:
        assert set(row['dimensions']) == set(DIMS)
        assert all(set(value) == {'total', 'correct', 'completed', 'accuracy'} for value in row['dimensions'].values())


@pytest.mark.parametrize('options,phrase', [
    ({'status': 'running'}, '尚未'), ({'status': 'queued'}, '尚未'),
    ({'status': 'stopped'}, '尚未'), ({'status': 'infrastructure_error'}, '尚未'),
    ({'incomplete': True}, '结果尚未'), ({'infrastructure': True}, '主办方'),
])
def test_unfinished_and_infrastructure_results_are_not_selectable(client, seed, options, phrase):
    good, bad = seed('good'), seed('bad', **options)
    candidates = client.get('/api/run-comparisons/candidates').json()['candidates']
    item = next(row for row in candidates if row['run_id'] == bad)
    assert not item['selectable'] and phrase in item['reason']
    for path in ('/api/run-comparisons', '/api/run-comparisons/charts/radar.svg', '/api/run-comparisons/charts/ranking.svg'):
        response = selected(client, [good, bad], path)
        assert response.status_code == 400 and phrase in response.json()['detail']


@pytest.mark.parametrize('ids', [[], ['one'], ['one', 'one'], ['one', 'missing'],
                               ['one', '../secret'], ['one', ''], [f'r{i}' for i in range(9)]])
def test_invalid_selection_is_friendly_400(client, seed, ids):
    seed('one')
    for path in ('/api/run-comparisons', '/api/run-comparisons/charts/radar.svg'):
        response = selected(client, ids, path)
        assert response.status_code == 400 and isinstance(response.json()['detail'], str)


def test_execution_and_frozen_scope_differences_warn_without_splitting(client, seed):
    first = seed('first')
    second = seed('second', mode='majority_vote', calls=3, early_stop=True, kind='mock',
                  dataset='other-dataset', concurrency=8, image='other-image',
                  budget={'max_calls_per_question': 5, 'timeout_per_question_seconds': 300})
    response = selected(client, [first, second])
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data['entries']) == 2 and 'groups' not in data
    messages = '\n'.join(data['warnings'])
    for term in ('测评类型', '模式', '调用次数', '提前结束', '题库版本', '实际评测题目', '预算', '并发'):
        assert term in messages


def test_same_dataset_hash_different_subset_warns_and_question_order_does_not(client, seed):
    first, reordered, other = seed('first'), seed('reordered', reverse=True), seed('other', question_prefix='OTHER')
    assert selected(client, [first, reordered]).json()['warnings'] == []
    warnings = selected(client, [first, other]).json()['warnings']
    assert any('实际评测题目' in warning for warning in warnings)
    assert not any('题库版本' in warning for warning in warnings)


def test_frozen_input_comparison_uses_text_with_or_without_cached_hash(client, seed):
    first, legacy = seed('cached-hash'), seed('missing-hash')
    with db() as connection:
        body = json.loads(connection.execute('SELECT body FROM runs WHERE id=?', (legacy,)).fetchone()[0])
        for question in body['questions']:
            question.pop('input_sha256')
        connection.execute('UPDATE runs SET body=? WHERE id=?', (dumps(body), legacy))
    assert selected(client, [first, legacy]).json()['warnings'] == []
    with db() as connection:
        body = json.loads(connection.execute('SELECT body FROM runs WHERE id=?', (legacy,)).fetchone()[0])
        body['questions'][0]['input_sha256'] = 'same-input'
        body['questions'][0]['input_text'] = 'Changed frozen question despite the same stale hash'
        connection.execute('UPDATE runs SET body=? WHERE id=?', (dumps(body), legacy))
    assert any('实际评测题目' in warning for warning in selected(client, [first, legacy]).json()['warnings'])


def test_eight_radar_series_fixed_scale_and_consistent_colors(client, seed):
    ids = [seed(f'run-{index}', correct=4 if index < 7 else 3) for index in range(8)]
    data = selected(client, ids).json()
    reverse = selected(client, ids[::-1]).json()
    assert data == reverse
    colors = {entry['run_id']: entry['color'] for entry in data['entries']}
    assert len(set(colors.values())) == 8
    for chart in ('radar', 'ranking'):
        response = selected(client, ids, f'/api/run-comparisons/charts/{chart}.svg')
        assert response.status_code == 200 and response.headers['content-type'].startswith('image/svg+xml')
        root = ET.fromstring(response.text)
        assert ('固定 0–100 分 标尺' if chart == 'ranking' else '固定 0–100% 标尺') in response.text
        series = {group.attrib['data-run-id']: group for group in root.findall('.//s:g[@data-run-id]', NS)}
        assert set(series) == set(ids)
        for identity, group in series.items():
            assert group.find('s:rect', NS).attrib['fill'] == colors[identity]
            if chart == 'radar':
                assert len(group.findall('s:polygon', NS)) == 1
                assert len(group.findall('s:circle', NS)) == 4
        if chart == 'radar':
            labels = [element.text for element in root.findall('.//s:text', NS)]
            assert all(tick in labels for tick in ('0%', '25%', '50%', '75%', '100%'))
            group = series[ids[0]]
            assert group.find('s:circle', NS).attrib['cy'] == '145.0000'


def test_missing_dimension_keeps_score_formula_but_has_no_polygon(client, seed):
    ids = [seed('complete'), seed('partial', missing='L3_main')]
    data = selected(client, ids).json()
    partial = next(row for row in data['entries'] if row['run_id'] == 'partial')
    assert partial['dimensions']['L3_main']['accuracy'] is None
    assert partial['weighted_score'] == pytest.approx(65)
    assert any('缺少评测维度' in message for message in data['warnings'])
    root = ET.fromstring(selected(client, ids, '/api/run-comparisons/charts/radar.svg').text)
    partial_series = root.find('.//s:g[@data-run-id="partial"]', NS)
    assert not partial_series.findall('s:polygon', NS)
    assert len(partial_series.findall('s:circle', NS)) == 3


def test_svg_escapes_names_limits_long_labels_and_excludes_private_fields(client, seed):
    name = '<script>alert("x")</script>&' + '很长的测评名称' * 80
    ids = [seed('long', name=name), seed('short')]
    responses = [client.get('/api/run-comparisons/candidates'), selected(client, ids)]
    for chart in ('radar', 'ranking'):
        response = selected(client, ids, f'/api/run-comparisons/charts/{chart}.svg')
        responses.append(response)
        root = ET.fromstring(response.text)
        assert not root.findall('.//s:script', NS)
        assert '<script>' not in response.text and '&lt;script&gt;' in response.text
        assert all(len(text.text or '') < 90 for text in root.findall('.//s:text', NS))
        assert any(name in (title.text or '') for title in root.findall('.//s:title', NS))
    for response in responses:
        assert response.status_code == 200
        for secret in ('PRIVATE_', 'api_key', 'api_key_env', 'source_metadata', 'reference_text', 'gt_answer',
                       'input_text', 'prompt_system', 'cohort', 'dataset_sha256'):
            assert secret not in response.text


def test_comparison_is_readonly_even_for_legacy_without_monitor(client, seed, monkeypatch):
    ids = [seed('cached'), seed('legacy', legacy=True)]
    from app.services import run_comparison, runs
    calls = []
    actual_report = runs.report
    def observed(identity, include_results=True):
        calls.append((identity, include_results))
        return actual_report(identity, include_results)
    monkeypatch.setattr(run_comparison, 'report', observed)
    before = database_rows()
    assert client.get('/api/run-comparisons/candidates').status_code == 200
    assert selected(client, ids).status_code == 200
    for chart in ('radar', 'ranking'):
        assert selected(client, ids, f'/api/run-comparisons/charts/{chart}.svg').status_code == 200
    assert database_rows() == before
    assert ('cached', False) in calls and ('legacy', True) in calls
    with db() as connection:
        assert connection.execute('SELECT COUNT(*) FROM run_monitor WHERE run_id=?', ('legacy',)).fetchone()[0] == 0


def test_unsupported_chart_is_friendly_400(client, seed):
    ids = [seed('a'), seed('b')]
    response = selected(client, ids, '/api/run-comparisons/charts/unknown.svg')
    assert response.status_code == 400 and '仅支持' in response.json()['detail']
