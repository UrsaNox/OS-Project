# OS Behavior Monitor — Review 2

Implementation prototype for **LLM-Driven Security and Behavior-Aware Operating System Kernel for Predictive Error Detection**.

A working userspace Linux monitoring pipeline: psutil telemetry → fixed sample windows → Isolation Forest → selective explanation → terminal output + CSV/JSON logs. This prototype detects behavioral deviations and supports triage. It does **not** modify the kernel, prove a security attack, or demonstrate prediction of future failures. Optional BCC instrumentation is a separate experimental probe.

## Target and quick start

Target: Kali Linux ARM64 (`aarch64`), Python 3.13, UTM VM, 4 GB RAM / 4 CPU cores. Stable path needs no root. Internet is needed for package installation and optional Gemini.

```bash
cd ~/Projects/os-behavior-monitor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-kali-tested.txt
python -m unittest discover -s tests -v
```

If `venv` is unavailable:

```bash
sudo apt update
sudo apt install python3-venv python3-pip git
```

Use the tested lock file for the exact verified Python 3.13 ARM64 environment. `requirements.txt` gives supported dependency ranges for other installations. Do not use `sudo pip` or `--break-system-packages`.

### One-command demo (about 3 minutes)

Close large downloads, pause updates, and keep the VM at ordinary activity during baseline collection. The baseline is assumed normal; no automatic cleansing of contaminated data is performed.

```bash
python -u -m osmonitor demo --seconds 90 --interval 1 --samples 3 \
  --phase-windows 4 --cooldown 9 --output data/my-demo
```

This records 30 baseline windows, trains a model, then records normal, CPU, recovery, memory, recovery, process, and recovery phases (4 windows per phase). CPU uses 2 workers, memory allocates 256 MiB, process activity adds 24 sleeping children. Every workload is finite and cleaned up by the parent.

Expected **format**, not a promised result: a `normal`/`ANOMALY` line with score, CPU%, memory%, and process count; explanation lines appear selectively. The actual measured run is in `docs/evidence/` and discussed in `docs/REVIEW2.md`.

Default output folders are overwritten on reuse. Choose a different `--output` for each run. Model files are also replaced when training again; use `--model models/new-baseline.pkl` to retain a prior model.

### Separate commands for a live review

Train once on normal activity (3 minutes with 5-second windows):

```bash
python -m osmonitor train --seconds 180 --model models/baseline.pkl
python -m osmonitor monitor --model models/baseline.pkl --output data/live
```

In another terminal, activate the same virtual environment and run one workload at a time:

```bash
python -m osmonitor stress cpu --seconds 20 --workers 2
python -m osmonitor stress memory --seconds 20 --memory-mb 256
python -m osmonitor stress process --seconds 20 --processes 24
```

Allow 15–30 seconds recovery between tests. Use `Ctrl+C` to stop monitoring or a workload. Monitoring does not stop independently launched stress commands; each has its own bounded duration. Outputs from manually invoked stress commands are `unlabeled` in monitor CSVs; evaluation labels are supplied only by the automatic demo.

## Architecture and features

- `telemetry.py`: CPU%, memory%, process count, cumulative disk/network counters converted into rates, context-switch rate. The first nonblocking CPU reading is discarded. Counter resets are clamped to zero. No process command lines, usernames, filenames, or packet contents are collected.
- `detection.py`: mean/max/population standard deviation for each of 8 metrics (24 features), non-overlapping windows. A 150-tree global Isolation Forest is retained as a diagnostic comparator. The operational detector uses eight 100-tree Isolation Forests, one per mean resource metric, with seed 42, contamination 0.05, and one training thread. Each resource must have positive IF novelty score AND absolute baseline deviation greater than 3 robust scale units. Scale is max(1.4826 × MAD, metric floor). Floors: CPU 5 percentage points, memory 2 percentage points, process count 5, disk rates 65536 B/s, network rates 4096 B/s, context switches 200/s. The exported score is max over resources of min(negative IF decision function, absolute deviation minus 3). If no resource forest alerts, a separate range fallback can alert when a resource is beyond its observed normal min/max by more than 3 metric floors and also beyond 3 robust scale units. This handles a sparse or constant baseline where IF endpoint scores saturate. The final score is the maximum of resource IF and range scores. Positive means alert; it is not a probability. `anomaly` is the hybrid decision; `iforest_anomaly` is the resource-forest-only decision; `detector_source` explicitly records `resource_iforest`, `range_fallback`, or `none`. `global_iforest_score` records the original global model separately. Train all forests on normal baseline only.
- Independent threshold baseline: mean CPU ≥80%, memory ≥85%, or process count above baseline median + max(20, 20% of baseline). This is a simple comparator, not a second gate on Isolation Forest.
- `explain.py`: rank mean-metric deviations using baseline medians, MAD, and metric-specific floors. This is a heuristic explanation, **not** model attribution. Send only alert timestamp, score, and top 3 aggregate changes to Gemini. No raw telemetry stream or phase labels are sent. Detection and logging continue while an API request runs in a background thread.
- `stress.py` / `workload.py`: bounded CPU/memory/process scenarios, stdlib-only child workers to avoid importing the ML stack in every child. Memory touches actual pages. Limits: ≤4 CPU workers, ≤512 MiB and ≤25% of available RAM, ≤32 child processes, ≤120 seconds per workload.
- `cli.py`: terminal dashboard, CSV logs, model metadata, automatic demo orchestration.
- `report.py`: measured comparison using held-out workload windows. First window per phase and all recovery windows are excluded from metrics to reduce transition effects.

The collector uses a monotonic clock for actual rate denominators. Windows contain a fixed number of samples, normally 3 or 5 at one-second intervals. Scheduler delays can lengthen a window; raw `elapsed_s` and `window_elapsed_s` record actual duration. No overlapping windows are used.

## Optional Gemini

Local explanations are the default and work without a key. To enable Gemini explicitly:

```bash
# For a fresh clone, copy .env.example to .env and fill in your key.
# The configured Mac/Kali copies already have a private .env file.
python -m osmonitor monitor --explain gemini --cooldown 30 --output data/gemini-demo
```

The CLI automatically loads the repo-root `.env` using python-dotenv. Existing exported variables take precedence. `.env` is ignored by Git and excluded from distributable archives; `.env.example` contains no key. Keep the local file private (mode 600). Never commit keys. Model availability depends on your account; set `GEMINI_MODEL` to a model your account can use. Requests may incur API charges. There is an 8-second network timeout, at most one request in flight, and a cooldown between explanations. Missing key, API error, timeout, blocked/invalid response → local fallback, with a sanitized reason in JSONL. Explanations are advisory and cannot execute actions. Live Gemini structured explanation succeeded on Kali using a user-provided key and gemini-3.5-flash-lite. Earlier 2.5 models returned HTTP 404 and larger 3.5/3.8 Flash models returned HTTP 503; each failure produced local fallback. The configured execution copies store the key only in their ignored local `.env` files; tracked files and distributed archives contain no key. The main measured workload demo used local mode; separate live Gemini evidence is retained.

## Saved outputs

`data/<run>/baseline/`: raw telemetry CSV + feature CSV. `data/<run>/test/`:

- `raw.csv`: measured samples and scenario labels.
- `predictions.csv`: 24 window features, scenario, eligibility flag, hybrid and resource-IF score/labels, source of alert, range fallback, threshold label.
- `explanations.jsonl`: selected explanation and source/fallback reason.
- `run.json`: configuration, model environment, API request count.
- `summary.json`: per-phase alert counts and measured confusion matrices.

```bash
python -m osmonitor report data/my-demo/test/predictions.csv
```

Training creates a `.pkl` and companion JSON metadata. Load only model files you created: pickle can execute code. Retrain after changing scikit-learn version. Live monitoring uses the sampling configuration saved in the model.

## Optional eBPF / BCC (experimental)

The psutil demo is the supported path. `scripts/optional_ebpf.py` counts Linux `sched_process_exec` tracepoint events with BCC and writes a separate CSV. It is **not integrated into the 24-feature model**, and the psutil model does not require BCC.

```bash
sudo apt update
sudo apt install python3-bpfcc bpfcc-tools
# Matching kernel headers may be required and may not exist for the VM kernel.
sudo apt install "linux-headers-$(uname -r)"
sudo /usr/bin/python3 scripts/optional_ebpf.py --seconds 30 --output /tmp/ebpf-exec.csv
```

Use system Python for distro BCC bindings. ARM64 support depends on your Kali kernel, permissions, tracepoint availability, LLVM, and headers. An unavailable optional probe exits clearly and does not alter the psutil demo. No BCC success is claimed in the measured results.

## GitHub and review submission

The local repository is ready to publish; no GitHub URL is invented. Create a repository under your account and push this folder, then put the real URL into `docs/REVIEW2.md`. If private, grant the faculty reviewer access. Do not publish `.venv`, secrets, model pickles, or ongoing telemetry; `.gitignore` excludes these. The deliberately retained controlled demonstration CSVs under `docs/evidence/` contain only aggregate telemetry and scenario labels.

```bash
git add .
git commit -m "Add Review-2 OS behavior monitoring prototype"
# Substitute the actual repository URL you created:
git remote add origin <YOUR_GITHUB_REPOSITORY_URL>
git branch -M main
git push -u origin main
```

Review demo sequence: show implementation notes → show recorded environment/tests → run automatic demo or saved model + separate stress commands → show real CSVs and summary → explain the limitations.

## Troubleshooting

- `ModuleNotFoundError`: activate `.venv`, install the tested requirements with that environment's `python -m pip`.
- No alerts or too many alerts: inspect the recorded baseline and VM background activity; collect a longer representative normal baseline. Do not tune on the same test run and present it as independent evaluation.
- Memory stress refused: available RAM is low; close other applications or use `--memory-mb 128`.
- Process anomaly persists after recovery: inspect actual process count; the VM's background processes may have changed. The model is deliberately not retrained on anomalies.
- Gemini errors: fallback is expected; check key/model/account separately, without exposing the key in screenshots.
- eBPF errors: use the stable psutil path.

## Primary references

- [psutil documentation](https://psutil.readthedocs.io/stable/)
- [Isolation Forest API](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html)
- [Gemini generateContent API](https://ai.google.dev/api/generate-content)
- [BCC project](https://github.com/iovisor/bcc)
