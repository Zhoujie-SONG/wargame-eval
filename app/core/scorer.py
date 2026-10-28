WEIGHTS = {'L2_main': .35, 'L3_main': .35, 'L1_sub': .15, 'L2_sub': .15}


def score(questions, results):
    dimensions = {key: {'total': 0, 'correct': 0, 'completed': 0} for key in WEIGHTS}
    for q in questions:
        d = dimensions[q['level']+'_'+q['role']]
        d['total'] += 1
        r = results.get(q['question_id'])
        if r:
            d['completed'] += 1
            d['correct'] += int(r.get('correct', False))
    return score_counts(dimensions)


def score_counts(dimensions):
    for d in dimensions.values():
        d['accuracy'] = d['correct']/d['total'] if d['total'] else None
        # Running view only: accuracy over the questions answered so far. Final scores use `accuracy`.
        d['answered_accuracy'] = d['correct']/d['completed'] if d.get('completed') else None
    answered = sum(d.get('completed', 0) for d in dimensions.values())
    # Missing dimensions contribute zero; never renormalize partial tests.
    weighted = 100 * sum(WEIGHTS[k]*(d['accuracy'] or 0) for k, d in dimensions.items())
    total = sum(d['total'] for d in dimensions.values())
    correct = sum(d['correct'] for d in dimensions.values())
    return dict(dimensions=dimensions, weighted_score=weighted,
                overall_accuracy=correct/total if total else 0,
                answered_accuracy=correct/answered if answered else None)
