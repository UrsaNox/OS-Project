#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-kali-tested.txt
printf '\nReady. Activate with: source .venv/bin/activate\n'
printf 'Run demo: python -m osmonitor demo --seconds 90 --interval 1 --samples 3 --phase-windows 4 --cooldown 9\n'
