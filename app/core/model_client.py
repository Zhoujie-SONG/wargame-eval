import hashlib
import json
from typing import Protocol
import httpx
from app.core.security import SafeNetworkBackend, redact


class ModelClient(Protocol):
    async def complete(self, messages: list, timeout: float) -> dict: ...


class OpenAIClient:
    """One complete() sends exactly one request; redirects and retries are disabled."""
    def __init__(self, model, key='', transport=None):
        self.model, self.key, self.transport = model, key, transport

    async def complete(self, messages, timeout):
        if self.transport is not None:
            transport = self.transport
        elif self.model['provider'] == 'mock':
            transport = httpx.MockTransport(mock_completion)
        else:
            transport = httpx.AsyncHTTPTransport(retries=0)
            transport._pool._network_backend = SafeNetworkBackend()
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False, timeout=timeout) as client:
            url = 'https://mock.invalid/v1' if self.model['provider'] == 'mock' else self.model['base_url']
            response = await client.post(url+'/chat/completions', headers={'Authorization': 'Bearer '+self.key},
                                         json={'model': self.model['model_id'], 'messages': messages,
                                               'temperature': self.model.get('temperature', 0),
                                               'max_tokens': self.model.get('max_tokens', 1024)})
            response.raise_for_status()
            body = response.json()
            output = body['choices'][0]['message']['content']
            if not isinstance(output, str) or len(output) > 200_000:
                raise ValueError('模型返回内容格式错误或过长')
            usage = body.get('usage') or {}
            usage = {k: v for k, v in usage.items() if k in ('prompt_tokens', 'completion_tokens', 'total_tokens') and isinstance(v, int) and v >= 0}
            return {'output': redact(output, self.key), 'usage': usage}


def mock_completion(request):
    """OpenAI-shaped in-memory HTTP endpoint. Never reads GT or the database."""
    payload = json.loads(request.content)
    text = json.dumps(payload['messages'][0:2], ensure_ascii=False, sort_keys=True)
    letter = 'ABCD'[int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) % 4]
    return httpx.Response(200, json={'choices': [{'message': {'content': letter}}],
                                    'usage': {'prompt_tokens': 100, 'completion_tokens': 1, 'total_tokens': 101}})


def make_client(model, key):
    # Register future provider adapters here, while keeping the same budget wrapper.
    return OpenAIClient(model, key)
