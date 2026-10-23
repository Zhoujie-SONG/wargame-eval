"""State of the four official-competition steps, decided once on the server.

The sidebar navigation, the per-page status line and the gated buttons only display this
result; the browser never re-implements these rules.

Check levels: ok (satisfied) / info (neutral note) / warn (can proceed, needs a look) /
bad (cannot proceed). A step is blocked by any bad check, warning on any warn check.
"""
from app.db import db
from app.services import competition as comp
from app.services import submissions as subs
from app.services.dataset import overview

ORDER = ('dataset', 'submissions', 'batches', 'analytics')
STATE_OF = {'bad': 'blocked', 'warn': 'warning'}
RUN_LABEL = {'interrupted': '已中断', 'infrastructure_error': '平台故障', 'stopped': '已停止'}


def _check(level, text):
    return {'level': level, 'text': text}


def _names(names, limit=5):
    shown = '、'.join(names[:limit])
    return shown + (f' 等 {len(names)} 支' if len(names) > limit else '')


def _step(key, checks, done_message, state=None):
    levels = {c['level'] for c in checks}
    state = state or ('blocked' if 'bad' in levels else 'warning' if 'warn' in levels else 'done')
    missing = [c['text'] for c in checks if c['level'] in ('bad', 'warn')]
    if state in ('done', 'todo', 'running') and not missing:
        message = done_message
    elif missing:
        message = missing[0] + (f'（另有 {len(missing) - 1} 项）' if len(missing) > 1 else '')
    else:
        message = done_message
    return {'key': key, 'state': state, 'message': message, 'missing': missing, 'checks': checks}


def _dataset():
    d = overview()
    exp_total = sum(d['expected'].values())
    checks = []
    if d['total'] == 0:
        checks.append(_check('bad', '尚未导入题库'))
    else:
        total_ok = d['total'] == exp_total
        checks.append(_check('ok' if total_ok else 'bad',
                             f"题库共 {d['total']:,} 题" + ('' if total_ok else f'，应为 {exp_total:,} 题')))
        for name, want in d['expected'].items():
            if d['counts'][name] != want:
                checks.append(_check('bad', f"{name} 应为 {want} 题，当前 {d['counts'][name]} 题"))
        missing = d['missing_image_questions']
        checks.append(_check('ok', '截图全部关联真实图片') if not missing
                      else _check('bad', f'{missing:,} 题缺少真实截图'))
    return _step('dataset', checks, f"{d['total']:,} 题齐全，截图全部关联")


def _submissions(all_subs, roster):
    real = [s for s in all_subs if s['provider'] != 'mock']
    if not real:
        return _step('submissions', [_check('bad', '还没有导入参赛配置')], '', state='todo')
    selected = [s for s in real if s['id'] in roster]
    if not selected:
        return _step('submissions', [_check('bad', '名单为空，请至少选择一支队伍')], '')
    checks = [_check('ok', f'已选 {len(selected)} 支队伍')]
    broken = [s['team_name'] for s in selected if not s['available']]
    if broken:
        checks.append(_check('bad', f'模型或 Prompt 缺失：{_names(broken)}'))
    nocred = [s['team_name'] for s in selected if not s['credential_provided']]
    checks.append(_check('bad', f'未提供凭据：{_names(nocred)}') if nocred else _check('ok', '凭据齐全'))
    outdated = [f"{s['team_name']}（{s['submission_version']}→{s['latest_version']}）"
                for s in selected if not s['is_latest']]
    checks.append(_check('warn', f'有更新版本未选中：{_names(outdated)}') if outdated
                  else _check('ok', '均为最新版本'))
    chosen_teams = {s['team_id'] for s in selected}
    left_out = sorted({s['team_name'] for s in real if s['team_id'] not in chosen_teams})
    if left_out:
        checks.append(_check('info', f'另有 {len(left_out)} 支已导入队伍未参赛：{_names(left_out)}'))
    return _step('submissions', checks, f'已选 {len(selected)} 支队伍，凭据齐全，均为最新版本')


def _current_batch():
    """Latest started official batch with its per-run states, or None."""
    for b in comp.list_batches():
        if b['started'] and b['plan'].get('official'):
            return comp.get_batch(b['batch_id'])
    return None


def compute():
    with db() as c:
        _, roster_list = subs.roster_ids(c)
    roster = set(roster_list)
    dataset, submissions = _dataset(), _submissions(subs.list_submissions(), roster)
    batch = _current_batch()
    running = bool(batch and batch['status'] == 'running')

    # 03: ready only when both earlier steps can proceed.
    pre = []
    for step, label in ((dataset, '01 数据集'), (submissions, '02 参赛名单')):
        if step['state'] in ('blocked', 'todo'):
            pre.append(_check('bad', f"{label}未就绪：{step['missing'][0]}"))
        elif step['state'] == 'warning':
            pre.append(_check('warn', f"{label}有提示：{step['missing'][0]}"))
        else:
            pre.append(_check('ok', f'{label}就绪'))
    if running:
        launch = _step('batches', pre, '比赛进行中，设置已冻结', state='running')
    elif any(c['level'] == 'bad' for c in pre):
        launch = _step('batches', pre, '')
    elif batch:
        launch = _step('batches', pre, '本轮已启动；可在此开始新一轮', state='done')
    else:
        launch = _step('batches', pre, '设置策略与次数后即可开始', state='todo')

    # 04: follows the current batch.
    if not batch:
        monitor = _step('analytics', [], '尚未开始正式比赛', state='todo')
    else:
        runs = batch['runs']
        flagged = [r for r in runs if r['status'] in RUN_LABEL]
        done = sum(r['completed'] for r in runs)
        total = sum(r['total'] for r in runs) or 1
        if flagged:
            checks = [_check('warn', f"{r['name']}：{RUN_LABEL[r['status']]}") for r in flagged]
            monitor = _step('analytics', checks, '', state='warning')
        elif running:
            monitor = _step('analytics', [], f'比赛进行中：{done:,} / {total:,} 题', state='running')
        elif runs and all(r['status'] == 'completed' for r in runs):
            monitor = _step('analytics', [], f"本轮已完成：{len(runs)} 支队伍", state='done')
        else:
            monitor = _step('analytics', [], '本批次尚未启动', state='todo')

    steps = [dataset, submissions, launch, monitor]
    by_key = {s['key']: s for s in steps}
    if running or monitor['state'] == 'warning':
        suggested = 'analytics'
    else:
        suggested = next((k for k in ORDER if by_key[k]['state'] != 'done'), 'analytics')
    return {'steps': steps, 'current_batch_id': batch['batch_id'] if batch else None,
            'locked': running, 'suggested_tab': suggested}
