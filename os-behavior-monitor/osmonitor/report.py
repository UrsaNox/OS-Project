"""Measured workload discrimination; not real failure prediction accuracy."""
import csv
import json
from pathlib import Path
from .display import Dashboard


def metrics(rows, field):
    tp = sum(r['phase'] in ('cpu', 'memory', 'process') and int(r[field]) == 1 for r in rows)
    fn = sum(r['phase'] in ('cpu', 'memory', 'process') and int(r[field]) == 0 for r in rows)
    fp = sum(r['phase'] == 'normal' and int(r[field]) == 1 for r in rows)
    tn = sum(r['phase'] == 'normal' and int(r[field]) == 0 for r in rows)
    return {'tp': tp, 'fn': fn, 'fp': fp, 'tn': tn,
            'precision': tp/(tp+fp) if tp+fp else None,
            'recall': tp/(tp+fn) if tp+fn else None,
            'false_positive_rate': fp/(fp+tn) if fp+tn else None}


def report(path):
    path = Path(path)
    with path.open(newline='') as f:
        allrows = list(csv.DictReader(f))
    eligible = [r for r in allrows if int(r['eligible']) == 1]
    phases = {}
    for phase in sorted({r['phase'] for r in allrows}):
        subset = [r for r in allrows if r['phase'] == phase]
        phases[phase] = {'windows': len(subset),
            'proposed_alerts': sum(int(r.get('anomaly', r['iforest_anomaly'])) for r in subset),
            'iforest_alerts': sum(int(r['iforest_anomaly']) for r in subset),
            'range_fallback_alerts': sum(r.get('detector_source') == 'range_fallback' for r in subset),
            'threshold_alerts': sum(int(r['threshold_anomaly']) for r in subset)}
    result = {'scope': 'Controlled workload discrimination, not security/failure accuracy',
              'total_windows': len(allrows), 'evaluated_windows': len(eligible),
              'excluded': 'First window of each phase and all recovery/unlabeled windows',
              'proposed_hybrid': metrics(eligible, 'anomaly') if all('anomaly' in r for r in eligible) else None,
              'isolation_forest': metrics(eligible, 'iforest_anomaly'),
              'threshold': metrics(eligible, 'threshold_anomaly'), 'phases': phases}
    out = path.with_name('summary.json')
    out.write_text(json.dumps(result, indent=2)+'\n')
    screen = Dashboard()
    screen.banner('DEMO RESULTS', 'Controlled workload detection; transition/recovery windows excluded.')
    screen.text(f"Evaluated: {len(eligible)} windows | Full data: {len(allrows)} windows")
    screen.write()
    screen.write(f"{'Method':20} {'TP':>4} {'FN':>4} {'FP':>4} {'TN':>4}")
    screen.write('-'*min(screen.width, 40))
    for label, key in [('Proposed hybrid', 'proposed_hybrid'), ('Resource IF', 'isolation_forest'), ('Threshold', 'threshold')]:
        m = result.get(key)
        if m:
            screen.write(f"{label:20} {m['tp']:4} {m['fn']:4} {m['fp']:4} {m['tn']:4}")
    screen.write()
    screen.text('TP: detected workload | FN: missed workload')
    screen.text('FP: normal-window alert | TN: normal window without alert')
    screen.text('This small demo does not measure future-error or attack prediction.')
    screen.text(f'Full summary: {out.resolve()}')
    return result
