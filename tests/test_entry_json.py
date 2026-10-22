import csv
import io
import json
import pytest
from app.api.entry_json import normalize_json_entries, merge_json_files


def example(team_id):
    return {
        'team_id': team_id, 'team_name': f'队伍 {team_id}',
        'submission_version': 'v1',
        'api': {'base_url': 'https://example.com/v1', 'api_key': 'DUMMY-ONLY',
                'model_id': 'visual-model', 'is_aggregator': False, 'provider': 'openai'},
        'prompts': {'system': '系统\n第二行', 'initial': '第一次, 判断', 'review': '核对"证据"'},
        'inference': {'temperature': 0, 'max_tokens': 512}
    }


def test_nested_json_preserves_chinese_and_multiline():
    entries = normalize_json_entries(example('one'))
    assert entries[0]['prompt_system'] == '系统\n第二行'
    assert entries[0]['prompt_review'] == '核对"证据"'
    assert entries[0]['is_aggregator'] == 'false'


def test_multiple_files_are_one_import():
    a = json.dumps(example('one'),ensure_ascii=False).encode()
    b = json.dumps({'submissions': [example('two')]},ensure_ascii=False).encode()
    csv_bytes = merge_json_files([('1.json',a),('2.json',b)])
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode('utf-8-sig'))))
    assert len(rows) == 2
    assert rows[1]['team_id'] == 'two'
    assert rows[0]['prompt_system'] == '系统\n第二行'


def test_invalid_json_and_unsafe_nested_types():
    with pytest.raises(ValueError): merge_json_files([('a.txt',b'{}')])
    with pytest.raises(ValueError): merge_json_files([('a.json',b'{not json')])
    with pytest.raises(ValueError): normalize_json_entries({'team_id':{'bad':'type'}})


def test_size_bound():
    with pytest.raises(ValueError): merge_json_files([('a.json',b' ' * 8_000_001)])
