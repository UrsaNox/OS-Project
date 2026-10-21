"""Unprivileged system telemetry and non-overlapping windows."""
import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psutil

METRICS = ('cpu_pct', 'memory_pct', 'process_count', 'disk_read_bps',
           'disk_write_bps', 'net_recv_bps', 'net_sent_bps', 'ctx_switches_ps')
FEATURES = tuple(f'{m}_{s}' for m in METRICS for s in ('mean', 'max', 'std'))


def stamp():
    return datetime.now(timezone.utc).isoformat()


class CSVLog:
    def __init__(self, path, fields):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open('w', newline='')
        self.writer = csv.DictWriter(self.file, fieldnames=fields)
        self.writer.writeheader()
        self.file.flush()

    def write(self, row):
        self.writer.writerow(row)
        self.file.flush()

    def close(self):
        self.file.close()


def features(rows):
    if not rows:
        raise ValueError('Empty feature window')
    out = {}
    for key in METRICS:
        x = np.asarray([r[key] for r in rows], dtype=float)
        if not np.isfinite(x).all():
            raise ValueError(f'Non-finite telemetry: {key}')
        out.update({f'{key}_mean': float(x.mean()), f'{key}_max': float(x.max()),
                    f'{key}_std': float(x.std())})
    return out


class Collector:
    def __init__(self, interval=1.0):
        self.interval = interval
        psutil.cpu_percent(None)  # Discard first, meaningless nonblocking result.
        self.prev = self.counters()
        self.last = time.monotonic()
        self.next_tick = self.last + interval

    @staticmethod
    def counters():
        disk = psutil.disk_io_counters()
        net = psutil.net_io_counters()
        return (disk.read_bytes if disk else 0, disk.write_bytes if disk else 0,
                net.bytes_recv if net else 0, net.bytes_sent if net else 0,
                psutil.cpu_stats().ctx_switches)

    def sample(self):
        time.sleep(max(0, self.next_tick - time.monotonic()))
        now = time.monotonic()
        current = self.counters()
        elapsed = max(now - self.last, 1e-9)
        rates = [max(0, b-a)/elapsed for a, b in zip(self.prev, current)]
        row = dict(zip(METRICS, (psutil.cpu_percent(None),
                               psutil.virtual_memory().percent,
                               len(psutil.pids()), *rates)))
        row['timestamp'] = stamp()
        row['elapsed_s'] = elapsed
        self.prev, self.last = current, now
        self.next_tick = now + self.interval
        return row

    def window(self, samples, rawlog, phase='unlabeled'):
        rows = []
        for _ in range(samples):
            row = self.sample()
            rawlog.write({**row, 'phase': phase})
            rows.append(row)
        return {'timestamp': rows[-1]['timestamp'], 'phase': phase,
                'window_elapsed_s': sum(r['elapsed_s'] for r in rows), **features(rows)}


RAW_FIELDS = ('timestamp', 'phase', 'elapsed_s', *METRICS)
WINDOW_FIELDS = ('timestamp', 'phase', 'window_elapsed_s', *FEATURES)
