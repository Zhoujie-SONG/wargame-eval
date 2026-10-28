"""Measure how many simultaneous requests one team's API accepts, with a few real requests.

The probe sends the first real question (with its screenshot) in rising groups of 1, 2, 4, 8, 16
simultaneous requests and stops at the first group that is refused or fails. At most 31 requests
per team. Nothing is stored; the organizer decides the shared per-team concurrency from the result.
"""
import asyncio
import json
import time
import httpx
from app.db import db, question_rows
from app.core.model_client import make_client
from app.core.prompt_manager import initial_messages
from app.core.security import credentials
from app.services.runs import get_images

LEVELS = (1, 2, 4, 8, 16)
REQUEST_TIMEOUT = 60


async def _one(client, messages):
    started = time.monotonic()
    try:
        await asyncio.wait_for(client.complete(messages, REQUEST_TIMEOUT), REQUEST_TIMEOUT)
        return 'ok', time.monotonic()-started
    except httpx.HTTPStatusError as exc:
        return ('limited' if exc.response.status_code == 429 else 'error'), time.monotonic()-started
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return 'timeout', time.monotonic()-started
    except Exception:
        return 'error', time.monotonic()-started


def _prepare(submission_id):
    with db() as c:
        if c.execute("SELECT 1 FROM runs WHERE status IN ('queued','running','stopping') LIMIT 1").fetchone():
            raise ValueError('有测评任务正在运行或排队，比赛进行中不能检测并发')
        sub = c.execute('SELECT * FROM submissions WHERE id=?', (submission_id,)).fetchone()
        if not sub:
            raise ValueError('参赛方案不存在')
        model_row = c.execute('SELECT * FROM models WHERE id=?', (sub['model_id'],)).fetchone()
        prompt_row = c.execute('SELECT * FROM prompts WHERE id=?', (sub['prompt_id'],)).fetchone()
    if not model_row or not prompt_row:
        raise ValueError('模型或 Prompt 配置不存在')
    model, prompt = json.loads(model_row['body']), json.loads(prompt_row['body'])
    images = get_images()
    for q in question_rows():
        image = images.get((q['replay_id'], q['image_step']))
        if image and image['real']:
            public = {k: q[k] for k in ('input_text', 'question_type', 'options')} | {'image': image}
            break
    else:
        raise ValueError('题库中没有关联真实截图的题目，请先完成 01 数据集')
    return sub, model, make_client(model, credentials(model, model_row['secret'])), initial_messages(public, prompt)


async def probe_team(submission_id):
    sub, model, client, messages = await asyncio.to_thread(_prepare, submission_id)
    levels, stable = [], 0
    for level in LEVELS:
        outcomes = await asyncio.gather(*[_one(client, messages) for _ in range(level)])
        kinds = [k for k, _ in outcomes]
        row = {'level': level, 'ok': kinds.count('ok'), 'limited': kinds.count('limited'),
               'timeout': kinds.count('timeout'), 'error': kinds.count('error'),
               'seconds': round(sorted(t for _, t in outcomes)[len(outcomes)//2], 2)}
        levels.append(row)
        if row['ok'] != level:
            break
        stable = level
    if not levels or levels[0]['ok'] != 1:
        note = '单个请求也未成功，请先检查该队的 API 地址、Key 和模型'
    elif stable == LEVELS[-1]:
        note = f'{stable} 路同时请求均成功'
    else:
        note = f'{stable} 路稳定；{levels[-1]["level"]} 路出现限流或失败'
    return {'team_id': sub['team_id'], 'submission_id': submission_id, 'model_id': model['model_id'],
            'levels': levels, 'stable': stable, 'requests': sum(r['level'] for r in levels), 'note': note}
