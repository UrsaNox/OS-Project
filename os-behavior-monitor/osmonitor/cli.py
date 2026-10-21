"""Run with python -m osmonitor. No root required for stable path."""
import argparse
import csv
import json
import math
import signal
import time
from pathlib import Path

from .display import Dashboard
from .config import load_env
from . import detection
from .explain import Explainer
from .report import report
from .stress import Stress
from .telemetry import CSVLog, Collector, FEATURES, RAW_FIELDS, WINDOW_FIELDS, stamp

PRED_FIELDS = (*WINDOW_FIELDS, 'eligible', 'iforest_anomaly', 'anomaly_score', 'threshold_anomaly', 'global_iforest_score', 'anomaly', 'range_anomaly', 'detector_source')


def duration(args):
    if args.interval <= 0 or args.samples < 2:
        raise ValueError('Use a positive interval and at least 2 samples/window')
    if not math.isfinite(args.interval) or args.interval > 60:
        raise ValueError('Interval must be finite and <=60 seconds')
    return args.interval*args.samples


def collect(args):
    window_s = duration(args)
    count = math.ceil(args.seconds/window_s)
    if count < 20:
        raise ValueError(f'Baseline needs >=20 windows (at least {20*window_s:g} seconds)')
    path = Path(args.output)
    path.mkdir(parents=True, exist_ok=True)
    rows = []
    raw = CSVLog(path/'raw.csv', RAW_FIELDS)
    windows = CSVLog(path/'features.csv', WINDOW_FIELDS)
    try:
        collector = Collector(args.interval)
        screen = Dashboard()
        screen.banner('OS BEHAVIOR MONITOR | BASELINE', f'{count} windows | {window_s:g}s per window | Keep the VM at normal activity.')
        for i in range(count):
            row = collector.window(args.samples, raw, 'baseline')
            windows.write(row)
            rows.append(row)
            screen.baseline(i+1, count, row)
        model = detection.train(rows, args.interval, args.samples, args.contamination)
        detection.save(model, args.model)
        print(f'Saved model: {args.model}', flush=True)
        return model
    finally:
        raw.close()
        windows.close()


def monitor(args, model=None, schedule=None):
    model = model or detection.load(args.model)
    path = Path(args.output)
    path.mkdir(parents=True, exist_ok=True)
    raw = CSVLog(path/'raw.csv', RAW_FIELDS)
    pred = CSVLog(path/'predictions.csv', PRED_FIELDS)
    explanation_file = (path/'explanations.jsonl').open('w')
    explainer = Explainer(args.explain, args.cooldown)
    stress = None
    started = time.monotonic()
    window_s = model['interval']*model['samples']
    if schedule is None:
        count = math.ceil(args.seconds/window_s) if args.seconds else None
        schedule = [('unlabeled', count)]
    try:
        collector = Collector(model['interval'])
        screen = Dashboard()
        screen.banner('OS BEHAVIOR MONITOR | DETECTION', f'Window: {window_s:g}s | Explanations: {args.explain.upper()} | Cooldown: {args.cooldown:g}s')
        screen.text('Positive score = alert. IF = Isolation Forest; RANGE = statistical fallback.')
        for phase, count in schedule:
            if phase in ('cpu', 'memory', 'process'):
                stress = Stress(phase, seconds=min(120, count*window_s+2),
                                workers=args.workers, memory_mb=args.memory_mb,
                                processes=args.processes).start()
            screen.phase(phase, count)
            i = 0
            try:
                while count is None or i < count:
                    row = collector.window(model['samples'], raw, phase)
                    if stress and any(c.poll() not in (None, 0) for c in stress.children):
                        raise RuntimeError('Controlled workload failed; do not evaluate this run')
                    result = detection.detect(model, row)
                    eligible = int(i > 0 and phase in ('normal', 'cpu', 'memory', 'process'))
                    pred.write({**row, 'eligible': eligible,
                                **{k: result[k] for k in ('iforest_anomaly', 'anomaly_score', 'threshold_anomaly', 'global_iforest_score', 'anomaly', 'range_anomaly', 'detector_source')}})
                    screen.sample(row, result)
                    pending = explainer.poll()
                    immediate = explainer.submit(row, result)
                    for explanation in (pending, immediate):
                        if explanation:
                            explanation_file.write(json.dumps(explanation)+'\n')
                            explanation_file.flush()
                            screen.explanation(explanation)
                    i += 1
            finally:
                if stress:
                    stress.stop()
                    stress = None
        pending = explainer.poll(wait=True)
        if pending:
            explanation_file.write(json.dumps(pending)+'\n')
            screen.explanation(pending)
    finally:
        if stress:
            stress.stop()
        explainer.close()
        raw.close()
        pred.close()
        explanation_file.close()
        Dashboard().text(f'Logs saved to: {path.resolve()}')
        (path/'run.json').write_text(json.dumps({'created_utc': stamp(),
            'elapsed_s': time.monotonic()-started, 'gemini_requests': explainer.calls,
            'explanation_mode': args.explain, 'model_metadata': model['metadata'],
            'interval': model['interval'], 'samples': model['samples'],
            'stress_workers': args.workers, 'stress_memory_mb': args.memory_mb,
            'stress_processes': args.processes}, indent=2)+'\n')


def main(argv=None):
    load_env()
    parser = argparse.ArgumentParser(description='Review-2 OS behavior monitor (userspace prototype)')
    sub = parser.add_subparsers(dest='command', required=True)
    def model_options(p):
        p.add_argument('--model', default='models/baseline.pkl')
    def collect_options(p):
        p.add_argument('--interval', type=float, default=1)
        p.add_argument('--samples', type=int, default=5)
        p.add_argument('--contamination', type=float, default=0.05)
    def stress_options(p):
        p.add_argument('--workers', type=int, default=2)
        p.add_argument('--memory-mb', type=int, default=256)
        p.add_argument('--processes', type=int, default=24)
    def explain_options(p):
        p.add_argument('--explain', choices=('local', 'gemini'), default='local')
        p.add_argument('--cooldown', type=float, default=30)
    p = sub.add_parser('train', help='Collect normal baseline and fit model')
    model_options(p); collect_options(p)
    p.add_argument('--seconds', type=float, default=180)
    p.add_argument('--output', default='data/baseline')
    p = sub.add_parser('monitor', help='Monitor using saved model')
    model_options(p); stress_options(p); explain_options(p)
    p.add_argument('--seconds', type=float, default=0, help='0 means until Ctrl+C')
    p.add_argument('--output', default='data/monitor')
    p = sub.add_parser('stress', help='Run finite controlled workload')
    p.add_argument('kind', choices=('cpu', 'memory', 'process'))
    p.add_argument('--seconds', type=float, default=20)
    stress_options(p)
    p = sub.add_parser('demo', help='Automatic baseline + held-out controlled demo')
    model_options(p); collect_options(p); stress_options(p); explain_options(p)
    p.add_argument('--seconds', type=float, default=120, help='Baseline collection seconds')
    p.add_argument('--phase-windows', type=int, default=4)
    p.add_argument('--output', default='data/demo')
    p = sub.add_parser('report', help='Summarize a recorded automatic demo')
    p.add_argument('predictions')
    args = parser.parse_args(argv)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if hasattr(args, 'seconds') and (not math.isfinite(args.seconds) or args.seconds < 0):
            raise ValueError('Seconds must be finite and non-negative')
        if hasattr(args, 'cooldown') and (not math.isfinite(args.cooldown) or args.cooldown < 0):
            raise ValueError('Cooldown must be finite and non-negative')
        if args.command == 'train':
            collect(args)
        elif args.command == 'monitor':
            monitor(args)
        elif args.command == 'stress':
            Stress(args.kind, args.seconds, args.workers, args.memory_mb, args.processes).start().wait()
        elif args.command == 'demo':
            if args.phase_windows < 2 or args.phase_windows*duration(args)+2 > 120:
                raise ValueError('Demo phases need >=2 windows and <=118 seconds')
            root = Path(args.output)
            args.output = str(root/'baseline')
            model = collect(args)
            args.output = str(root/'test')
            n = args.phase_windows
            schedule = [('normal', n), ('cpu', n), ('recovery', n),
                        ('memory', n), ('recovery', n), ('process', n), ('recovery', n)]
            monitor(args, model, schedule)
            report(root/'test'/'predictions.csv')
        elif args.command == 'report':
            report(args.predictions)
    except KeyboardInterrupt:
        print('\nStopped. Owned stress workers cleaned up; completed CSV rows preserved.', flush=True)
        return 130
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f'Error: {exc}\n')
    return 0
