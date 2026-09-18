# Session Handoff - TCN: test, improve and validate (2026-09-18)

Operational handoff for the next agent working on the archived TCN. Read
`AGENTS.md` first: the commit protocol there is mandatory. Read this whole
document before running anything, because two of the prerequisites no longer
exist on disk and one of them is not obvious.

Encoding: write files as plain ASCII. PowerShell 5.1 `Out-File` and
`Set-Content -Encoding utf8` write a UTF-8 BOM and corrupt accents and the
bullet character. Use
`[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))`.

## 1. What the TCN is, and what it is allowed to do

The TCN is an offline research model, not a trading model. It cannot authorize,
size, block or execute an order, and it is not in the Decision Agent payload.
`TCNAdvisor` returns `status=RESEARCH_ONLY`, `execution_eligible=false`,
`can_authorize_order=false`.

Do not weaken that boundary. Do not add the TCN to the live payload. Do not let
it influence the Risk Manager in any way. The deterministic Risk Manager remains
the only component allowed to approve an order.

Files that define the boundary:

- `backend/ml/ARCHIVED.md` -- the archive boundary and reopening criteria.
- `docs/research/tcn_reopening_protocol.md` -- the pre-registered protocol.
- `docs/research/tcn_neural_research_report.md` -- the failed experiment record.

## 2. Repository state at handoff

Confirm these yourself.

```powershell
git status --short
git log --oneline -5
git log --oneline origin/main..HEAD
```

At the moment of writing: branch `main`, HEAD `db492f9`, working tree clean,
`origin/main` identical to HEAD, no worktrees, no stashes.

Two branches exist: `main` and `fix/wilder-rsi-atr-nan` (pre-existing).

## 3. Environment, and the CUDA problem

**The venv has a CPU-only PyTorch build.**

```
torch 2.14.0+cpu
cuda available: False
device count: 0
```

This matters more than anything else in this document. The archived experiment
was trained on CUDA (`--device cuda` in the reproduction block). Every training
command in `docs/research/tcn_neural_research_report.md` assumes a GPU.

Consequences:

- A full retrain on CPU is not practical at the documented scale: 4.6M global
  examples and 969K local rows, six-level TCN, 12 epochs. Estimate your own
  wall-clock before starting; do not assume it will finish.
- If a GPU is available on this machine, install the CUDA build of torch:
  `& ".\.venv\Scripts\python.exe" -m pip install torch --index-url https://download.pytorch.org/whl/cu<version>`
  Confirm the toolchain against `nvidia-smi` first. **Verify `torch.cuda.is_available()`
  before and after**; do not trust the install message.
- If there is no GPU, design around it: smaller sequences, fewer epochs, a
  bounded row cap via `--maximum-sequences`, and say in the report that the run
  was CPU-bound and not comparable to the archived numbers.

Never present a CPU-bound smoke result as if it were a full experiment.

## 4. The two missing datasets

The reproduction block in the research report references files that **do not
exist** on disk. Verified:

| Path | Exists |
| --- | --- |
| `backend/reports/binance_full_dataset.csv` | **no** |
| `backend/reports/mb_tcn_dataset.csv` | **no** |
| `backend/reports/binance_barrier_targets.npz` | **no** |
| `backend/reports/mb_barrier_targets.npz` | **no** |
| `backend/reports/tcn_barrier_final/local_best.pt` | **no** |

Everything under `backend/reports/` is git-ignored, so the artifacts were lost
with the working copy. **You must regenerate them.** This is the first real
task, and it is the step most likely to fail.

What does exist:

- **Local BTC/BRL 1-minute data**: `backend/data_exports/mercado_bitcoin_btc_brl_1m/`
  (43.5 MB, a merged `btc_brl_1m.csv` plus 29 chunk files).
  Range: **2026-03-01 to 2026-09-01** (185 days).
  Coverage measured: **192,161 rows out of 266,400 expected minutes = 72.13%**,
  36,498 gaps, largest gap 222 minutes.
- The chunk directory that `build_tcn_sequence_dataset.py` reads by default.

The coverage number matters: the archived experiment used 969,131 local rows.
You have roughly a fifth of that, over 185 days, with a quarter of the minutes
missing. Any comparison against the archived numbers is therefore not
apples-to-apples, and the report must say so.

### Regenerating the datasets, in order

```powershell
# 1. Local featured dataset (this consumes the raw csv, so use a raw path that exists)
& ".\.venv\Scripts\python.exe" .\backend\tests\build_tcn_sequence_dataset.py
# -> backend/reports/mb_tcn_dataset.csv

# 2. Global BTCUSDT dataset. Requires network; downloads checksum-verified
#    monthly archives from Binance. Bound it for a smoke test first.
& ".\.venv\Scripts\python.exe" .\backend\tests\download_binance_history.py `
  --from-month 2026-03 --to-month 2026-09 --max-months 3
# -> backend/data_exports/binance_btcusdt_1m/ (merged csv inside)

# 3. Barrier targets for both (the archived path, note the barrier values)
& ".\.venv\Scripts\python.exe" .\backend\tests\build_barrier_targets.py `
  .\backend\reports\mb_tcn_dataset.csv `
  --output .\backend\reports\mb_barrier_targets.npz `
  --horizons 15 60 --barrier-pct 0.20 0.40

# 4. Slow-horizon dataset (the protocol's cadence and horizons)
& ".\.venv\Scripts\python.exe" .\backend\tests\build_slow_tcn_dataset.py `
  --cadence-minutes 15 --horizons 240 1440 --actionable-move-pct 0.25
# -> backend/reports/mb_slow_tcn_v2.csv
```

If the Binance download fails (no network, or the archive layout changed), **say
so and stop that branch**. Do not substitute another exchange silently: mixing
providers changes the experiment's semantics, which is a documented limitation
in `FINAL_ACCEPTANCE.md`.

### Which path to take

There are now **two** TCN lines, and you must pick one deliberately rather than
mixing them:

- **The archived line** (`train_tcn.py`, 1-minute context, 15/60-minute
  first-touch barriers, quantile + direction heads). This reproduces the failed
  experiment. Its value is as a baseline, not as a candidate.
- **The slow line** (`train_slow_tcn_v2.py` + `build_slow_tcn_dataset.py`). This
  is the reopening protocol's answer to the objective mismatch: 15-minute
  decision cadence, 240-minute and 1440-minute horizons, sequence length 48,
  synchronized global features. **This already exists and is tested** -- 36 TCN
  tests pass, including three that specifically assert the slow dataset's
  causality and cadence.

The protocol was written because the archived direction head learned first-touch
barrier classes while the economic policy ignored that head. The slow line is
the corrected design. **Prefer the slow line**; use the archived line only to
reproduce the baseline for comparison.

## 5. What already exists, so you do not rebuild it

This is the most important section for saving time. The reopening protocol is
**largely implemented already**:

| Component | File | Status |
| --- | --- | --- |
| Causal slow-horizon dataset | `backend/ml/slow_dataset.py` | implemented, tested |
| Dataset builder | `backend/tests/build_slow_tcn_dataset.py` | implemented, tested |
| Purged walk-forward folds | `backend/ml/sequences.py` | implemented, tested |
| Safe checkpoint schema | `backend/ml/checkpoints.py` | implemented, tested |
| Abstaining probability policy | `backend/ml/policy.py` | implemented, tested |
| Policy threshold fitting on calibration only | `policy.fit_probability_policy` | implemented, tested |
| Fail-closed advisor | `backend/ml/inference.py` | implemented, tested |
| Readiness gate | `backend/ml/readiness.py` | implemented, tested |
| Slow training entry point | `backend/tests/train_slow_tcn_v2.py` | implemented, tested |

Git history worth reading before you change anything:

```
88b8526 feat(ml): add causal slow-horizon TCN dataset
7e64de3 refactor(ml): add purged TCN walk-forward protocol
44d772e feat(ml): calibrate abstaining TCN probability policy
```

**Your job is to run it, find what is wrong with it, and improve it -- not to
write it from scratch.** If you find yourself implementing a feature the
protocol describes, first check whether it exists and is merely unexercised.

## 6. What "improve" means here

The protocol's own diagnosis is that the earlier result failed for an
**objective mismatch**, not for lack of capacity. Larger channels and removing
pretraining were both tried and neither helped. Do not start by making the model
bigger.

Places where improvement is legitimate and testable:

1. **The policy/threshold coupling.** The archived quantile policy abstained on
   every test row. The corrected policy fits thresholds on calibration data
   only. Verify that the fitted policy actually trades on the calibration
   window, and report the abstention rate honestly.
2. **Class imbalance.** BUY precision was 45.23% (15m) and 44.09% (60m) while
   SELL precision was 54.95%/57.74%. The model was one-sided. The class-weight
   power is a parameter (`--class-weight-power`); changing it is an experiment,
   not a fix.
3. **The horizon/cost relationship.** This is the crux and it is already
   measured: the configured round-trip cost is 0.70% for BUY and 0.35% for SELL,
   while the median absolute 60-minute move was 0.470%
   (`docs/reports/EDGE_AND_COST_DIAGNOSTIC_2026-09-16.md`). The 240-minute and
   1440-minute horizons exist precisely because of this. Whether the longer
   horizon clears the cost is **the open question this work is meant to answer**.
4. **Causal feature additions**, as the protocol lists: synchronized global
   market features, calendar/session features, or other information genuinely
   available at decision time. Not reconstructed news -- the protocol forbids it
   unless publication timestamps can be proven.
5. **Calibration**, which was already reasonable (ECE 2.36%/2.85%). Do not break
   it while chasing accuracy.

## 7. The evaluation contract you must honour

From `docs/research/tcn_reopening_protocol.md`. Do not relax any of it:

- Compare every candidate on **identical timestamps** against: always HOLD,
  simple momentum, trend confirmation, RSI mean reversion, the archived TCN
  configuration, and a small non-neural classifier where practical.
- Model and policy thresholds may use train, selection and calibration windows.
  **They may never use a fold's test window.** The final holdout is opened once,
  after everything is frozen.
- Use non-overlapping positions, and include costs and drawdown in the policy
  evaluation.
- Report: balanced accuracy, macro F1, per-class precision and recall;
  calibration error, NLL, Brier; signal count, executed trades, overlap skips,
  abstention rate; net return after fees and slippage, win rate, average trade,
  drawdown; block-bootstrap confidence intervals across contiguous time blocks;
  metrics by regime and by fold; all-zero/HOLD and simple-rule baselines on the
  same rows.

A result without the baselines on the same rows is not a result.

## 8. The promotion gate (do not shortcut it)

The TCN may become read-only LLM evidence only when all of these hold:

1. Multiple walk-forward test folds show repeatable improvement over simple
   baselines **after costs**.
2. Confidence intervals do not indicate the result comes from one isolated
   regime.
3. The final untouched holdout confirms the frozen policy.
4. BUY and SELL are both supported; aggregate accuracy cannot hide a one-sided
   model.
5. Dataset provenance, hashes, feature definitions, model configuration and
   policy thresholds are persisted.
6. Failure or absence degrades to `UNAVAILABLE`, never to permission to trade.

**Expect to fail this gate.** The archived model failed it, and the honest
outcome of this work may be "still no edge". A negative result with baselines,
confidence intervals and a reproducible configuration is a **successful
deliverable** for this task. Do not tune until something looks positive.

## 9. Test and validation procedure

```powershell
# The TCN's own suite (fast, no training)
& ".\.venv\Scripts\python.exe" -m pytest backend\tests\test_tcn_archive_boundary.py `
  backend\tests\test_tcn_inference.py backend\tests\test_tcn_policy.py `
  backend\tests\test_tcn_training.py backend\tests\test_slow_tcn_dataset.py -q
# expected: 36 passed

# Full backend suite before you commit anything
& ".\.venv\Scripts\python.exe" -m pytest backend\tests -q
# expected: 453 passed at handoff; PostgreSQL must be healthy first

docker compose up -d db
docker ps --format "{{.Names}} {{.Status}}"   # wait for healthy
& ".\.venv\Scripts\python.exe" -m compileall -q backend
```

The desktop suite is not affected by this work unless you touch the console.

To inspect a trained checkpoint:

```powershell
& ".\.venv\Scripts\python.exe" .\backend\tests\inspect_tcn_advisory.py `
  --checkpoint .\backend\reports\<run>\local_best.pt --device cpu
```

The advisor is designed to **refuse** a checkpoint that was not barrier-trained,
lacks a reserved temporal test, lacks calibrated temperatures, or falls below
the balanced-accuracy floor. A refusal is correct behaviour, not a bug to work
around.

## 10. Non-negotiables

- Real trading stays disabled; `REAL_TRADING_ENABLED` remains `false`.
- The Risk Manager is untouched. Do not modify thresholds, the conviction gate,
  or any risk rule. If the TCN work seems to require it, stop and report.
- The TCN cannot enter an order-approval path. Ever.
- Never commit `.env` files, credentials, or anything from
  `backend/data_exports/` (not public, outside Git).
- Prefer failing closed. An `UNAVAILABLE` or a refusal is a valid outcome.
- Generated datasets, checkpoints and reports stay under `backend/reports/`
  (git-ignored). Do not commit them.

## 11. Commit protocol

Read `AGENTS.md`. Before any commit: one independent subagent validator on the
exact diff, then the double red-team (`ollama-cloud/glm-5.3-flash` and
`opencode/union-alpha`, independently, without showing one the other's
analysis). After committing: `codex exec` with gpt-5.6-sol, effort explicit
(`-c 'model_reasoning_effort="medium"'`), `-s read-only`.

A reviewer is not read-only by instruction. Check `git diff` **and**
`git diff --cached` after every review, and treat reviewer edits as unreviewed.

## 12. Deliverables

1. **Regenerated artifacts** under `backend/reports/`, with the exact commands
   and the measured coverage, or a clear statement of what could not be
   regenerated and why.
2. **A report** under `docs/reports/` following the evaluation contract in
   section 7, including the baselines on the same rows and the confidence
   intervals.
3. **Any code improvement** you make, with tests, committed under the protocol
   in section 11.
4. **An explicit statement** of whether the promotion gate was met. "Not met,
   here is the evidence" is a complete and acceptable answer.

## 13. Open questions the operator has not decided

Do not treat these as settled:

1. **Is a GPU available?** The current venv is CPU-only. If a GPU exists,
   installing the CUDA torch build changes what is feasible. If not, the scope
   of a "full" experiment has to shrink. Ask before spending hours.
2. **Is network access allowed for the Binance download?** The global dataset is
   an ablation, not an assumption, so the protocol permits skipping it -- but
   that must be a stated decision, not a silent omission.
3. **Which line is the priority** if time is short: reproduce the archived
   baseline, or push the slow line forward. The protocol implies the slow line.
4. **What is the wall-clock budget?** Training is the expensive step and it is
   not bounded anywhere.
5. **When to open the final holdout.** The protocol says once, after everything
   is frozen. It has never been opened. Confirm with the operator before doing
   it, and record the dataset hash.

## 14. What not to trust

- **Do not trust the archived numbers as a target.** The archived local dataset
  had 969,131 rows; the current source has 192,161 with 72.13% coverage. A
  different result is expected and is not a regression.
- **Do not trust a test count written anywhere.** Run the suite.
- **Do not trust the CPU smoke run as an experiment.** Label it clearly.
- **Do not treat the 36 TCN tests as evidence of edge.** They assert causality,
  shape, checkpoint safety and the archive boundary. None of them measures
  predictive value.
- **Do not report a metric without its baseline on the same rows.**

## 15. Pointer

- Protocol: `docs/research/tcn_reopening_protocol.md`
- Failed experiment record: `docs/research/tcn_neural_research_report.md`
- Archive boundary: `backend/ml/ARCHIVED.md`
- Cost/edge reality check for the horizon question:
  `docs/reports/EDGE_AND_COST_DIAGNOSTIC_2026-09-16.md`
- The live-pipeline handoff, a separate concern:
  `docs/reports/SESSION_HANDOFF_2026-09-18.md`
