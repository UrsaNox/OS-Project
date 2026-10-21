"""Finite bounded workloads, with cleanup restricted to owned children."""
import subprocess
import sys
from pathlib import Path

import psutil


class Stress:
    def __init__(self, kind, seconds=20, workers=2, memory_mb=256, processes=24):
        if kind not in ('cpu', 'memory', 'process'):
            raise ValueError('Unknown stress kind')
        if not 1 <= seconds <= 120:
            raise ValueError('Stress duration must be 1..120 seconds')
        if not 1 <= workers <= min(4, psutil.cpu_count() or 1):
            raise ValueError('CPU workers must be 1..min(4, cores)')
        if not 16 <= memory_mb <= 512:
            raise ValueError('Memory allocation must be 16..512 MB')
        if kind == 'memory' and memory_mb*1024*1024 > psutil.virtual_memory().available*0.25:
            raise ValueError('Memory allocation exceeds 25% of currently available RAM')
        if not 1 <= processes <= 32:
            raise ValueError('Process count must be 1..32')
        self.count = workers if kind == 'cpu' else processes if kind == 'process' else 1
        self.command = [sys.executable, str(Path(__file__).with_name('workload.py')),
                        kind, str(seconds), str(memory_mb)]
        self.children = []

    def start(self):
        try:
            for _ in range(self.count):
                self.children.append(subprocess.Popen(self.command))
        except BaseException:
            self.stop()
            raise
        return self

    def stop(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
        for child in self.children:
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)

    def wait(self):
        try:
            for child in self.children:
                code = child.wait()
                if code != 0:
                    raise RuntimeError(f'Stress worker exited with code {code}')
        finally:
            self.stop()
