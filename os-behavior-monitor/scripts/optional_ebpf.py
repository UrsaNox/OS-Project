#!/usr/bin/env python3
"""Optional BCC exec-event counter. Separate research probe, not detector input."""
import argparse
import csv
import sys
import time
from datetime import datetime, timezone

PROGRAM = r'''
#include <uapi/linux/ptrace.h>
BPF_ARRAY(counts, u64, 1);
TRACEPOINT_PROBE(sched, sched_process_exec) {
    u32 key = 0;
    u64 *value = counts.lookup(&key);
    if (value) __sync_fetch_and_add(value, 1);
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--output', default='ebpf-exec.csv')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        parser.error('Use 1..300 seconds')
    try:
        from bcc import BPF
        import ctypes
        bpf = BPF(text=PROGRAM)
        table = bpf['counts']
        last = int(table[ctypes.c_int(0)].value)
        tick = time.monotonic()
        with open(args.output, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'exec_events_per_second'])
            for _ in range(args.seconds):
                time.sleep(1)
                now = time.monotonic()
                count = int(table[ctypes.c_int(0)].value)
                rate = (count-last)/(now-tick)
                writer.writerow([datetime.now(timezone.utc).isoformat(), rate])
                f.flush()
                print(f'exec/s: {rate:.1f}', flush=True)
                last, tick = count, now
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f'Optional eBPF unavailable ({type(exc).__name__}). '
              'Use the psutil demo; see README for BCC prerequisites.', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
