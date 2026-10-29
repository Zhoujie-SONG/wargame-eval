"""Local JSON import must be complete, reproducible and safe for model input."""
import copy
import hashlib
import io
import json
import shutil

import pytest
import yaml
from PIL import Image

from app.core.prompt_manager import initial_messages
from app.db import db, dumps, question_rows
from app.services.runs import dataset_digest


def _png(color):
    stream = io.BytesIO()
    Image.new('RGB', (24, 24), color).save(stream, format='PNG')
    return stream.getvalue()


@pytest.fixture
def local_data(client, tmp_path):
    from app import settings
    config = yaml.safe_load(settings.CONFIG.read_text(encoding='utf-8'))
    config['dataset']['expected_counts'] = {'L2_main': 1, 'L3_main': 1, 'L1_sub': 1, 'L2_sub': 1}
    settings.CONFIG.write_text(yaml.safe_dump(config), encoding='utf-8')
    folder = tmp_path / '原始 数据'
    folder.mkdir()
    images = folder / 'images'
    images.mkdir()
    rows = []
    for i, levels in enumerate((('L2', 'L1'), ('L3', 'L2')), 1):
        sid = f'G01-{i:02}'
        for role, code, level in (('core', 'CORE', levels[0]), ('subquestion', 'P1', levels[1])):
            text = f'只看当前图像作答，{sid} {code}。\n\nA. 甲方\nB. 乙方\n'
            rows.append(dict(
                id=f'{sid}-{code}', sample_id=sid, group_id='G01', source_sample_id=f'source-{i}',
                role=role, code=code, level=level, type='single_choice',
                replay={'id': f'replay-{i}', 'step': i, 'map_id': '21'},
                image={'path': f'images/G01/{sid}.png'},
                model_input=text, question='请选择', options={'A': '甲方', 'B': '乙方'},
                answer={'label': 'A', 'labels': ['A'], 'source_markdown': 'SECRET_GT 答案 A。\n'},
                review_markdown='SECRET_REVIEW 仅审核参考。',
                source={'file': 'source/questions.md', 'model_input_sha256': hashlib.sha256(text.encode()).hexdigest()},
            ))
        (images / f'{sid}_{sid}_step{i}_12345678-1234-1234-1234-1234567890ab.png').write_bytes(
            _png('navy' if i == 1 else 'green'))
    qa = folder / 'qa_all.json'
    qa.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    return dict(client=client, rows=rows, qa=qa, images=images, data=settings.HOME)


def _post(data, rows=None):
    if rows is not None:
        data['qa'].write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    return data['client'].post('/api/dataset/import-local', json={
        'qa_path': str(data['qa']), 'images_dir': str(data['images']),
    })


def _assert_empty(data):
    with db() as connection:
        for table in ('questions', 'images', 'image_map'):
            assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    assert not list((data['data'] / 'raw').iterdir())
    assert not list((data['data'] / 'images').iterdir())


def test_local_complete_idempotent_and_private_reference(local_data):
    data = local_data
    response = _post(data)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['total'] == 4 and result['mapped_frames'] == result['unique_images'] == 2
    assert result['counts'] == {'L2_main': 1, 'L3_main': 1, 'L1_sub': 1, 'L2_sub': 1}
    assert result['already_imported'] is False
    stats = data['client'].get('/api/dataset/stats').json()
    assert stats['real_image_questions'] == 4 and stats['missing_image_questions'] == 0
    for original in data['rows']:
        question = data['client'].get('/api/questions/' + original['id']).json()
        assert question['input_text'] == original['model_input']
        assert question['input_sha256'] == original['source']['model_input_sha256']
        assert question['reference_text'] == original['answer']['source_markdown'] + original['review_markdown']
        assert question['parent_id'] == (None if original['role'] == 'core' else original['sample_id'] + '-CORE')
        image = question['image']
        assert image['real'] == 1
        assert data['client'].get('/api/images/' + image['id']).status_code == 200
        messages = json.dumps(initial_messages(question, {}), ensure_ascii=False)
        assert 'SECRET_GT' not in messages and 'SECRET_REVIEW' not in messages
    assert _post(data).json()['already_imported'] is True
    assert (data['data'] / 'raw' / (result['archive_sha256'] + '.json')).read_bytes() == data['qa'].read_bytes()
    manifest = json.loads((data['data'] / 'processed/local_image_map.json').read_text(encoding='utf-8'))
    assert len(manifest) == 2
    remembered = data['client'].get('/api/dataset/local-source').json()
    assert remembered == {'qa_path': str(data['qa']), 'images_dir': str(data['images'])}
    # Moving the same original data does not change stored question identity.
    moved = data['qa'].parent.parent / 'moved'
    shutil.copytree(data['qa'].parent, moved)
    response = data['client'].post('/api/dataset/import-local', json={
        'qa_path': str(moved / 'qa_all.json'), 'images_dir': str(moved / 'images')})
    assert response.status_code == 200 and response.json()['already_imported'] is True


@pytest.mark.parametrize('failure', ['missing', 'ambiguous', 'invalid_image', 'gt', 'parent', 'missing_parent',
                                    'sha', 'options', 'duplicate', 'count', 'unsafe_path'])
def test_local_failed_batch_has_no_partial_state(local_data, failure):
    data, rows = local_data, copy.deepcopy(local_data['rows'])
    first = next(data['images'].glob('G01-01*.png'))
    if failure == 'missing':
        first.unlink()
    elif failure == 'ambiguous':
        (data['images'] / 'G01-01_G01-01_step1_abcdefab-1234-1234-1234-1234567890ab.png').write_bytes(first.read_bytes())
    elif failure == 'invalid_image':
        first.write_bytes(b'not an image')
    elif failure == 'gt':
        rows[0]['answer']['labels'] = ['H']
    elif failure == 'parent':
        rows[1]['replay']['step'] = 99
    elif failure == 'missing_parent':
        rows[0]['role'] = 'subquestion'
    elif failure == 'sha':
        rows[0]['source']['model_input_sha256'] = '0' * 64
    elif failure == 'options':
        rows[0]['options']['A'] = '与输入不同'
    elif failure == 'duplicate':
        rows[1]['id'] = rows[0]['id']
    elif failure == 'count':
        rows[0]['level'] = 'L3'
    elif failure == 'unsafe_path':
        for row in rows[:2]:
            row['image']['path'] = '../outside.png'
    response = _post(data, rows)
    assert response.status_code == 400, response.text
    _assert_empty(data)
    assert data['client'].get('/api/dataset/local-source').json() == {'qa_path': '', 'images_dir': ''}


@pytest.mark.parametrize('naming', ['canonical', 'replay_step', 'replay'])
def test_local_exact_alternative_names(local_data, naming):
    data = local_data
    for i in (1, 2):
        sid = f'G01-{i:02}'
        original = next(data['images'].glob(f'{sid}_*.png'))
        if naming == 'canonical':
            target = data['images'] / 'G01' / f'{sid}.png'
            target.parent.mkdir(exist_ok=True)
        else:
            target = data['images'] / (f'replay-{i}_step{i}.png' if naming == 'replay_step' else f'replay-{i}.png')
        original.rename(target)
    response = _post(data)
    assert response.status_code == 200, response.text
    assert response.json()['mapped_frames'] == 2


def test_local_cannot_replace_existing_questions_or_images(local_data):
    data = local_data
    assert _post(data).status_code == 200
    original_stats = data['client'].get('/api/dataset/stats').json()
    modified = copy.deepcopy(data['rows'])
    modified[0]['answer'] = {'label': 'B', 'labels': ['B'], 'source_markdown': 'Different reference'}
    assert _post(data, modified).status_code == 400
    assert data['client'].get('/api/dataset/stats').json() == original_stats
    original_image = data['client'].get('/api/questions/G01-01-CORE').json()['image']['id']
    next(data['images'].glob('G01-01*.png')).write_bytes(_png('orange'))
    assert _post(data, data['rows']).status_code == 400
    assert data['client'].get('/api/questions/G01-01-CORE').json()['image']['id'] == original_image


@pytest.mark.parametrize('first_entry', ['paths', 'workspace', 'upload'])
def test_import_entry_points_preserve_existing_schema_and_digest(local_data, monkeypatch, first_entry):
    """The three entry points must never migrate an unchanged local dataset."""
    import app.main as main
    data, client = local_data, local_data['client']
    monkeypatch.setattr(main, 'ROOT', data['qa'].parent)
    (data['images'] / 'qa_all.json').write_bytes(data['qa'].read_bytes())

    def post(entry):
        if entry == 'paths':
            return _post(data)
        if entry == 'workspace':
            return client.post('/api/dataset/import-local')
        files = [('images', ('images/' + image.name, image.read_bytes(), 'image/png'))
                 for image in sorted(data['images'].glob('*.png'))]
        files.append(('qa', ('qa_all.json', data['qa'].read_bytes(), 'application/json')))
        return client.post('/api/dataset/import-upload', files=files)

    first = post(first_entry)
    assert first.status_code == 200, first.text
    # This is the row shape already stored by the desktop version before merging.
    legacy_rows = []
    for raw in data['rows']:
        role = 'main' if raw['role'] == 'core' else 'sub'
        legacy_rows.append(dict(
            question_id=raw['id'], parent_id=None if role == 'main' else raw['sample_id'] + '-CORE',
            sample_id=raw['sample_id'], replay_id=raw['replay']['id'], image_step=raw['replay']['step'],
            image_path=raw['image']['path'], level=raw['level'], role=role, question_type=raw['type'],
            input_text=raw['model_input'], gt_answer=raw['answer']['labels'], options=['A', 'B'],
            reference_text=raw['answer']['source_markdown'] + raw['review_markdown'],
            source=raw['source']['file'], input_sha256=raw['source']['model_input_sha256'],
        ))
    legacy_rows.sort(key=lambda question: question['question_id'])
    assert question_rows() == legacy_rows
    frozen_digest = dataset_digest(legacy_rows)
    frozen = dumps({'questions': legacy_rows, 'dataset_sha256': frozen_digest})
    with db() as connection:
        before = [tuple(row) for row in connection.execute('SELECT * FROM questions ORDER BY question_id')]
        maps = [tuple(row) for row in connection.execute('SELECT * FROM image_map ORDER BY replay_id,image_step')]
        connection.execute('INSERT INTO runs(id,status,created_at,body) VALUES(?,?,?,?)',
                           ('historic-local-run', 'completed', '2026-10-27T00:00:00+00:00', frozen))

    for entry in ('paths', 'workspace', 'upload'):
        response = post(entry)
        assert response.status_code == 200, response.text
        result = response.json()
        if entry == 'paths':
            assert result['already_imported'] is True
        else:
            assert result['changed'] is False and result['backup'] is None and result['changed_inputs'] == 0
        assert dataset_digest(question_rows()) == frozen_digest
        with db() as connection:
            assert [tuple(row) for row in connection.execute('SELECT * FROM questions ORDER BY question_id')] == before
            assert [tuple(row) for row in connection.execute('SELECT * FROM image_map ORDER BY replay_id,image_step')] == maps
            assert connection.execute('SELECT body FROM runs WHERE id=?', ('historic-local-run',)).fetchone()[0] == frozen
    assert not list((data['data'] / 'raw').glob('dataset-backup-*.json'))


@pytest.mark.parametrize('status', ['running', 'queued', 'stopping'])
@pytest.mark.parametrize('existing_dataset', [False, True])
def test_path_import_blocks_active_runs_without_changing_dataset(local_data, status, existing_dataset):
    data = local_data
    if existing_dataset:
        assert _post(data).status_code == 200
    with db() as connection:
        before = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
                  for table in ('questions', 'images', 'image_map')}
        connection.execute('INSERT INTO runs(id,status,created_at,body) VALUES(?,?,?,?)',
                           ('active-run', status, 'now', dumps({})))
    files_before = {path.relative_to(data['data']): path.read_bytes()
                    for folder in ('raw', 'images', 'processed')
                    for path in (data['data'] / folder).rglob('*') if path.is_file()}
    response = _post(data)
    assert response.status_code == 400 and '运行或排队' in response.json()['detail']
    with db() as connection:
        for table, rows in before.items():
            assert [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')] == rows
    assert {path.relative_to(data['data']): path.read_bytes()
            for folder in ('raw', 'images', 'processed')
            for path in (data['data'] / folder).rglob('*') if path.is_file()} == files_before
