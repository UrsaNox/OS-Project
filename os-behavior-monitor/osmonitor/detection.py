"""Isolation Forest model plus independent threshold comparison."""
import json
import pickle
import platform
from pathlib import Path

import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest

from .telemetry import FEATURES, METRICS, stamp

# Floors keep tiny baseline variations from dominating explanation rankings.
FLOORS = np.array([5, 2, 5, 65536, 65536, 4096, 4096, 200], dtype=float)


def train(rows, interval, samples, contamination=0.05):
    if len(rows) < 20:
        raise ValueError('Collect at least 20 complete normal windows before training')
    x = np.asarray([[r[k] for k in FEATURES] for r in rows])
    if not np.isfinite(x).all():
        raise ValueError('Training contains non-finite values')
    forest = IsolationForest(n_estimators=150, contamination=contamination,
                             random_state=42, n_jobs=1).fit(x)
    means = x[:, ::3]
    center = np.median(means, axis=0)
    scale = np.maximum(np.median(np.abs(means-center), axis=0)*1.4826, FLOORS)
    resource_forests = [IsolationForest(n_estimators=100, contamination=contamination,
                        random_state=42, n_jobs=1).fit(means[:, i:i+1])
                        for i in range(len(METRICS))]
    return {'schema': 3, 'features': FEATURES, 'forest': forest,
            'resource_forests': resource_forests, 'deviation_gate': 3.0,
            'training_min': means.min(axis=0), 'training_max': means.max(axis=0),
            'center': center, 'scale': scale, 'interval': interval, 'samples': samples,
            'metadata': {'created_utc': stamp(), 'training_windows': len(rows),
                         'contamination': contamination, 'seed': 42,
                         'python': platform.python_version(), 'sklearn': sklearn.__version__,
                         'platform': platform.platform(), 'collector': 'psutil'}}


def save(model, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as f:
        pickle.dump(model, f)
    path.with_suffix('.json').write_text(json.dumps({**model['metadata'],
        'interval': model['interval'], 'samples': model['samples'],
        'features': list(FEATURES)}, indent=2)+'\n')


def load(path):
    # Only load your own model. Pickle can execute arbitrary code.
    with Path(path).open('rb') as f:
        model = pickle.load(f)
    if model.get('schema') != 3 or tuple(model.get('features', [])) != FEATURES:
        raise ValueError('Model feature schema differs; retrain')
    if model['metadata']['sklearn'] != sklearn.__version__:
        raise ValueError('scikit-learn version differs; retrain locally')
    return model


def detect(model, row):
    x = np.array([[row[k] for k in FEATURES]])
    decision = float(model['forest'].decision_function(x)[0])
    means = x[0, ::3]
    deviations = (means-model['center'])/model['scale']
    resource_scores = np.array([-float(f.decision_function([[means[i]]])[0])
                               for i, f in enumerate(model['resource_forests'])])
    # A resource must have both IF novelty and a material baseline deviation.
    effective = np.minimum(resource_scores, np.abs(deviations)-model['deviation_gate'])
    iforest_score = float(effective.max())
    # Sparse/discrete baselines can saturate an IF endpoint below its cutoff.
    # Explicit statistical fallback: outside observed normal range by >3 floors.
    range_margin = np.maximum((means-model['training_max'])/FLOORS,
                              (model['training_min']-means)/FLOORS)-3.0
    range_score = float(np.minimum(range_margin, np.abs(deviations)-3.0).max())
    score = max(iforest_score, range_score)
    source = 'resource_iforest' if iforest_score > 0 else 'range_fallback' if range_score > 0 else 'none'
    ranked = sorted(zip(METRICS, deviations, means, model['center']),
                    key=lambda v: abs(v[1]), reverse=True)
    changes = [{'metric': m, 'deviation': round(float(z), 2),
                'observed': round(float(v), 3), 'baseline': round(float(b), 3)}
               for m, z, v, b in ranked[:3]]
    threshold = bool(means[0] >= 80 or means[1] >= 85 or
                     means[2] > model['center'][2]+max(20, model['center'][2]*0.2))
    return {'anomaly': int(score > 0), 'iforest_anomaly': int(iforest_score > 0),
            'range_anomaly': int(range_score > 0), 'detector_source': source,
            'anomaly_score': score,
            'global_iforest_score': -decision,
            'resource_scores': dict(zip(METRICS, map(float, resource_scores))),
            'threshold_anomaly': int(threshold), 'changes': changes}
