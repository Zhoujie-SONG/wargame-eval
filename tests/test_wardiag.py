import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import wardiag


@pytest.fixture
def showcase(tmp_path, monkeypatch):
    monkeypatch.setenv('WARGAME_WARDIAG_READER', str(tmp_path / 'reader.html'))
    monkeypatch.setenv('WARGAME_WARDIAG_DEMO', str(tmp_path / 'demo.zip'))
    monkeypatch.setenv('WARGAME_WARDIAG_SERVICE_URL', 'http://127.0.0.1:8766')
    app = FastAPI()
    app.include_router(wardiag.router)
    with TestClient(app) as client:
        yield client


def export(tmp_path, *, markup=None):
    task = {'id': 'comprehensive_decision', 'title': '综合策略生成', 'display_id': '001-1',
            'question_id': 'question-1', 'question_sha256': 'a' * 64,
            'sections': [{'title': '题目', 'html': markup or '<p>合成测试题面</p>'}],
            'answer': {'html': 'private answer'}, 'explanation': {'html': 'private reference'},
            'locator': {'source': 'private locator'}, 'replay': {'source': 'private replay'}}
    value = {'default_collection': 'current', 'built_at': '2026-11-07',
             'portal': {'tasks': [{'id': 'comprehensive_decision', 'label': '综合策略生成'}],
                        'challenges': [{'id': 'dynamic_changes', 'label': '动态变化'}]},
             'collections': [{'id': 'old', 'groups': []}, {'id': 'current', 'groups': [
                 {'label': 'private source label', 'mother_challenges': ['dynamic_changes'], 'tasks': [task]}]}]}
    (tmp_path / 'reader.html').write_text(json.dumps(value), encoding='utf-8')
    return value


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(wardiag.httpx, 'AsyncClient',
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_missing_export_is_an_explicit_empty_state(showcase):
    response = showcase.get('/api/wardiag/catalog')
    assert response.status_code == 200
    assert response.json()['available'] is False
    assert response.json()['counts'] == {'mothers': 0, 'questions': 0}
    assert response.json()['groups'] == []


def test_catalog_uses_only_selected_collection_and_public_fields(showcase, tmp_path):
    export(tmp_path)
    value = showcase.get('/api/wardiag/catalog').json()
    assert value['available'] is True
    assert value['counts'] == {'mothers': 1, 'questions': 1}
    assert value['groups'][0]['label'] == '母局势 001'
    assert value['groups'][0]['tasks'][0]['question_id'] == 'question-1'
    assert 'private' not in json.dumps(value)
    assert set(value['groups'][0]['tasks'][0]) == {
        'id', 'title', 'display_id', 'question_id', 'question_sha256'}


def test_question_html_cannot_escape_container_or_load_active_resources(showcase, tmp_path):
    export(tmp_path, markup='</div><p onclick="bad()">题面<script>secret()</script>'
           '<style>body{display:none}</style><img src="https://example.invalid/a" onerror="bad()">'
           '<a href="javascript:bad()">文本</a><svg><script>bad()</script></svg>'
           '<table><tr><td colspan="2">数值</td></tr></table></p>')
    response = showcase.get('/api/wardiag/questions/0/comprehensive_decision')
    assert response.status_code == 200
    value = response.json()
    html = value['sections'][0]['html']
    assert html.startswith('<p>题面')
    assert '<td colspan="2">数值</td>' in html
    assert '<a>文本</a>' in html
    for term in ('script', 'onclick', 'img', 'href', 'svg', 'style', 'secret', 'https:'):
        assert term not in html
    assert not ({'answer', 'explanation', 'locator', 'replay'} & value.keys())
    assert showcase.get('/api/wardiag/questions/0/unknown').status_code == 404


def test_portable_all_question_refs_require_exact_identity(showcase, tmp_path):
    value = export(tmp_path)
    source = value['collections'][1]
    source['groups'][0]['id'] = 'source-group'
    reference = {'collection_id': 'current', 'group_id': 'source-group',
                 'question_id': 'question-1', 'question_sha256': 'a' * 64}
    value['collections'].append({'id': 'all_questions', 'groups': [
        {'task_refs': [reference]},
        {'task_refs': [{**reference, 'question_sha256': 'b' * 64}]}]})
    value['default_collection'] = 'all_questions'
    (tmp_path / 'reader.html').write_text(json.dumps(value), encoding='utf-8')
    catalog = showcase.get('/api/wardiag/catalog').json()
    assert catalog['counts'] == {'mothers': 1, 'questions': 1}
    assert catalog['groups'][0]['index'] == 0
    assert showcase.get('/api/wardiag/questions/0/comprehensive_decision').status_code == 200
    assert showcase.get('/api/wardiag/questions/1/comprehensive_decision').status_code == 404


def test_html_export_comparison_is_only_aggregate_allowlist(showcase, tmp_path):
    value = export(tmp_path)
    comparison = {'schema_version': 'wardiag_agent_comparison_v1', 'status': 'measured',
                  'date_shanghai': '2026-11-01', 'question_count': 5, 'mother_count': 1,
                  'different_budget': True, 'models': [{
                      'label': '测试模型', 'role': 'baseline', 'provider_id': 'private provider',
                      'overall': {'mean': 0.2, 'denominator': 5, 'private': 'private evidence'},
                      'execution': {'completed': 5, 'output_limit': 0}, 'usage': {'total_tokens': 100},
                      'budget': {'max_rounds': 1, 'tools_enabled': False},
                      'tasks': {'value_estimation': {'mean': 0.1, 'denominator': 1}},
                      'records': ['private answer']}], 'private': 'private reference'}
    (tmp_path / 'reader.html').write_text(
        '<script id="sample-data" type="application/json">' + json.dumps(value) + '</script>'
        '<script id="evaluation-results" type="application/json">' +
        json.dumps({'agent_comparison': comparison}) + '</script>', encoding='utf-8')
    demo = showcase.get('/api/wardiag/catalog').json()['demo']['comparison']
    assert demo['available'] is True
    assert demo['date'] == '2026-11-01'
    assert demo['rows'][0]['mean'] == 0.2
    assert demo['rows'][0]['tasks']['value_estimation'] == {'mean': 0.1, 'n': 1}
    assert 'private' not in json.dumps(demo)


def test_local_demo_is_downloaded_without_contacting_evaluation(showcase, tmp_path, monkeypatch):
    (tmp_path / 'demo.zip').write_bytes(b'zip test bytes')
    transport(monkeypatch, lambda request: pytest.fail('local template must not contact service'))
    response = showcase.get('/api/wardiag/demo/template')
    assert response.status_code == 200
    assert response.content == b'zip test bytes'
    assert 'attachment' in response.headers['content-disposition']


def test_proxy_scopes_cookie_and_preserves_bootstrap_contract(showcase, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url == 'http://127.0.0.1:8766/api/evaluation/bootstrap'
        assert request.headers['origin'] == 'http://127.0.0.1:8766'
        assert request.headers['cookie'] == 'wardiag_eval_session=abcdefghijklmnop'
        return httpx.Response(200, json={'csrf_token': 'token', 'providers': [], 'runs': []},
                              headers={'Set-Cookie': 'wardiag_eval_session=abcdefghijklmnop; Path=/api/evaluation; HttpOnly'})
    transport(monkeypatch, handler)
    response = showcase.get('/api/wardiag/evaluation/bootstrap',
                            headers={'Cookie': 'other=private; wardiag_eval_session=abcdefghijklmnop'})
    assert response.status_code == 200
    assert response.json()['csrf_token'] == 'token'
    assert 'Path=/api/wardiag;' in response.headers['set-cookie']
    assert 'private' not in response.headers['set-cookie']
    assert len(calls) == 1


def test_proxy_forwards_only_explicit_same_origin_posts(showcase, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'POST'
        assert request.url.path == '/api/evaluation/runs'
        assert request.headers['origin'] == 'http://127.0.0.1:8766'
        assert request.headers['X-WarDiag-CSRF'] == 'csrf'
        assert json.loads(request.content) == {'selection': {'method': 'all'}}
        return httpx.Response(201, json={'run_id': 'run-123456'})
    transport(monkeypatch, handler)
    path = '/api/wardiag/evaluation/runs'
    assert showcase.post(path, json={}).status_code == 403
    assert showcase.post(path, json={}, headers={'Origin': 'http://example.invalid'}).status_code == 403
    response = showcase.post(path, json={'selection': {'method': 'all'}},
                             headers={'Origin': 'http://testserver', 'X-WarDiag-CSRF': 'csrf'})
    assert response.status_code == 201
    assert len(calls) == 1


@pytest.mark.parametrize('origin', ['https://127.0.0.1:8766', 'http://example.invalid', 'http://127.0.0.1:8766/other'])
def test_proxy_rejects_non_loopback_or_non_origin_targets(showcase, monkeypatch, origin):
    monkeypatch.setenv('WARGAME_WARDIAG_SERVICE_URL', origin)
    transport(monkeypatch, lambda request: pytest.fail('invalid target must not be contacted'))
    assert showcase.get('/api/wardiag/evaluation/bootstrap').status_code == 503


def test_proxy_rejects_paths_queries_redirects_and_unreachable_service(showcase, monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(302, headers={'Location': 'http://example.invalid'}))
    assert showcase.get('/api/wardiag/evaluation/other').status_code == 404
    assert showcase.get('/api/wardiag/evaluation/bootstrap?url=bad').status_code == 400
    response = showcase.get('/api/wardiag/evaluation/bootstrap')
    assert response.status_code == 503
    assert 'location' not in response.headers
