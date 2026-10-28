import copy
import hashlib
import io
import json
import time
from pathlib import Path

import pytest
import yaml
from PIL import Image

from app.db import db, dumps, question_rows
from app import settings as S


def write_json(folder, rows):
    (folder / 'qa_all.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def local_dataset(client, tmp_path, monkeypatch):
    import app.main as main
    root = tmp_path / 'workspace'
    folder = root / 'images'
    folder.mkdir(parents=True)
    monkeypatch.setattr(main, 'ROOT', root)
    config = yaml.safe_load(S.CONFIG.read_text(encoding='utf-8'))
    config['dataset']['expected_counts'] = dict(L2_main=1, L3_main=1, L1_sub=1, L2_sub=1)
    S.CONFIG.write_text(yaml.safe_dump(config), encoding='utf-8')
    rows = []
    for sample, step, level, role, suffix in [
        ('G01-01', 0, 'L2', 'core', 'CORE'), ('G01-01', 0, 'L1', 'subquestion', 'P1'),
        ('G02-01', 120, 'L3', 'core', 'CORE'), ('G02-01', 120, 'L2', 'subquestion', 'C1')]:
        text = '完整题目，保留尾部换行。\nA. 甲\nB. 乙\n'
        rows.append(dict(id=sample + '-' + suffix, sample_id=sample, level=level, role=role,
                         replay=dict(id='replay-' + sample, step=step),
                         image=dict(path='images/' + sample[:3] + '/' + sample + '.png'),
                         model_input=text, options={'A': '甲', 'B': '乙'},
                         type='single_choice', answer={'labels': ['A']}, review_markdown='隐藏标注',
                         source={'model_input_sha256': hashlib.sha256(text.encode()).hexdigest()}))
    for sample, step, color in [('G01-01', 0, 'navy'), ('G02-01', 120, 'orange')]:
        Image.new('RGB', (32, 20), color).save(folder / f'{sample}_{sample}_step{step}_abc-123.png')
    write_json(folder, rows)
    return client, folder, rows


def test_one_click_import_and_idempotency(local_dataset):
    c, folder, rows = local_dataset
    response = c.post('/api/dataset/import-local')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['total'] == 4 and result['mapped_frames'] == 2 and result['backup'] is None
    stats = c.get('/api/dataset/stats').json()
    assert stats['missing_image_questions'] == 0 and stats['real_image_questions'] == 4
    questions = {q['question_id']: q for q in question_rows()}
    for raw in rows:
        q = questions[raw['id']]
        assert q['input_text'] == raw['model_input']
        assert q['input_sha256'] == raw['source']['model_input_sha256']
        assert q['gt_answer'] == raw['answer']['labels']
        assert q['parent_id'] == (raw['sample_id'] + '-CORE' if raw['role'] == 'subquestion' else None)
    core = c.get('/api/questions/G01-01-CORE').json()
    sub = c.get('/api/questions/G01-01-P1').json()
    assert core['image'] == sub['image'] and core['image']['real']
    assert c.get('/api/images/' + core['image']['id']).status_code == 200
    repeated = c.post('/api/dataset/import-local').json()
    assert not repeated['changed'] and repeated['backup'] is None
    assert len(c.get('/api/images').json()) == 2


@pytest.mark.parametrize('failure', ['missing', 'ambiguous', 'corrupt', 'metadata', 'duplicate',
                                     'parent', 'hash', 'counts', 'traversal', 'answers'])
def test_invalid_import_is_atomic(local_dataset, failure):
    c, folder, rows = local_dataset
    assert c.post('/api/dataset/import-local').status_code == 200
    before = question_rows()
    with db() as con:
        maps = [tuple(r) for r in con.execute('SELECT * FROM image_map ORDER BY replay_id,image_step')]
        image_count = con.execute('SELECT COUNT(*) FROM images').fetchone()[0]
    path = next(folder.glob('G02-01_*.png'))
    if failure == 'missing':
        path.unlink()
    elif failure == 'ambiguous':
        path.with_name(path.name.replace('abc-123', 'def-456')).write_bytes(path.read_bytes())
    elif failure == 'corrupt':
        path.write_bytes(b'not a PNG')
    elif failure == 'metadata':
        rows[1]['replay']['step'] = 99
    elif failure == 'duplicate':
        rows.append(copy.deepcopy(rows[0]))
    elif failure == 'parent':
        rows[1]['sample_id'] = 'G03-01'
    elif failure == 'hash':
        rows[0]['model_input'] += '修改'
    elif failure == 'counts':
        rows.pop()
    elif failure == 'traversal':
        rows[0]['image']['path'] = '../outside.png'
    elif failure == 'answers':
        rows[0]['answer']['labels'] = ['Z']
    write_json(folder, rows)
    assert c.post('/api/dataset/import-local').status_code == 400
    assert question_rows() == before
    with db() as con:
        assert [tuple(r) for r in con.execute('SELECT * FROM image_map ORDER BY replay_id,image_step')] == maps
        assert con.execute('SELECT COUNT(*) FROM images').fetchone()[0] == image_count


def test_exact_image_paths_and_mock_source_protection(local_dataset):
    c, folder, rows = local_dataset
    for sample in ('G01-01', 'G02-01'):
        path = folder / sample[:3] / (sample + '.png')
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(next(folder.glob(sample + '_*.png')).read_bytes())
    image = next(folder.glob('G01-01_*.png')).read_bytes()
    upload = c.post('/api/images', files={'file': ('test.png', image, 'image/png')}, data={'real': 'false'})
    assert upload.status_code == 200
    assert c.post('/api/dataset/import-local').status_code == 400
    assert not question_rows()
    with db() as con:
        con.execute('DELETE FROM images')
    assert c.post('/api/dataset/import-local').status_code == 200


def test_update_backup_frozen_run_and_mock_execution(local_dataset):
    c, folder, rows = local_dataset
    assert c.post('/api/dataset/import-local').status_code == 200
    launch = c.post('/api/runs', json=dict(name='JSON import integration', model_id='mock-demo',
                                          prompt_id='default', mode='single_pass', calls=1, official=False))
    assert launch.status_code == 200, launch.text
    run_id = launch.json()['run_id']
    for _ in range(200):
        result = c.get('/api/runs/' + run_id).json()
        if result['status'] == 'completed':
            break
        time.sleep(.01)
    assert result['status'] == 'completed' and result['completed'] == 4
    assert result['actual_calls'] == 4 and result['external_api_calls'] == 0
    with db() as con:
        frozen = con.execute('SELECT body FROM runs WHERE id=?', (run_id,)).fetchone()[0]
    original = copy.deepcopy(rows)
    rows[0]['model_input'] += '\n新的完整输入'
    rows[0]['source']['model_input_sha256'] = hashlib.sha256(rows[0]['model_input'].encode()).hexdigest()
    write_json(folder, rows)
    update = c.post('/api/dataset/import-local').json()
    assert update['changed'] and update['changed_inputs'] == 1 and update['backup']
    backup = json.loads((S.HOME / 'raw' / update['backup']).read_text(encoding='utf-8'))
    assert backup['questions'][0]['input_text'] == original[0]['model_input']
    assert len(backup['image_map']) == 2
    with db() as con:
        assert con.execute('SELECT body FROM runs WHERE id=?', (run_id,)).fetchone()[0] == frozen
    assert c.get('/api/runs/' + run_id).json() == result


@pytest.mark.parametrize('status', ['running', 'queued', 'stopping'])
def test_active_runs_block_import(local_dataset, status):
    c, folder, rows = local_dataset
    with db() as con:
        con.execute('INSERT INTO runs(id,status,created_at,body) VALUES(?,?,?,?)',
                    ('active', status, 'now', dumps({})))
    response = c.post('/api/dataset/import-local')
    assert response.status_code == 400 and not question_rows()
    assert not list((S.HOME / 'images').iterdir())


def test_missing_json(local_dataset):
    c, folder, rows = local_dataset
    (folder / 'qa_all.json').unlink()
    response = c.post('/api/dataset/import-local')
    assert response.status_code == 400 and 'qa_all.json' in response.json()['detail']


@pytest.mark.skipif(not (S.ROOT / 'images' / 'qa_all.json').is_file(), reason='主办方私有 images 题库未提供')
def test_organizer_full_dataset_in_isolated_database(client):
    response = client.post('/api/dataset/import-local')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['total'] == 1279 and result['mapped_frames'] == 240
    assert result['counts'] == dict(L2_main=179, L3_main=61, L1_sub=757, L2_sub=282)
    stats = client.get('/api/dataset/stats').json()
    assert stats['real_image_questions'] == 1279 and stats['missing_image_questions'] == 0
    source = json.loads((S.ROOT / 'images' / 'qa_all.json').read_text(encoding='utf-8-sig'))
    stored = {q['question_id']: q for q in question_rows()}
    for raw in source:
        q = stored[raw['id']]
        assert q['input_text'] == raw['model_input'] and q['gt_answer'] == raw['answer']['labels']
        assert q['replay_id'] == raw['replay']['id'] and q['image_step'] == raw['replay']['step']


def test_manual_upload_import_matches_workspace_import(local_dataset):
    client, folder, rows = local_dataset
    files = [('images', ('images/' + p.name, p.read_bytes(), 'image/png')) for p in sorted(folder.glob('*.png'))]
    files.append(('images', ('images/.DS_Store', b'junk', 'application/octet-stream')))
    response = client.post('/api/dataset/import-upload', files=files + [('qa', ('qa_all.json', (folder / 'qa_all.json').read_bytes()))])
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['total'] == 4 and data['mapped_frames'] == 2 and data['changed'] is True
    again = client.post('/api/dataset/import-upload', files=files + [('qa', ('qa_all.json', (folder / 'qa_all.json').read_bytes()))])
    assert again.json()['changed'] is False
    stats = client.get('/api/dataset/stats').json()
    assert stats['missing_image_questions'] == 0


def test_manual_upload_rejects_folder_without_images(local_dataset):
    client, folder, _ = local_dataset
    response = client.post('/api/dataset/import-upload', files=[
        ('images', ('images/readme.txt', b'x', 'text/plain')), ('qa', ('qa_all.json', (folder / 'qa_all.json').read_bytes()))])
    assert response.status_code == 400 and '没有' in response.json()['detail']
