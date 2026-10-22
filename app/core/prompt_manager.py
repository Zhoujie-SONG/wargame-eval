import base64
import hashlib
from app.db import dumps
from app.settings import HOME


def version(prompt):
    return hashlib.sha256(dumps(prompt).encode()).hexdigest()[:16]


def initial_messages(public_question, prompt):
    # This function only receives allowlisted public fields. No GT or annotation access.
    content = [{'type': 'text', 'text': public_question['input_text']}]
    image = public_question.get('image')
    if image:
        raw = (HOME / 'images' / image['id']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != image['id']:
            raise ValueError('截图内容哈希与冻结版本不符')
        data = base64.b64encode(raw).decode()
        content.append({'type': 'image_url', 'image_url': {'url': f'data:{image["mime"]};base64,{data}'}})
    messages = []
    if prompt.get('system'):
        messages.append({'role': 'system', 'content': prompt['system']})
    if prompt.get('initial'):
        content.insert(0, {'type': 'text', 'text': prompt['initial']})
    messages.append({'role': 'user', 'content': content})
    return messages
