import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from osmonitor import detection
from osmonitor.explain import Explainer, gemini
from osmonitor.report import metrics
from osmonitor.stress import Stress
from osmonitor.telemetry import FEATURES, METRICS, features


class PipelineTests(unittest.TestCase):
    def baseline(self):
        rng = np.random.default_rng(4)
        return [features([{k: float(v) for k, v in zip(METRICS,
                 [5+rng.normal(0, .3), 30+rng.normal(0, .2), 180+rng.integers(-2, 3),
                  0, 0, 0, 0, 1000+rng.normal(0, 10)])} for _ in range(5)])
                for _ in range(60)]

    def test_features_means_std_and_schema(self):
        out = features([{k: 2 for k in METRICS}, {k: 4 for k in METRICS}])
        self.assertEqual(tuple(out), FEATURES)
        self.assertEqual(out['cpu_pct_mean'], 3)
        self.assertEqual(out['cpu_pct_std'], 1)
        with self.assertRaises(ValueError):
            features([])
        with self.assertRaises(ValueError):
            features([{k: float('nan') for k in METRICS}])

    def test_training_persistence_and_score_direction(self):
        rows = self.baseline()
        model = detection.train(rows, 1, 5)
        extreme = features([{k: v for k, v in zip(METRICS,
                   [100, 90, 500, 1e7, 1e7, 1e7, 1e7, 1e6])} for _ in range(5)])
        result = detection.detect(model, extreme)
        self.assertEqual(result['iforest_anomaly'], 1)
        self.assertGreater(result['anomaly_score'], 0)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'model.pkl'
            detection.save(model, p)
            self.assertEqual(detection.detect(detection.load(p), extreme), result)
        with self.assertRaises(ValueError):
            detection.train(rows[:5], 1, 5)

    def test_each_resource_detects_material_change(self):
        model = detection.train(self.baseline(), 1, 5)
        normal = dict(zip(METRICS, [5, 30, 180, 0, 0, 0, 0, 1000]))
        row = features([normal]*5)
        self.assertEqual(detection.detect(model, row)['iforest_anomaly'], 0)
        for metric, value in [('cpu_pct', 50), ('memory_pct', 37), ('process_count', 204)]:
            changed = features([{**normal, metric: value}]*5)
            self.assertEqual(detection.detect(model, changed)['anomaly'], 1, metric)

    def test_constant_baseline_uses_explicit_fallback(self):
        normal = dict(zip(METRICS, [5, 30, 180, 0, 0, 0, 0, 1000]))
        model = detection.train([features([normal]*5)]*30, 1, 5)
        changed = features([{**normal, 'process_count': 204}]*5)
        result = detection.detect(model, changed)
        self.assertEqual(result['anomaly'], 1)
        self.assertEqual(result['iforest_anomaly'], 0)
        self.assertEqual(result['detector_source'], 'range_fallback')

    def test_local_selectivity_cooldown_and_no_key(self):
        result = {'iforest_anomaly': 1, 'anomaly_score': .1,
                  'changes': [{'metric': 'cpu_pct', 'observed': 90, 'baseline': 5}]}
        e = Explainer('gemini', cooldown=30)
        try:
            with patch.dict('os.environ', {}, clear=True):
                self.assertIsNone(e.submit({'timestamp': 't'}, {**result, 'iforest_anomaly': 0}))
                out = e.submit({'timestamp': 't'}, result)
                self.assertEqual(out['fallback_reason'], 'no_api_key')
                self.assertEqual(out['source'], 'local')
                self.assertIsNone(e.submit({'timestamp': 't'}, result))
                self.assertEqual(e.calls, 0)
        finally:
            e.close()

    def test_remote_failure_fallback_and_single_inflight(self):
        result = {'iforest_anomaly': 1, 'anomaly_score': .1,
                  'changes': [{'metric': 'memory_pct', 'observed': 50, 'baseline': 30}]}
        e = Explainer('gemini', cooldown=0)
        try:
            with patch.dict('os.environ', {'GEMINI_API_KEY': 'test-only'}), \
                 patch('osmonitor.explain.gemini', side_effect=TimeoutError):
                self.assertIsNone(e.submit({'timestamp': 't'}, result))
                self.assertIsNone(e.submit({'timestamp': 't'}, result))
                out = e.poll(wait=True)
                self.assertEqual(out['source'], 'local')
                self.assertEqual(out['fallback_reason'], 'TimeoutError')
                self.assertEqual(e.calls, 1)
        finally:
            e.close()

    def test_gemini_request_and_validated_response(self):
        reply={'likely_cause':'CPU workload','severity':'low','explanation':'Elevated CPU',
               'recommended_action':'Inspect top'}
        body={'candidates':[{'content':{'parts':[{'text':json.dumps(reply)}]}}]}
        with patch.dict('os.environ', {'GEMINI_API_KEY':'test-only'}, clear=True), \
             patch('osmonitor.explain.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(body).encode())) as call:
            out=gemini({'timestamp':'t','anomaly_score':.1,'changes':[]})
            self.assertEqual(out['source'],'gemini')
            req=call.call_args.args[0]
            self.assertNotIn('test-only',req.full_url)
            self.assertEqual(req.get_header('X-goog-api-key'),'test-only')
            self.assertIn('gemini-3.5-flash-lite',req.full_url)

    def test_gemini_rejects_malformed_response(self):
        body={'candidates':[{'content':{'parts':[{'text':'{}'}]}}]}
        with patch.dict('os.environ', {'GEMINI_API_KEY':'test-only'}, clear=True), \
             patch('osmonitor.explain.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(body).encode())):
            with self.assertRaises(ValueError):
                gemini({'changes':[]})

    def test_stress_bounds_cleanup_and_worker_exit(self):
        with self.assertRaises(ValueError):
            Stress('memory', memory_mb=2048)
        with self.assertRaises(ValueError):
            Stress('process', processes=500)
        for kind in ('cpu', 'memory', 'process'):
            s = Stress(kind, seconds=1, workers=1, memory_mb=16, processes=3).start()
            s.wait()
            self.assertTrue(all(c.poll() == 0 for c in s.children))
        s = Stress('process', seconds=20, processes=3).start()
        s.stop()
        self.assertTrue(all(c.poll() is not None for c in s.children))

    def test_metrics_independent_labels(self):
        rows = [{'phase': 'cpu', 'pred': '1'}, {'phase': 'memory', 'pred': '0'},
                {'phase': 'normal', 'pred': '1'}, {'phase': 'normal', 'pred': '0'}]
        m = metrics(rows, 'pred')
        self.assertEqual((m['tp'], m['fn'], m['fp'], m['tn']), (1, 1, 1, 1))
        self.assertEqual(m['recall'], .5)
        self.assertIsNone(metrics([], 'pred')['recall'])


if __name__ == '__main__':
    unittest.main()
