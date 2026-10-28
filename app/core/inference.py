import asyncio
import copy
import inspect
import random
import time
from collections import Counter
import httpx
from app.core.answer_parser import parse_answer


class Stopped(Exception):
    pass


# HTTP 429 means the provider refused the request before doing any work. Wait and resend inside the
# same call slot: the wait does not use up the question's time limit or its call budget. After the last
# delay a still-limited request is recorded as an ordinary API error (counted wrong, as before).
BACKOFF_SECONDS = (1, 2, 4, 8, 16, 30, 30, 30)


def retry_delay(response, attempt):
    try:
        asked = float(response.headers.get('retry-after', ''))
    except (TypeError, ValueError):
        asked = None
    base = asked if asked is not None and asked >= 0 else BACKOFF_SECONDS[attempt]
    return min(60.0, base) + (random.random() * 0.5 if base else 0)


async def _maybe(fn, *args):
    """Callbacks may be plain functions (tests) or coroutines (the runner keeps database writes off the loop)."""
    value = fn(*args)
    return await value if inspect.isawaitable(value) else value


async def infer(public_question, prompt, model_client, mode, requested_calls, max_calls, timeout,
                save_call, should_stop, early_stop=False):
    from app.core.prompt_manager import initial_messages
    if not 1 <= requested_calls <= max_calls:
        raise ValueError('调用预算越界')
    deadline = time.monotonic()+timeout
    messages = initial_messages(public_question, prompt)
    limit = 1 if mode == 'single_pass' else requested_calls
    answers, records = [], []
    previous = None
    status = 'invalid_output'
    for ordinal in range(1, limit+1):
        if await _maybe(should_stop):
            raise Stopped()
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            status = 'timeout'
            break
        # All attempts, including HTTP errors/timeouts, consume a slot before sending.
        record = {'ordinal': ordinal, 'status': 'sending', 'elapsed_seconds': 0, 'output': '', 'usage': {}}
        await _maybe(save_call, record)
        started = time.monotonic()
        limited_for = 0.0
        try:
            attempt = 0
            while True:
                remaining = deadline-time.monotonic()
                try:
                    result = await asyncio.wait_for(model_client.complete(copy.deepcopy(messages), remaining), remaining)
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 429 or attempt >= len(BACKOFF_SECONDS):
                        raise
                    delay = retry_delay(exc.response, attempt)
                    attempt += 1
                    limited_for += delay
                    deadline += delay  # waiting for the provider's rate limit is not the team's time
                    record.update(rate_limit_retries=attempt, rate_limit_wait_seconds=limited_for)
                    await asyncio.sleep(delay)
                    if await _maybe(should_stop):
                        raise Stopped()
            record.update(result, status='ok')
            answer = parse_answer(result['output'], public_question['question_type'], public_question['options'])
            record['answer'] = answer
            answers.append(answer)
            status = 'ok' if answer else 'invalid_output'
        except Stopped:
            raise
        except (asyncio.TimeoutError, httpx.TimeoutException):
            record.update(status='timeout', error='模型请求超时')
            answers.append(None)
            status = 'timeout'
        except httpx.HTTPStatusError as exc:
            record.update(status='api_error', http_status=exc.response.status_code,
                          error=f'API 返回 HTTP {exc.response.status_code}')
            answers.append(None)
            status = 'api_error'
        except Exception as exc:
            # Do not record provider error bodies, URLs, headers or exception text.
            record.update(status='api_error', error=f'API 请求失败 ({type(exc).__name__})')
            answers.append(None)
            status = 'api_error'
        finally:
            record['elapsed_seconds'] = time.monotonic()-started-limited_for
            await _maybe(save_call, record)
            records.append(record)
        if status == 'timeout':
            break
        if mode == 'self_refine' and record['status'] == 'ok':
            if early_stop and answers[-1] and previous == answers[-1]:
                break
            previous = answers[-1]
            messages += [{'role': 'assistant', 'content': record['output']},
                         {'role': 'user', 'content': prompt.get('review') or '请检查上一轮回答并给出最终选项。'}]
    if mode == 'majority_vote':
        legal = [tuple(a) for a in answers if a]
        if legal:
            counts = Counter(legal)
            best = max(counts.values())
            final = list(next(a for a in legal if counts[a] == best))
            status = 'ok' if status != 'timeout' else status
        else:
            final = None
    else:
        final = answers[-1] if answers else None
    # Ordinary question timeout is always incorrect, even after an earlier valid sample.
    if status == 'timeout':
        final = None
    return {'answer': final, 'status': status, 'calls': len(records),
            'elapsed_seconds': timeout-(deadline-time.monotonic())}
