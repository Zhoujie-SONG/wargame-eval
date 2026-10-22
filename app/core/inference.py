import asyncio
import copy
import time
from collections import Counter
import httpx
from app.core.answer_parser import parse_answer


class Stopped(Exception):
    pass


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
        if should_stop():
            raise Stopped()
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            status = 'timeout'
            break
        # All attempts, including HTTP errors/timeouts, consume a slot before sending.
        record = {'ordinal': ordinal, 'status': 'sending', 'elapsed_seconds': 0, 'output': '', 'usage': {}}
        save_call(record)
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(model_client.complete(copy.deepcopy(messages), remaining), remaining)
            record.update(result, status='ok')
            answer = parse_answer(result['output'], public_question['question_type'], public_question['options'])
            record['answer'] = answer
            answers.append(answer)
            status = 'ok' if answer else 'invalid_output'
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
            record['elapsed_seconds'] = time.monotonic()-started
            save_call(record)
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
