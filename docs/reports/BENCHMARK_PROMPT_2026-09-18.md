# Benchmark prompt for the next agent

Paste the block below to the next agent as its task. It is written to be copied
verbatim. It assumes the agent has read `AGENTS.md` and
`docs/reports/SESSION_HANDOFF_2026-09-18.md`.

Before pasting, confirm the HEAD in the prompt matches the real HEAD
(`git rev-parse HEAD`). Adjust if commits landed since.

---

## TASK

You are running a **performance benchmark of this repository and of the LLM
models it uses**, in `D:\tgr01-trading-llmv2`. This is a **measurement task, not
a development task.**

### Hard rules

1. **Do not modify the pipeline.** You may add new benchmark scripts under
   `backend/tests/` and new report files under `backend/reports/` (git-ignored).
   You may **not** change `backend/risk/`, `backend/features/`,
   `backend/agents/`, `backend/main.py`, the prompts, the thresholds, or the
   database schema. If a measurement seems to require a code change, stop and
   report it instead.
2. **Real trading stays disabled.** `REAL_TRADING_ENABLED=false`. Never add an
   order, cancel, transfer or withdrawal call. The Mercado Bitcoin client is
   GET-only plus the OAuth token exchange; keep it that way.
3. **Do not commit anything.** Produce reports only. The operator reviews before
   any commit. If you believe a commit is warranted, ask first.
4. **Never print or persist secrets.** No `.env` contents, no `MB_CLIENT_ID`,
   `MB_CLIENT_SECRET`, `MB_ACCOUNT_ID`, no `DATABASE_URL` password, no API keys.
   Mask passwords as `tgr01:***@` when quoting a connection string.
5. **Use the project venv, always:** `& ".\.venv\Scripts\python.exe"`. The global
   `python` lacks the dependencies.
6. **A blocked outcome is a valid result.** A HOLD, a refusal, or a timeout is
   data, not a failure to hide. Never loosen a gate to make a benchmark "pass".

### Before you start

```powershell
git rev-parse HEAD                 # expect 3736bb7 or a later commit
git status --short                 # expect clean; note anything else
docker compose up -d db            # PostgreSQL 16, container tgr01-postgres
docker ps --format "{{.Names}} {{.Status}}"    # wait for healthy
& ".\.venv\Scripts\python.exe" -m pytest backend\tests -q   # expect 453 passed
```

Record the HEAD, the timestamp, the machine you ran on, and the exact command
lines in the final report. If the baseline suite does not pass, stop and report
that instead of benchmarking a broken tree.

### What to measure

Produce a single report with these sections. For every number, state how it was
obtained.

#### A. Repository performance (no LLM cost)

Measure the deterministic path only. Do not call any model in this section.

1. **Test suite wall time**, cold and warm:
   `& ".\.venv\Scripts\python.exe" -m pytest backend\tests -q` and record the
   reported duration. Run it twice and report both.
2. **Indicator throughput.** Time `calculate_technical_status` on a fixed frame
   (for example 5,000 one-minute candles) with `time.perf_counter`. Report
   milliseconds per call and candles per second. Use a frame you construct
   deterministically so the number is reproducible.
3. **Payload build time.** Time `build_agent_payload` against the live database
   (with the workers stopped, so the result is a stale-payload abort rather than
   a model call) and report the duration separately for the DB read and the
   technical calculation if you can instrument them without changing production
   code.
4. **Serializer throughput.** Time `serialize_payload_snapshot` on a real
   captured payload, N=1,000 iterations, report microseconds per call.
5. **Database round trips.** Time `repository.get_system_health`,
   `get_virtual_portfolio` and `get_recent_news` with 100 iterations each.
   Report p50 and p95, not only the mean.
6. **Desktop build and test time.** In `desktop`: `npm test` and `npm run build`,
   report durations and the reported test count (expect 35).

#### B. Model performance (consumes provider quota)

**First, count the calls and get the operator's go-ahead before spending.**
Print an estimate: models x scenarios x cycles. Do not start a campaign that
consumes quota without that number in the report.

Use the existing harnesses rather than writing a new one where possible:

- Contract and quality matrix: `backend\tests\redteam_llm_matrix.py`
- Profile and model comparison: `backend\tests\compare_llm_models.py`
- Tool-augmented benchmark: `backend\tests\benchmark_tool_augmented_llm.py`

For each model, report:

1. **Contract validity rate**: valid structured outputs / attempts. A malformed
   output that fails Pydantic is a data point, not something to retry until it
   passes.
2. **Latency**: p50 and p95 in seconds, measured wall clock per call. Report the
   cold-start call separately from the warm calls.
3. **Token usage and estimated cost** when the provider exposes them. If it does
   not, say so explicitly rather than estimating.
4. **Directional quality**: on the frozen scenarios, agreement with the expected
   action, and whether the output violates the payload constraints
   (`enforce_payload_decision_constraints` rewrote it).
5. **Safety behaviour**: on the adversarial scenarios, confirm the model never
   produces a directional action on absent or contradictory evidence. A model
   that fails this is disqualified regardless of quality.

Models to compare, per the allowlist:

- `ollama-cloud/kimi-k2.7-code` (current decision default)
- `ollama-cloud/glm-5.3` (news and technical default)
- `ollama-cloud/glm-5.3-flash` (cheaper alternative)

Do **not** benchmark `kimi-k3`, `deepseek-v4-pro`, or Muse Spark. Do not spend
OpenCode Go quota on this; if you need a Go model, ask first.

When comparing two models, guarantee they receive the **same logical snapshot**.
Record `snapshot_hash` and `as_of` for every call so two rows can be proven to
describe the same market instant.

#### C. Risk Manager behaviour under benchmark

This is a read-only characterization, not a change. Time `evaluate_order` on a
fixed set of payloads (clean, stale, extreme ATR, missing indicators) and report
the decision and the wall time for each. Confirm the decisions match the
documented table in `docs/reports/PROJECT_CLOSURE_2026-09-17.md`. Any mismatch is
a finding to report, not to fix.

### Output

Write the report to `backend/reports/benchmark_YYYYMMDD_HHMMSS.md` (git-ignored)
and print the path. Include:

1. Environment: HEAD, date, OS, Python version, CPU/RAM, Docker status.
2. The exact commands you ran.
3. Every measurement with its method, not only the result.
4. An explicit **uncertainty** note per number: sample size, variance if you
   sampled, and what the number does not cover.
5. A **"what I could not measure"** section. Honest gaps are more useful than a
   guessed number.
6. A short list of defects or anomalies you observed while benchmarking. Do not
   fix them.

Do not claim anything about profitability or edge. This benchmark measures
latency, cost, contract validity and safety behaviour, not returns. If you want
to state an interpretation, mark it clearly as an interpretation and separate it
from the measurements.

### Reporting back

Give the operator: the report path, the headline numbers per section, the quota
consumed, and any anomaly worth a follow-up. Keep it short; the report carries
the detail.
