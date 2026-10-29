"""Read-only, explicitly selected run comparisons; never an official leaderboard."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from app.db import db, dumps
from app.services.runs import report
from app.services.competition_report import DIMENSIONS, SVG, esc, number


COLORS = ('#2f6fa8', '#d98224', '#248267', '#8661b2',
          '#c34966', '#65758c', '#8c692d', '#2295a5')
SCOPE_NOTE = ('自选测评对比，仅供参考，不属于正式排名。可跨批次、独立任务及不同执行条件比较；'
              '综合分沿用 35% L2主 + 35% L3主 + 15% L1子 + 15% L2子，缺失维度按零贡献计分，雷达图不补零。')
PUBLIC_FIELDS = ('run_id', 'name', 'created_at', 'finished_at', 'submission_version',
                 'prompt_version', 'kind', 'mode', 'calls_per_question', 'early_stop',
                 'total', 'weighted_score')


def _records(run_ids=None):
    clause = '' if run_ids is None else 'WHERE r.id IN (' + ','.join('?' for _ in run_ids) + ')'
    with db() as connection:
        return [dict(row) for row in connection.execute(f"""
            SELECT r.id, b.name AS batch_name, m.run_id AS monitored
            FROM runs r
            LEFT JOIN batch_entries e ON e.run_id=r.id
            LEFT JOIN evaluation_batches b ON b.id=e.batch_id
            LEFT JOIN run_monitor m ON m.run_id=r.id
            {clause} ORDER BY r.created_at DESC,r.id
            """, run_ids or [])]


def _summary(record):
    # Older runs may not have a cached monitor row. The normal lightweight report
    # backfills it, so use the read-only full report for that legacy case only.
    return report(record['id'], include_results=not bool(record['monitored']))


def _reason(summary):
    if summary['status'] != 'completed':
        return '任务尚未完整完成（当前状态：' + str(summary['status']) + '）'
    if summary['total'] <= 0 or summary['completed'] != summary['total']:
        return '结果尚未覆盖全部评测题目'
    if summary['infrastructure_errors'] or (summary['kind'] == 'official' and not summary['eligible']):
        return '存在主办方资源故障或正式成绩不完整，不能参与对比'
    return ''


def _label(item):
    timestamp = item.get('finished_at') or item.get('created_at')
    try:
        date = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        date_text = date.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError, AttributeError):
        date_text = str(timestamp or '时间未知')
    version = item['submission_version'] or item['prompt_version'] or '版本未知'
    identity = item['run_id']
    short_id = identity if len(identity) <= 16 else identity[:8] + '…' + identity[-4:]
    return f'{item["name"]} · {date_text} · {version} · {short_id}'


def _public(summary, record):
    item = {key: summary.get(key) for key in PUBLIC_FIELDS}
    item['batch_name'] = record['batch_name'] or '独立测评'
    item['model_id'] = summary.get('model', {}).get('model_id')
    item['dimensions'] = {
        key: {field: summary['dimensions'][key].get(field)
              for field in ('total', 'correct', 'completed', 'accuracy')}
        for key, _ in DIMENSIONS
    }
    item['label'] = _label(item)
    return item


def candidates():
    output = []
    for record in _records():
        summary = _summary(record)
        item = _public(summary, record)
        reason = _reason(summary)
        item.update(selectable=not reason, reason=reason)
        output.append(item)
    return {'candidates': output}


def _fingerprint(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def _compatibility(run_ids):
    """Fingerprint frozen scope and settings internally, without exporting them."""
    with db() as connection:
        rows = connection.execute(
            "SELECT id,json_extract(body,'$.questions') AS questions,"
            "json_extract(body,'$.config') AS config,"
            "json_extract(body,'$.concurrency') AS concurrency FROM runs WHERE id IN ("
            + ','.join('?' for _ in run_ids) + ')', run_ids).fetchall()
    output = {}
    for row in rows:
        questions = []
        for question in json.loads(row['questions'] or '[]'):
            image = question.get('image') or {}
            questions.append({
                'id': question['question_id'], 'parent': question.get('parent_id'),
                'level': question['level'], 'role': question['role'],
                'input': hashlib.sha256(question.get('input_text', '').encode('utf-8')).hexdigest(),
                'type': question.get('question_type'), 'options': question.get('options'),
                'answer': sorted(question.get('gt_answer') or []),
                'image': (image.get('id'), image.get('real')),
            })
        questions.sort(key=lambda question: question['id'])
        config = json.loads(row['config'] or '{}')
        output[row['id']] = {
            'questions': _fingerprint(questions), 'budget': _fingerprint(config.get('competition', {})),
            'weights': _fingerprint(config.get('evaluation', {}).get('weights', {})),
            'concurrency': row['concurrency'] if row['concurrency'] is not None else 1,
        }
    return output


def compare(run_ids):
    if not 2 <= len(run_ids) <= 8:
        raise ValueError('请选择 2～8 个测评任务进行对比')
    if any(not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', identity)
           for identity in run_ids):
        raise ValueError('测评任务 ID 格式无效，请从候选列表重新选择')
    if len(set(run_ids)) != len(run_ids):
        raise ValueError('不能重复选择同一个测评任务')
    records = _records(run_ids)
    if len(records) != len(run_ids):
        raise ValueError('部分测评任务不存在，请刷新候选列表后重新选择')
    summaries, entries = [], []
    for record in records:
        summary = _summary(record)
        reason = _reason(summary)
        if reason:
            raise ValueError(f'{summary["name"]}：{reason}')
        summaries.append(summary)
        entries.append(_public(summary, record))
    entries.sort(key=lambda entry: (-entry['weighted_score'], entry['run_id']))
    colors = {identity: COLORS[index] for index, identity in enumerate(sorted(run_ids))}
    rank, previous = 0, None
    for index, entry in enumerate(entries, 1):
        if entry['weighted_score'] != previous:
            rank = index
        entry.update(rank=rank, color=colors[entry['run_id']])
        previous = entry['weighted_score']

    compatibility = _compatibility(run_ids)
    warnings = []
    checks = [
        ('kind', '测评类型不同（含正式、局部测试或 Mock），成绩仅供自选参考。'),
        ('mode', '推理模式不同，结果不能视为相同执行条件下的正式排名。'),
        ('calls_per_question', '每题调用次数不同，请结合调用预算理解分数差异。'),
        ('early_stop', '提前结束设置不同。'),
        ('dataset_sha256', '题库版本不同，请注意题目难度及数据来源可能不同。'),
    ]
    for key, message in checks:
        if len({_fingerprint(summary.get(key)) for summary in summaries}) > 1:
            warnings.append(message)
    for key, message in (
        ('questions', '实际评测题目、题目内容、答案或截图不同，不能仅凭综合分判断能力差异。'),
        ('budget', '调用上限、超时等主办方预算不同。'),
        ('concurrency', '并发设置不同，运行条件存在差异。'),
        ('weights', '冻结配置中的评分权重记录不同；图表沿用系统现行固定评分公式。'),
    ):
        if len({value[key] for value in compatibility.values()}) > 1:
            warnings.append(message)
    if any(entry['dimensions'][key]['accuracy'] is None for entry in entries for key, _ in DIMENSIONS):
        warnings.append('部分任务缺少评测维度；综合分按原公式计零贡献，雷达图仅显示已知点，不绘制完整多边形。')
    return dict(entries=entries, warnings=warnings, selection_count=len(entries), scope_note=SCOPE_NOTE)


def chart_svg(data, chart):
    rows = data['entries']
    scale_label = '0–100 分' if chart == 'ranking' else '0–100%'
    subtitle = f'自选 {len(rows)} 个测评 · 仅供参考，非正式排名 · 固定 {scale_label} 标尺'
    if chart == 'ranking':
        svg = SVG('自选测评 · 综合分对比', 150 + 96 * len(rows), subtitle)
        left, width = 365, 650
        for tick in range(0, 101, 20):
            x = left + width * tick / 100
            svg.line(x, 105, x, svg.height - 45)
            svg.text(x, 95, str(tick), 12, anchor='middle')
        for index, row in enumerate(rows):
            y = 120 + index * 96
            svg.label(30, y + 8, f'{row["rank"]}. {row["label"]}', width=43)
            score = row['weighted_score']
            svg.parts.append(f'<g data-run-id="{esc(row["run_id"])}">'
                             f'<rect x="{left}" y="{y}" width="{width * score / 100:.4f}" height="30" '
                             f'fill="{row["color"]}"/><title>{esc(row["label"])}</title></g>')
            svg.text(left + width * score / 100 + 7, y + 21, number(score), 12)
        svg.text(left + width, svg.height - 15, '综合分 / 100 分 · 同分并列', 12, anchor='end')
        return svg.finish()
    if chart != 'radar':
        raise ValueError('仅支持雷达图 radar 和综合分图 ranking')
    svg = SVG('自选测评 · 四维准确率雷达图', max(610, 140 + 102 * len(rows)), subtitle)
    cx, cy, radius = 320, 335, 190
    for tick in (25, 50, 75, 100):
        length = radius * tick / 100
        points = [(cx, cy - length), (cx + length, cy), (cx, cy + length), (cx - length, cy)]
        svg.parts.append('<polygon points="' + ' '.join(f'{x},{y}' for x, y in points)
                         + '" fill="none" stroke="#dbe3ec"/>')
        svg.text(cx + 6, cy - length - 4, f'{tick}%', 10, '#61748b')
    svg.text(cx + 6, cy + 15, '0%', 10, '#61748b')
    directions = ((0, -1), (1, 0), (0, 1), (-1, 0))
    for (dx, dy), (_, label), anchor in zip(directions, DIMENSIONS, ('middle', 'start', 'middle', 'end')):
        svg.line(cx, cy, cx + dx * radius, cy + dy * radius)
        svg.text(cx + dx * (radius + 16), cy + dy * (radius + 24) + 4, label, 13, anchor=anchor)
    for index, row in enumerate(rows):
        y = 120 + index * 102
        values = [row['dimensions'][key]['accuracy'] for key, _ in DIMENSIONS]
        color = row['color']
        svg.parts.append(f'<g data-run-id="{esc(row["run_id"])}"><title>{esc(row["label"])}</title>')
        svg.parts.append(f'<rect x="625" y="{y - 11}" width="12" height="12" rx="3" fill="{color}"/>')
        svg.label(647, y, row['label'], width=61, color=color)
        detail = ' / '.join('缺失' if value is None else number(value * 100, 1) + '%' for value in values)
        svg.text(647, y + 57, detail + '（L2主 / L3主 / L1子 / L2子）', 11, '#61748b')
        if any(value is None for value in values):
            svg.text(647, y + 77, '缺失维度：仅画已知点，不补零', 11, '#9a6400')
        points = [(cx + dx * radius * value, cy + dy * radius * value)
                  for (dx, dy), value in zip(directions, values) if value is not None]
        if len(points) == len(DIMENSIONS):
            svg.parts.append('<polygon points="' + ' '.join(f'{x:.4f},{y:.4f}' for x, y in points)
                             + f'" fill="{color}" fill-opacity="0.07" stroke="{color}" stroke-width="2"/>')
        for x, y in points:
            svg.parts.append(f'<circle cx="{x:.4f}" cy="{y:.4f}" r="3.5" fill="{color}" '
                             'stroke="white" stroke-width="1"/>')
        svg.parts.append('</g>')
    return svg.finish()
