"""Teams of a batch run together, questions inside a team run N at a time, 429 waits are free."""
import asyncio
import json
import re
import time
from uuid import uuid4

import pytest

from app.core.security import encrypt
from app.db import db, dumps
from app.services import runs
from app.services.monitor import ensure

LEVELS = [('L2', 'main'), ('L3', 'main'), ('L1', 'sub'), ('L2', 'sub')]


def make_run(n, concurrency, name='队'):
    questions = [dict(question_id=f'q{i}', level=LEVELS[i % 4][0], role=LEVELS[i % 4][1], gt_answer=['A'], parent_id=None,
                      input_text='Q A. 甲 B. 乙', question_type='single_choice', options=['A', 'B'], image=None)
                 for i in range(n)]
    body = dict(name=name, kind='partial_test', model=dict(name='M', model_id='M1', provider='mock', is_aggregator=False),
                prompt={'version': 'p', 'system': '', 'initial': '', 'review': ''}, mode='single_pass', calls=1, early_stop=False,
                config={'competition': {'max_calls_per_question': 3, 'timeout_per_question_seconds': 30}},
                cohort='c', dataset_sha256='d', started_at=None, finished_at=None, official=False,
                questions=questions, concurrency=concurrency, team_id=name, submission_version='v1')
    rid = uuid4().hex
    with db() as c:
        c.execute('INSERT INTO runs(id,status,created_at,body,secret) VALUES(?,?,?,?,?)',
                  (rid, 'queued', '2026-10-28T00:00:00+00:00', dumps(body), encrypt('')))
        ensure(c, rid, body)
    return rid


class Probe:
    """Model stand-in that records how many requests are in flight at once."""
    def __init__(self, delay=.05):
        self.now = self.peak = 0
        self.delay = delay

    async def complete(self, messages, timeout):
        self.now += 1
        self.peak = max(self.peak, self.now)
        await asyncio.sleep(self.delay)
        self.now -= 1
        return {'output': 'A', 'usage': {}}


def status_and_results(rid):
    with db() as c:
        status = c.execute('SELECT status FROM runs WHERE id=?', (rid,)).fetchone()[0]
        n = c.execute('SELECT COUNT(*) FROM results WHERE run_id=?', (rid,)).fetchone()[0]
    return status, n


@pytest.fixture
def probe(client, monkeypatch):
    p = Probe()
    monkeypatch.setattr(runs, 'make_client', lambda *a: p)
    return p


def test_questions_inside_a_team_run_n_at_a_time(probe):
    rid = make_run(24, 4)
    started = time.monotonic()
    asyncio.run(runs.Runner().execute(rid))
    assert probe.peak == 4
    assert status_and_results(rid) == ('completed', 24)
    assert time.monotonic()-started < 24*.05*.6          # clearly faster than one at a time


def test_default_is_one_at_a_time(probe):
    rid = make_run(6, 1)
    asyncio.run(runs.Runner().execute(rid))
    assert probe.peak == 1 and status_and_results(rid) == ('completed', 6)


def test_teams_of_one_packet_run_together_and_global_cap_holds(probe, monkeypatch):
    ids = [make_run(8, 1, f'队{i}') for i in range(3)]
    asyncio.run(runs.Runner().run_packet(tuple(ids)))
    assert probe.peak == 3                                  # three teams, one question each, all at once
    assert all(status_and_results(i) == ('completed', 8) for i in ids)
    probe.peak = 0
    monkeypatch.setattr(runs, 'GLOBAL_CONCURRENCY', 2)
    ids = [make_run(8, 4, f'队{i}') for i in range(3)]
    asyncio.run(runs.Runner().run_packet(tuple(ids)))
    assert probe.peak == 2                                  # 3 teams × 4 lanes, held to the global cap
    assert all(status_and_results(i) == ('completed', 8) for i in ids)


def test_stop_ends_all_lanes_without_recording_half_answers(probe):
    rid = make_run(60, 4)

    async def go():
        runner = runs.Runner()
        task = asyncio.create_task(runner.execute(rid))
        await asyncio.sleep(.12)
        with db() as c:
            c.execute('UPDATE runs SET stop=1 WHERE id=?', (rid,))
        await task
    asyncio.run(go())
    status, n = status_and_results(rid)
    assert status == 'stopped' and 0 < n < 60
    assert probe.now == 0


def test_cancelled_packet_marks_running_teams_interrupted(probe):
    ids = [make_run(40, 2, f'队{i}') for i in range(2)]

    async def go():
        task = asyncio.create_task(runs.Runner().run_packet(tuple(ids)))
        await asyncio.sleep(.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    assert all(status_and_results(i)[0] == 'interrupted' for i in ids)


def test_one_failing_team_does_not_stop_the_others(probe, monkeypatch):
    good, bad = make_run(8, 2, '好'), make_run(8, 2, '坏')
    calls = {'n': 0}

    def maker(*a):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('boom')
        return probe
    monkeypatch.setattr(runs, 'make_client', maker)
    asyncio.run(runs.Runner().run_packet((good, bad)))
    states = {status_and_results(good)[0], status_and_results(bad)[0]}
    assert states == {'completed', 'infrastructure_error'}


def test_probe_stops_at_the_first_refused_level_and_reports_the_stable_one(client, monkeypatch):
    import httpx
    from app.services import concurrency_probe as cp
    request = httpx.Request('POST', 'https://example.com/v1/chat/completions')

    class Limited8:
        sent = 0

        def __init__(self):
            self.active = 0

        async def complete(self, messages, timeout):
            Limited8.sent += 1
            self.active += 1
            try:
                await asyncio.sleep(.02)
                if self.active > 4:                      # this API accepts at most 4 at once
                    raise httpx.HTTPStatusError('x', request=request, response=httpx.Response(429, request=request))
                return {'output': 'A', 'usage': {}}
            finally:
                self.active -= 1
    fake = Limited8()
    monkeypatch.setattr(cp, '_prepare', lambda sid: ({'team_id': 'alpha'}, {'model_id': 'M'}, fake, [[] for _ in range(16)]))
    result = client.post('/api/competition/probe-concurrency', json={'submission_id': 'x'}).json()
    assert result['stable'] == 4 and result['team_id'] == 'alpha'
    assert [row['level'] for row in result['levels']] == [1, 2, 4, 8]    # stops after 8 failed, never sends 16
    assert result['levels'][-1]['limited'] > 0 and result['requests'] == 15 == Limited8.sent
    assert '4 路稳定' in result['note']


def test_probe_refuses_while_a_run_is_active_and_for_unknown_submissions(client):
    response = client.post('/api/competition/probe-concurrency', json={'submission_id': 'nope'})
    assert response.status_code == 400 and '不存在' in response.json()['detail']
    make_run(3, 1)                                           # a queued run
    response = client.post('/api/competition/probe-concurrency', json={'submission_id': 'nope'})
    assert response.status_code == 400 and '正在运行' in response.json()['detail']


def test_many_teams_and_lanes_write_every_result_exactly_once(client, monkeypatch):
    p = Probe(delay=.002)
    monkeypatch.setattr(runs, 'make_client', lambda *a: p)
    ids = [make_run(120, 16, f'队{i}') for i in range(4)]       # 4 teams × 16 lanes, written from many threads
    started = time.monotonic()
    asyncio.run(runs.Runner().run_packet(tuple(ids)))
    for rid in ids:
        assert status_and_results(rid) == ('completed', 120)
        with db() as c:
            calls = c.execute('SELECT COUNT(*) FROM calls WHERE run_id=?', (rid,)).fetchone()[0]
            monitor = c.execute('SELECT completed FROM run_monitor WHERE run_id=?', (rid,)).fetchone()[0]
        assert calls == 120 and monitor == 120                  # monitor triggers stayed in step
    assert p.peak <= 64 and time.monotonic()-started < 20


def make_sets_run(sets, concurrency):
    """sets: sub-question counts; each set gets one main question (listed first, as in the dataset) and its sub-questions."""
    rid = make_run(0, concurrency, '套题队')
    qs = []
    for s, subs in enumerate(sets):
        qs.append(dict(question_id=f's{s}-CORE', parent_id=None, level='L2', role='main'))
        qs += [dict(question_id=f's{s}-P{k}', parent_id=f's{s}-CORE', level='L1', role='sub') for k in range(subs)]
    qs = [dict(q, gt_answer=['A'], input_text=f'Q[{q["question_id"]}] A. 甲 B. 乙', question_type='single_choice',
               options=['A', 'B'], image=None) for q in qs]
    with db() as c:
        body = json.loads(c.execute('SELECT body FROM runs WHERE id=?', (rid,)).fetchone()[0])
        body['questions'] = qs
        c.execute('UPDATE runs SET body=? WHERE id=?', (dumps(body), rid))
    return rid


class SetProbe:
    """Records which questions are in flight and the order in which they start."""
    def __init__(self, first_wave=1):
        self.active, self.started, self.peak, self.same_set = set(), [], 0, 0
        self.first_wave = first_wave
        self.wave_ready = asyncio.Event()

    async def complete(self, messages, timeout):
        qid = re.search(r'Q\[(s\d+-\w+)\]', str(messages)).group(1)
        sample = qid.split('-')[0]
        self.same_set = max(self.same_set, 1 + sum(a.startswith(sample + '-') for a in self.active))
        self.active.add(qid)
        self.started.append(qid)
        self.peak = max(self.peak, len({a.split('-')[0] for a in self.active}))
        if self.peak >= self.first_wave:
            self.wave_ready.set()
        await asyncio.wait_for(self.wave_ready.wait(), 2)
        await asyncio.sleep(.01)
        self.active.discard(qid)
        return {'output': 'A', 'usage': {}}


def test_question_sets_put_sub_questions_first_and_main_last():
    qs = [dict(question_id='A-CORE', parent_id=None, role='main'), dict(question_id='A-P1', parent_id='A-CORE', role='sub'),
          dict(question_id='B-CORE', parent_id=None, role='main'), dict(question_id='A-P2', parent_id='A-CORE', role='sub'),
          dict(question_id='B-P1', parent_id='B-CORE', role='sub')]
    assert [[q['question_id'] for q in g] for g in runs.question_sets(qs)] == [['A-P1', 'A-P2', 'A-CORE'], ['B-P1', 'B-CORE']]


def test_only_different_sets_run_together_sub_questions_first(client, monkeypatch):
    # Hold the first wave until all four lanes enter, independent of database/host speed.
    p = SetProbe(first_wave=4)
    monkeypatch.setattr(runs, 'make_client', lambda *a: p)
    rid = make_sets_run([3, 5, 2, 4, 6, 3], 4)
    asyncio.run(runs.Runner().execute(rid))
    assert status_and_results(rid) == ('completed', 29)
    assert p.same_set == 1 and p.peak == 4                    # never two of one set at once; 4 sets in parallel
    for sample in {q.split('-')[0] for q in p.started}:
        mine = [q for q in p.started if q.startswith(sample + '-')]
        assert mine[-1].endswith('CORE') and mine[:-1] == sorted(mine[:-1])


def test_one_lane_does_one_set_after_another(client, monkeypatch):
    p = SetProbe()
    monkeypatch.setattr(runs, 'make_client', lambda *a: p)
    rid = make_sets_run([2, 2, 2], 1)
    asyncio.run(runs.Runner().execute(rid))
    assert p.peak == 1 and [q.split('-')[0] for q in p.started] == ['s0']*3 + ['s1']*3 + ['s2']*3


def test_lanes_recheck_remaining_sets_after_awaiting_stop(client, monkeypatch):
    """Another lane may take the last set while the first lane reads the stop flag."""
    p = SetProbe()
    monkeypatch.setattr(runs, 'make_client', lambda *a: p)
    original_to_thread = asyncio.to_thread
    delayed = False

    async def delayed_stop_read(func, *args, **kwargs):
        nonlocal delayed
        if func.__name__ == 'read_stop' and not delayed:
            delayed = True
            # Let the other lanes take both remaining sets before the first resumes.
            async with asyncio.timeout(2):
                while len(p.started) < 2:
                    await asyncio.sleep(0)
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, 'to_thread', delayed_stop_read)
    rid = make_sets_run([0, 0], 4)
    asyncio.run(runs.Runner().execute(rid))
    assert delayed
    assert status_and_results(rid) == ('completed', 2)
    assert sorted(p.started) == ['s0-CORE', 's1-CORE']
