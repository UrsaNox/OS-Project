"""Readable, dependency-free terminal presentation; full details stay in logs."""
import re
import shutil
import sys
import textwrap
from datetime import datetime


def clean(value):
    # External explanations cannot inject terminal control sequences.
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(value))
    return ''.join(c for c in value if c >= ' ' or c == '\n')


class Dashboard:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.width = max(40, min(88, shutil.get_terminal_size((80, 24)).columns-1))
        self.wide = self.width >= 64

    def write(self, text=''):
        print(text, file=self.stream, flush=True)

    def text(self, value, indent=''):
        for line in textwrap.wrap(clean(value), width=self.width,
                                  initial_indent=indent, subsequent_indent=indent) or ['']:
            self.write(line)

    def banner(self, title, subtitle):
        self.write()
        self.write('='*self.width)
        self.text(title)
        self.text(subtitle)
        self.write('='*self.width)

    def baseline(self, number, total, row):
        filled = int(20*number/total)
        line = (f"[{('#'*filled).ljust(20, '.')}] {number:02}/{total:02} "
                f"CPU {row['cpu_pct_mean']:4.1f}%  RAM {row['memory_pct_mean']:4.1f}%")
        if self.width < 64:
            line = f"Baseline {number}/{total} | CPU {row['cpu_pct_mean']:.1f}% RAM {row['memory_pct_mean']:.1f}%"
        if self.stream.isatty():
            print('\r'+line.ljust(self.width), end='\n' if number == total else '',
                  file=self.stream, flush=True)
        else:
            self.text(line)

    def phase(self, phase, count):
        labels = {'normal':'NORMAL OBSERVATION', 'cpu':'CPU WORKLOAD',
                  'memory':'MEMORY WORKLOAD', 'process':'PROCESS WORKLOAD',
                  'recovery':'RECOVERY', 'unlabeled':'LIVE MONITORING'}
        self.write()
        self.write('-'*self.width)
        self.text(f"{labels.get(phase, phase.upper())} | {count or 'continuous'} windows")
        self.header()

    def header(self):
        self.write('-'*self.width)
        if self.wide:
            self.write(f"{'UTC time':8}  {'Status':7}  {'Score':>7}  {'CPU%':>5}  {'RAM%':>5}  {'Procs':>5}  {'Via':7}")
        else:
            self.write(f"{'UTC time':8}  {'Status':7}  {'CPU%':>5}  {'RAM%':>5}")
        self.write('-'*self.width)

    def sample(self, row, result):
        clock = datetime.fromisoformat(row['timestamp']).strftime('%H:%M:%S')
        state = 'ALERT' if result['anomaly'] else 'NORMAL'
        via = {'resource_iforest':'IF', 'range_fallback':'RANGE', 'none':'-'}[result['detector_source']]
        score = f"{result['anomaly_score']:+.3f}"
        if len(score)>7:
            score = f"{result['anomaly_score']:+.1e}"
        if self.wide:
            self.write(f"{clock:8}  {state:7}  {score:>7}  {row['cpu_pct_mean']:5.1f}  "
                       f"{row['memory_pct_mean']:5.1f}  {row['process_count_mean']:5.0f}  {via:7}")
        else:
            self.write(f"{clock:8}  {state:7}  {row['cpu_pct_mean']:5.1f}  {row['memory_pct_mean']:5.1f}")
            self.text(f"Score {score} | Processes {row['process_count_mean']:.0f} | Via {via}", '  ')

    def explanation(self, result):
        self.write()
        self.write('+'+'-'*(self.width-2)+'+')
        clock = datetime.fromisoformat(result['timestamp']).strftime('%H:%M:%S')
        self.text(f"EXPLANATION | {result['source'].upper()} | {clock} UTC", '  ')
        self.text(f"Severity: {result['severity'].upper()}", '  ')
        if result.get('fallback_reason'):
            self.text(f"Local fallback: {result['fallback_reason']}", '  ')
        for label, key in [('Possible cause', 'likely_cause'), ('Reason', 'explanation'),
                           ('Suggested action', 'recommended_action')]:
            self.write()
            self.text(label+':', '  ')
            self.text(result[key], '    ')
        self.write('+'+'-'*(self.width-2)+'+')
        self.write()
        self.header()
