"""Standalone stdlib-only child: avoid loading NumPy/sklearn per worker."""
import sys
import time


def run(kind, seconds, size_mb):
    end = time.monotonic()+seconds
    if kind == 'cpu':
        value = 1
        while time.monotonic() < end:
            for _ in range(10000):
                value = (value*1664525+1013904223) & 0xffffffff
    elif kind == 'memory':
        block = bytearray(size_mb*1024*1024)
        for i in range(0, len(block), 4096):
            block[i] = 1
        while time.monotonic() < end:
            time.sleep(0.1)
    else:
        while time.monotonic() < end:
            time.sleep(0.1)


if __name__ == '__main__':
    run(sys.argv[1], float(sys.argv[2]), int(sys.argv[3]))
