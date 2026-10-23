"""Dataset readiness numbers shared by /api/dataset/stats and the flow status."""
from app import settings as S
from app.db import question_rows
from app.services.runs import get_images


def overview():
    # HOME / CONFIG are read through the module at call time so tests can redirect them.
    expected = S.settings()['dataset']['expected_counts']
    questions = question_rows()
    images = get_images()
    counts = {k: sum(q['level'] + '_' + q['role'] == k for q in questions) for k in expected}
    ready = sum(bool((v := images.get((q['replay_id'], q['image_step']))) and v['real']
                     and (S.HOME / 'images' / v['id']).is_file()) for q in questions)
    frames = {(q['replay_id'], q['image_step']) for q in questions}
    return {'total': len(questions), 'counts': counts, 'expected': expected,
            'real_image_questions': ready, 'missing_image_questions': len(questions) - ready,
            'unique_frames': len(frames), 'mapped_frames': len(set(images) & frames)}
