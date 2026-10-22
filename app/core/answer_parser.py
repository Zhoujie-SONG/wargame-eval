import json
import re


def parse_answer(output, question_type='single_choice', options=None):
    allowed = set(options or 'ABCDEFGH')
    raw = output.strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            value = value.get('answer', value.get('final_answer'))
        if isinstance(value, list):
            if not all(isinstance(v, str) and re.fullmatch('[A-H]', v.strip().upper()) for v in value):
                return None
            raw = ','.join(value)
        elif isinstance(value, str):
            raw = value
        else:
            return None
    except (ValueError, TypeError):
        pass
    raw = raw.strip().upper()
    if not re.fullmatch(r'[A-H](?:[\s,，、;；]*[A-H])*', raw):
        return None
    letters = re.findall('[A-H]', raw)
    if not set(letters) <= allowed or len(letters) != len(set(letters)):
        return None
    if question_type == 'single_choice' and len(letters) != 1:
        return None
    return sorted(letters)
