"""Direct JSON submission import for the local organizer UI.

Accept one or multiple JSON files, each containing one entry, a list of entries,
or {"submissions": [...]} (flat and nested shapes supported). Data passes
through the existing atomic preview/confirm importer, preserving validation,
credential encryption and duplicate/version semantics.
"""
import csv
import io
import json
from typing import Any
from fastapi import APIRouter, File, UploadFile
from app.services import submissions as subs

router = APIRouter(prefix='/api')
MAX_FILES = 100
MAX_BYTES = 8_000_000


def _value(record: dict, key: str, *nested_paths: tuple[str, str]):
    if key in record:
        return record[key]
    for parent, field in nested_paths:
        container = record.get(parent)
        if isinstance(container, dict) and field in container:
            return container[field]
    return ''


def normalize_json_entries(payload: Any) -> list[dict[str, str]]:
    """Map a participant JSON document to the existing CSV/XLSX contract."""
    if isinstance(payload, dict) and 'submissions' in payload:
        payload = payload['submissions']
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not payload:
        raise ValueError('JSON 应包含一份队伍配置、非空队伍数组，或 submissions 数组')
    normalized = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError('每份参赛方案必须是 JSON 对象')
        data = {
            'team_id': _value(item, 'team_id'),
            'team_name': _value(item, 'team_name'),
            'submission_version': _value(item, 'submission_version'),
            'api_base_url': _value(item, 'api_base_url', ('api', 'base_url'), ('model', 'base_url')),
            'api_key': _value(item, 'api_key', ('api', 'api_key')),
            'api_key_env': _value(item, 'api_key_env', ('api', 'api_key_env')),
            'model_id': _value(item, 'model_id', ('api', 'model_id'), ('model', 'model_id')),
            'is_aggregator': _value(item, 'is_aggregator', ('api', 'is_aggregator'), ('model', 'is_aggregator')),
            'prompt_system': _value(item, 'prompt_system', ('prompts', 'system')),
            'prompt_initial': _value(item, 'prompt_initial', ('prompts', 'initial')),
            'prompt_review': _value(item, 'prompt_review', ('prompts', 'review')),
            'temperature': _value(item, 'temperature', ('inference', 'temperature'), ('model', 'temperature')),
            'max_tokens': _value(item, 'max_tokens', ('inference', 'max_tokens'), ('model', 'max_tokens')),
            'provider': _value(item, 'provider', ('api', 'provider'), ('model', 'provider')),
        }
        # Python bool must serialize to lower-case to match the existing parser.
        out = {}
        for key, value in data.items():
            if value is None:
                value = ''
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError('配置字段只能填写字符串、数字或布尔值')
            out[key] = ('true' if value else 'false') if isinstance(value, bool) else str(value)
        normalized.append(out)
    return normalized


def merge_json_files(files: list[tuple[str, bytes]]) -> bytes:
    if not files or len(files) > MAX_FILES:
        raise ValueError('请提交 1～100 个 JSON 文件')
    records = []
    total = 0
    for filename, raw in files:
        if not filename.lower().endswith('.json'):
            raise ValueError('只能上传 .json 格式的参赛配置')
        total += len(raw)
        if total > MAX_BYTES:
            raise ValueError('JSON 文件总大小不能超过 8MB')
        try:
            parsed = json.loads(raw.decode('utf-8-sig'))
        except (UnicodeError, ValueError):
            raise ValueError('JSON 语法或字符编码错误，请使用 UTF-8；不回显文件内容') from None
        records.extend(normalize_json_entries(parsed))
        if len(records) > MAX_FILES:
            raise ValueError('每次最多导入 100 份参赛方案')
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=subs.FIELDS, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue().encode('utf-8-sig')


@router.get('/submissions/json/example')
def json_example():
    return {
        'team_id': 'team-001',
        'team_name': '参赛队伍名称',
        'submission_version': 'v1',
        'api': {
            'base_url': 'https://api.example.com/v1',
            'api_key': '',
            'model_id': 'MODEL_ID',
            'is_aggregator': False,
            'provider': 'openai',
        },
        'prompts': {
            'system': '请填写总体角色与规则',
            'initial': '请填写初次推理提示词',
            'review': '请填写复核提示词',
        },
        'inference': {'temperature': 0, 'max_tokens': 1024},
    }


@router.post('/submissions/json/import')
async def import_json(files: list[UploadFile] = File(...)):
    if not 1 <= len(files) <= MAX_FILES:
        raise ValueError('请选择 1～100 个 JSON 文件')
    content = []
    size = 0
    for file in files:
        raw = await file.read(MAX_BYTES + 1 - size)
        size += len(raw)
        if size > MAX_BYTES:
            raise ValueError('JSON 文件总大小不能超过 8MB')
        content.append((file.filename or '', raw))
    csv_bytes = merge_json_files(content)
    # One atomic transaction for the complete import; no real API calls.
    import_preview = subs.preview_import(csv_bytes, 'entries.csv')
    if import_preview['errors']:
        # Never include raw cells, keys or prompts in errors.
        return {
            'ok': False, 'imported': 0,
            'duplicates': import_preview['duplicates'],
            'errors': import_preview['errors'],
            'items': [
                {'row': row['row'], 'errors': row.get('errors', []), 'status': row['status']}
                for row in import_preview['items'] if row['status'] == 'error'
            ],
        }
    result = subs.confirm_import(import_preview['preview_id'])
    return {'ok': True, **result}
