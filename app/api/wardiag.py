"""Read local WarDiag presentation exports and relay its existing evaluation API."""
from functools import lru_cache
from html import escape
from html.parser import HTMLParser
from http.cookies import SimpleCookie
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.settings import ROOT


router = APIRouter(prefix='/api/wardiag', tags=['WarDiag'])
TASK_IDS = frozenset({'comprehensive_decision', 'situation_understanding',
                      'interaction_prediction', 'value_estimation', 'equilibrium_selection'})
PROXY_PREFIX = '/api/wardiag/evaluation'
_RUN = r'[a-zA-Z0-9_-]{8,80}'
_GET = re.compile(rf'(?:packages(?:/template)?|bootstrap|catalog|runs(?:/{_RUN}(?:/events)?)?)\Z')
_POST = re.compile(rf'(?:packages(?:/[a-f0-9]{{64}}/self-test)?|runs(?:/{_RUN}/control)?)\Z')
_UNAVAILABLE = '智能体测评服务当前不可用，请稍后刷新。'


class _QuestionHTML(HTMLParser):
    """Keep presentation markup while excluding active content and resources."""
    tags = frozenset('div span p br hr h1 h2 h3 h4 h5 h6 ul ol li blockquote pre code '
                     'strong em b i u s sub sup table thead tbody tfoot tr th td '
                     'details summary dl dt dd a'.split())
    blocked = frozenset({'script', 'style', 'iframe', 'object', 'embed', 'svg', 'math',
                         'form', 'input', 'textarea', 'button', 'select', 'template'})
    void = frozenset({'br', 'hr'})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.suppressed, self.opened = [], [], []

    def handle_starttag(self, tag, attrs):
        if self.suppressed:
            if tag in self.blocked:
                self.suppressed.append(tag)
            return
        if tag in self.blocked:
            if tag not in {'input', 'embed'}:
                self.suppressed.append(tag)
            return
        if tag not in self.tags:
            return
        safe = []
        for name, value in attrs:
            if tag in {'td', 'th'} and name in {'colspan', 'rowspan'} and value and re.fullmatch(r'[1-9]|1[0-9]|20', value):
                safe.append(f' {name}="{value}"')
            elif tag == 'details' and name == 'open':
                safe.append(' open')
        self.parts.append('<' + tag + ''.join(safe) + '>')
        if tag not in self.void:
            self.opened.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.pop()
            return
        if tag in self.opened:
            while self.opened:
                current = self.opened.pop()
                self.parts.append('</' + current + '>')
                if current == tag:
                    break

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(escape(data))


def _html(value):
    parser = _QuestionHTML()
    parser.feed(str(value or ''))
    parser.close()
    for tag in reversed(parser.opened):
        parser.parts.append('</' + tag + '>')
    return ''.join(parser.parts)


def _text(value, maximum=300):
    return value[:maximum] if isinstance(value, str) else ''


def _reader_path():
    return Path(os.environ.get('WARGAME_WARDIAG_READER', ROOT / 'data/wardiag/reader.html'))


@lru_cache(maxsize=2)
def _read_export(filename, modified, size):
    if size > 512 * 1024 * 1024:
        raise ValueError('EXPORT_TOO_LARGE')
    text = Path(filename).read_text(encoding='utf-8')
    comparison = {}
    if text.lstrip().startswith('{'):
        value = json.loads(text)
        results = value.get('evaluation_results') if isinstance(value, dict) else None
        comparison = results.get('agent_comparison', {}) if isinstance(results, dict) else {}
    else:
        match = re.search(r'<script\b(?=[^>]*\bid\s*=\s*["\']sample-data["\'])[^>]*>(.*?)</script\s*>',
                          text, re.IGNORECASE | re.DOTALL)
        if not match:
            raise ValueError('EXPORT_DATA_MISSING')
        value = json.loads(match.group(1))
        results = re.search(r'<script\b(?=[^>]*\bid\s*=\s*["\']evaluation-results["\'])[^>]*>(.*?)</script\s*>',
                            text, re.IGNORECASE | re.DOTALL)
        if results:
            try:
                comparison = json.loads(results.group(1)).get('agent_comparison', {})
            except (ValueError, AttributeError):
                comparison = {}
    if not isinstance(value, dict) or not isinstance(value.get('collections'), list):
        raise ValueError('EXPORT_FORMAT_INVALID')
    collection = next((c for c in value['collections'] if isinstance(c, dict)
                       and c.get('id') == value.get('default_collection')), None)
    if collection is None:
        collection = next((c for c in value['collections'] if isinstance(c, dict)), None)
    if collection is None or not isinstance(collection.get('groups'), list):
        raise ValueError('EXPORT_COLLECTION_MISSING')
    # Portable all-question views refer to bodies kept once in source collections.
    # Resolve all four identity fields; never fall back to a different version.
    lookup = {}
    for source in value['collections']:
        if not isinstance(source, dict):
            continue
        for group in source.get('groups', []):
            if not isinstance(group, dict):
                continue
            for task in group.get('tasks', []):
                if isinstance(task, dict):
                    key = (source.get('id'), group.get('id'), task.get('question_id'), task.get('question_sha256'))
                    if all(isinstance(part, str) for part in key):
                        lookup[key] = task
    resolved = []
    for group in collection['groups']:
        if not isinstance(group, dict) or not isinstance(group.get('task_refs'), list):
            resolved.append(group)
            continue
        entries = []
        for reference in group['task_refs']:
            key = tuple(reference.get(name) for name in ('collection_id', 'group_id', 'question_id', 'question_sha256')) if isinstance(reference, dict) else ()
            task = lookup.get(key) if len(key) == 4 and all(isinstance(part, str) for part in key) else None
            if task is None:
                entries = []
                break
            entries.append(task)
        resolved.append({**group, 'tasks': entries})
    collection = {**collection, 'groups': resolved}
    return value, collection, _comparison(comparison)


def _number(value):
    return value if type(value) in {int, float} and math.isfinite(value) else None


def _comparison(source):
    if (not isinstance(source, dict) or source.get('schema_version') != 'wardiag_agent_comparison_v1'
            or source.get('status') != 'measured'):
        return {'available': False}
    rows = []
    for model in source.get('models', []):
        if not isinstance(model, dict):
            continue
        overall, execution, usage, budget = (model.get(k, {}) for k in ('overall', 'execution', 'usage', 'budget'))
        if not all(isinstance(v, dict) for v in (overall, execution, usage, budget)):
            continue
        task_metrics = model.get('tasks', {})
        tasks = {key: {'mean': _number(v.get('mean')), 'n': _number(v.get('denominator'))}
                 for key, v in task_metrics.items() if key in TASK_IDS and isinstance(v, dict)} if isinstance(task_metrics, dict) else {}
        rows.append({'label': _text(model.get('label')), 'role': _text(model.get('role')),
                     'mean': _number(overall.get('mean')), 'n': _number(overall.get('denominator')),
                     'completed': _number(execution.get('completed')), 'truncated': _number(execution.get('output_limit')),
                     'total_tokens': _number(usage.get('total_tokens')), 'tasks': tasks,
                     'budget': {key: value for key, value in budget.items()
                                if key in {'max_rounds', 'max_output_tokens_per_round', 'max_output_tokens_per_question_upper_bound',
                                           'max_tool_calls_per_question', 'thinking_enabled', 'tools_enabled'}
                                and (type(value) is bool or _number(value) is not None)}})
    return {'available': bool(rows), 'date': _text(source.get('date_shanghai')),
            'question_count': _number(source.get('question_count')), 'mother_count': _number(source.get('mother_count')),
            'different_budget': source.get('different_budget') is True,
            'controlled_correction': source.get('controlled_correction') is True,
            'formal_gold': source.get('formal_gold') is True, 'rows': rows,
            'note': '既有实验汇总仅描述当时题目、参考和调用预算下的结果；不同预算的观察性对照不能归因为单一工具效果。'}


def _data():
    path = _reader_path()
    stat = path.stat()
    return _read_export(str(path), stat.st_mtime_ns, stat.st_size)


def _task(task):
    return {key: _text(task.get(key)) for key in
            ('id', 'title', 'display_id', 'question_id', 'question_sha256')}


def _demo_path():
    return Path(os.environ.get('WARGAME_WARDIAG_DEMO', ROOT / 'data/wardiag/demo.zip'))


@router.get('/catalog')
def catalog():
    demo = {'template_available': _demo_path().is_file(), 'template_url': '/api/wardiag/demo/template'}
    try:
        value, collection, comparison = _data()
    except (OSError, ValueError, UnicodeError, TypeError):
        return {'available': False, 'message': '当前未载入题库阅读材料。', 'updated_at': '',
                'counts': {'mothers': 0, 'questions': 0}, 'tasks': [], 'challenges': [],
                'groups': [], 'demo': {**demo, 'comparison': {'available': False}}}
    portal = value.get('portal') if isinstance(value.get('portal'), dict) else {}
    tasks = [{key: _text(row.get(key), 2000) for key in ('id', 'label', 'intro', 'input', 'output')}
             for row in portal.get('tasks', []) if isinstance(row, dict) and row.get('id') in TASK_IDS]
    challenges = [{key: _text(row.get(key), 1000) for key in ('id', 'label', 'intro')}
                  for row in portal.get('challenges', []) if isinstance(row, dict)]
    axes = {row['id'] for row in challenges}
    groups = []
    for index, group in enumerate(collection['groups']):
        if not isinstance(group, dict):
            continue
        entries = [_task(t) for t in group.get('tasks', [])
                   if isinstance(t, dict) and t.get('id') in TASK_IDS]
        if not entries:
            continue
        groups.append({'index': index, 'label': f'母局势 {len(groups) + 1:03d}',
                       'challenges': [axis for axis in group.get('mother_challenges', []) if axis in axes],
                       'tasks': entries})
    return {'available': bool(groups), 'message': '' if groups else '当前阅读材料没有可展示的题目。',
            'updated_at': _text(collection.get('updated_at') or value.get('built_at')),
            'counts': {'mothers': len(groups), 'questions': sum(len(g['tasks']) for g in groups)},
            'tasks': tasks, 'challenges': challenges, 'groups': groups, 'demo': {**demo, 'comparison': comparison}}


@router.get('/questions/{group_index}/{task_id}')
def question(group_index: int, task_id: str):
    if group_index < 0 or task_id not in TASK_IDS:
        raise HTTPException(404, '找不到这道题目。')
    try:
        _, collection, _ = _data()
        group = collection['groups'][group_index]
        task = next(t for t in group['tasks'] if isinstance(t, dict) and t.get('id') == task_id)
        sections = [{'title': _text(s.get('title'), 1000), 'html': _html(s.get('html'))}
                    for s in task.get('sections', []) if isinstance(s, dict)]
    except (OSError, ValueError, UnicodeError, TypeError, IndexError, KeyError, StopIteration):
        raise HTTPException(404, '找不到这道题目，请刷新题库。') from None
    return {**_task(task), 'group_index': group_index, 'sections': sections}


def _origin():
    value = os.environ.get('WARGAME_WARDIAG_SERVICE_URL', 'http://127.0.0.1:8766')
    try:
        parts = urlsplit(value)
        valid = (parts.scheme == 'http' and parts.hostname in {'127.0.0.1', '::1'}
                 and 1 <= (parts.port or 80) <= 65535 and parts.path in {'', '/'}
                 and not parts.username and not parts.password and not parts.query and not parts.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(503, _UNAVAILABLE)
    return f'http://{parts.netloc}'


def _cookie(value):
    jar = SimpleCookie()
    try:
        jar.load(value or '')
        value = jar['wardiag_eval_session'].value if 'wardiag_eval_session' in jar else ''
        return 'wardiag_eval_session=' + value if re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', value) else ''
    except Exception:
        return ''


def _response_cookie(response):
    value = _cookie(response.headers.get('set-cookie'))
    return value + '; Path=/api/wardiag; HttpOnly; SameSite=Strict' if value else ''


def _headers(request, origin):
    expected = str(request.base_url).rstrip('/')
    if ((request.headers.get('origin') is not None and request.headers['origin'] != expected)
            or request.headers.get('sec-fetch-site') in {'cross-site', 'same-site'}
            or (request.method == 'POST' and request.headers.get('origin') != expected)):
        raise HTTPException(403, '请从当前网页提交操作。')
    headers = {'Accept': request.headers.get('accept', 'application/json'), 'Origin': origin,
               'Sec-Fetch-Site': 'same-origin'}
    cookie = _cookie(request.headers.get('cookie'))
    if cookie:
        headers['Cookie'] = cookie
    for name in ('Content-Type', 'X-WarDiag-CSRF', 'Last-Event-ID'):
        if name in request.headers:
            headers[name] = request.headers[name]
    return headers


async def _relay(request, path, *, session=False):
    if not (_GET if request.method == 'GET' else _POST).fullmatch(path):
        raise HTTPException(404, '没有这个测评接口。')
    query = str(request.url.query)
    if query and not (path.endswith('/events') and re.fullmatch(r'after=\d{1,12}', query)):
        raise HTTPException(400, '测评请求参数无效。')
    origin = _origin()
    headers = _headers(request, origin)
    body = None
    if request.method == 'POST':
        length = request.headers.get('content-length', '')
        maximum = 28_000_000 if path == 'packages' else 1_000_000
        if not re.fullmatch(r'\d{1,8}', length) or not 0 < int(length) <= maximum:
            raise HTTPException(413, '提交内容超过允许大小。')
        if headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise HTTPException(415, '请使用 JSON 提交测评操作。')
        body = await request.body()
        if len(body) != int(length):
            raise HTTPException(400, '提交内容长度无效。')
    client = httpx.AsyncClient(timeout=httpx.Timeout(180, connect=3), follow_redirects=False, trust_env=False)
    cookie = ''
    try:
        if session:
            start = await client.get(origin + '/api/evaluation/packages', headers=headers)
            if start.status_code != 200:
                raise HTTPException(503, _UNAVAILABLE)
            cookie = _response_cookie(start)
            headers.pop('Cookie', None)
        target = origin + '/api/evaluation/' + path + ('?' + query if query else '')
        response = await client.send(client.build_request(request.method, target, headers=headers, content=body),
                                     stream=path.endswith('/events'))
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise HTTPException(503, _UNAVAILABLE)
        result_headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
        cookie = _response_cookie(response) or cookie
        if cookie:
            result_headers['Set-Cookie'] = cookie
        mime = response.headers.get('content-type', 'application/json')
        if path.endswith('/events') and response.status_code == 200:
            async def events():
                try:
                    async for chunk in response.aiter_bytes():
                        yield chunk
                finally:
                    await response.aclose()
                    await client.aclose()
            return StreamingResponse(events(), status_code=200, media_type=mime, headers=result_headers)
        payload = await response.aread()
        await client.aclose()
        if path.endswith('/template') and response.status_code == 200:
            result_headers['Content-Disposition'] = 'attachment; filename="wardiag-demo-agent.zip"'
        return Response(payload, status_code=response.status_code, media_type=mime, headers=result_headers)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(503, _UNAVAILABLE) from None
    except Exception:
        await client.aclose()
        raise


@router.get('/demo/template')
async def template(request: Request):
    path = _demo_path()
    if path.is_file():
        return FileResponse(path, media_type='application/zip', filename='wardiag-demo-agent.zip')
    return await _relay(request, 'packages/template', session=True)


@router.api_route('/evaluation/{path:path}', methods=['GET', 'POST'])
async def evaluation(request: Request, path: str):
    return await _relay(request, path)
