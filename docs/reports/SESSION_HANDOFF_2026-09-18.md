# Session Handoff - 2026-09-18

Operational handoff for the next agent. Read this before touching anything.
Read `AGENTS.md` at the repository root first: the commit protocol there is
mandatory. Read `.opencode/skills/agent-delegate/SKILL.md` before delegating to
any agent, and `.opencode/skills/codex-delegate/SKILL.md` before using Codex.

Encoding note: write files as plain ASCII. PowerShell 5.1 `Out-File` and
`Set-Content -Encoding utf8` write a UTF-8 BOM and corrupt accents and the
bullet character. A BOM also makes an attached file read as binary and fail.
Use `[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))`.

## 1. Repository state at handoff

Confirm these yourself; do not trust the numbers below.

```powershell
git status --short
git log --oneline -5
git log --oneline origin/main..HEAD
git branch --show-current
```

At the moment of writing:

- Branch: `main`
- HEAD: `5b099ab` (`fix(risk): make the fail-closed boundary structural`)
- Working tree: clean
- `origin/main`: identical to HEAD, nothing to push
- No extra worktrees, no stashes

The three most recent commits:

```
5b099ab fix(risk): make the fail-closed boundary structural
15ea05b fix(risk): close the three Risk Manager fail-closed findings
840df4b docs: add consolidated project closure record
```

## 2. GitHub authentication

`git push origin main` **works** in this environment; it was used successfully
in this session (`840df4b..5b099ab  main -> main`). This is an observed fact, not
a permanent authorization. If a push fails, treat it as a blocker and report it
to the operator instead of working around it. Never force-push.

## 3. Environment dependencies

- **Python: always the project venv.** `& ".\.venv\Scripts\python.exe"`. The
  global `python` and `py -3.11` do **not** have the project dependencies.
  Verified: venv is Python 3.11.9.
- **Docker Desktop** must be running for PostgreSQL.
- **Node.js** for the Electron console.
- **Ollama daemon** signed in locally when running decision experiments with the
  cloud-model defaults.
- **Codex CLI** (`codex`) for the post-commit adversarial pass.
- The Codex sandbox frequently reports that it cannot run tests or that the venv
  is broken (`EPERM`, "venv points to a missing Python"). That is a sandbox
  limitation, not an environment failure. Run the command yourself and cite the
  real output.

## 4. Required services

```powershell
docker compose up -d db          # PostgreSQL 16, container tgr01-postgres
docker ps --format "{{.Names}} {{.Status}}"
```

The container must be `healthy` before the backend suite. If it is not running,
**438-ish tests fail at setup with a connection timeout**, which looks like a
code failure and is not one. Wait for healthy, then rerun.

The official Hybrid RAG Engine is a **separate** Docker project, not required for
trading. When it is not running the client reports `unavailable`, which is the
expected state in a normal session.

## 5. Project structure

```text
backend/
  agents/       LLM contracts, decision agent, multi-agent shadow pipeline
  analysis/     bounded deterministic analysis tools
  core/         schema, repository, clock, runtime safety, audit, run audit
  data/         public market and news workers
  evaluation/   historical snapshot construction and partition sampling
  execution/    transactional paper simulator, MB read-only private client
  features/     indicators, payload builder, decision memory
  ml/           archived neural research (TCN), excluded from the live path
  ops/          allowlisted commands, process control, experiment runners
  rag/          auxiliary local store and official RAG client
  risk/         deterministic Risk Manager and portfolio guard
  tests/        tests and operational/research utilities
desktop/        React, Vite and Electron operations console
docs/reports/   acceptance, diagnostics, handoffs
docs/research/  retained research
```

`backend/data_exports/` holds the historical dataset and is **outside Git**. It
is not public. Muse Spark must never read it.

## 6. Non-negotiable rules (from `AGENTS.md`)

- Real trading stays disabled. `REAL_TRADING_ENABLED` must remain `false`. Never
  add an order, cancel, transfer or withdrawal endpoint.
- The deterministic Risk Manager is the only component allowed to approve an
  order. Never weaken it, and never let an LLM, an agent or the RAG approve,
  block or size an order.
- Do not modify Risk Manager thresholds or the conviction gate without explicit
  operator sign-off. Prompt calibration is fixed on the prompt side.
- Never commit `.env` files or credentials. Never paste secrets into a prompt.
- Prefer failing closed. A block or a HOLD is a valid, safe outcome.

Current thresholds (all in `backend/risk/risk_manager.py`):

| Constant | Value | Meaning |
| --- | --- | --- |
| `MINIMUM_CONVICTION` | 70 | conviction floor for a directional action |
| `NO_NEWS_MINIMUM_CONVICTION` | 80 | raised floor when there is no usable news |
| `MINIMUM_HYBRID_CONFIDENCE` | 0.50 | **exclusive** floor on conviction x reliability |
| `EXTREME_ATR_RATIO` | 0.05 | ATR/price above this is extreme |
| `LIVE_MAX_EXPOSURE_PCT` | 80.0 | live exposure ceiling (`backend/core/market_policy.py`) |

## 7. Skills

| Skill | Path | Use for |
| --- | --- | --- |
| agent-delegate | `.opencode/skills/agent-delegate/SKILL.md` | provider/model selection, sessions, worktrees, quota, the pre-commit double red-team |
| codex-delegate | `.opencode/skills/codex-delegate/SKILL.md` | driving the Codex CLI (gpt-5.6-sol) for read-only audits and resumes |

Read the relevant skill before spawning any agent that can change files.

## 8. Allowed and forbidden models

Provider default is **Ollama Cloud**, not OpenCode Go. There are two OpenCode Go
subscriptions and they must last the month.

| Role | Model |
| --- | --- |
| Implementation workhorse | `ollama-cloud/glm-5.3-flash` |
| Coding / agentic | `ollama-cloud/kimi-k2.7-code` |
| Hard specialist (rare) | `ollama-cloud/glm-5.3` (~100-300 calls) |
| Reasoning (not routine) | `opencode-go/gpt-5.6-luna --variant high` |
| Red-team A (mandatory) | `ollama-cloud/glm-5.3-flash` |
| Red-team B (mandatory) | `opencode/union-alpha` |
| Risk/security audit | `opencode/muse-spark-1.3-contributor-free` |
| Research / reading | orchestrator + `explore` subagent |

**Forbidden outright:**

- `kimi-k3` (any provider). Almost no gain for the price; one task can consume
  ~30% of the 5-hour window.
- `deepseek-v4-pro` (any provider). Inferior to `deepseek-v4.1-flash`.
- **Muse Spark must never be an orchestrator and must never see sensitive data.**
  It trains on prompts (not ZDR). Use it only on code that is already public.
  Never pass `.env`, tokens, keys, `DATABASE_URL`, or `backend/data_exports/`.

Do not spend OpenCode Go on grep, file reads, exploration or mechanical test
runs. `union-alpha` exposes no variants; do not pass `--variant` to it or to
`kimi-k2.7-code`. Confirm the model that actually ran via `opencode export`
before trusting an answer: a long multi-line prompt passed as an argument can
silently drop `-m`.

## 9. Reviewer and subagent policy

Five agents is a **ceiling, not a target**. Zero is a valid answer. Rungs:

| Situation | Agents |
| --- | --- |
| Trivial, or you can do it yourself | 0 |
| Small/medium bug | 0 or 1 |
| Complex bug | 1 investigates, 1 implements or reviews |
| Important change | 1 implementer + 1 independent reviewer |
| Critical | several investigate different hypotheses; one owns the implementation; the rest review |

**Before any commit, without exception:** the mandatory double red-team on the
exact diff. Red-team A is `ollama-cloud/glm-5.3-flash`; red-team B is
`opencode/union-alpha` (free, keeps Go quota untouched), independently, without
showing A's analysis to B first. If they disagree, do not vote: reproduce, read
the code, run a test, decide with evidence.

**After committing:** the Codex pass with gpt-5.6-sol, effort always explicit:

```powershell
codex exec "<scoped review prompt>" `
  -m gpt-5.6-sol -c 'model_reasoning_effort="medium"' `
  -s read-only `
  -o "C:\Users\danie\AppData\Local\Temp\opencode\review_out.txt" 2>&1
```

`codex exec` accepts `-s read-only`; `codex exec resume` does not, use
`-c 'sandbox_mode="read-only"'`.

A reviewer is **not** read-only by instruction. In this session a reviewer edited
the README it was reviewing, and an earlier one ran `git checkout --`. Always
diff after every review (`git diff` **and** `git diff --cached`), treat reviewer
edits as unreviewed, and freeze the diff by hash before the final gate.

## 10. Test procedures

Backend (needs PostgreSQL healthy; about 3 minutes):

```powershell
& ".\.venv\Scripts\python.exe" -m pytest backend\tests -q
```

Expected at this handoff: **453 passed**. The number rises as tests are added;
always report what you actually observed.

Other backend gates:

```powershell
& ".\.venv\Scripts\python.exe" -m compileall -q backend
& ".\.venv\Scripts\python.exe" .\backend\tests\chaos_monkey.py
& ".\.venv\Scripts\python.exe" .\backend\tests\trading_readiness_report.py
& ".\.venv\Scripts\python.exe" .\backend\tests\dashboard_state.py
```

Desktop:

```powershell
Set-Location .\desktop
npm test                       # expect 35
npm run build
$env:ELECTRON_DISABLE_SANDBOX="1"; npm run test:electron
```

Check the **exit code** of the Electron smoke, not just the printed output.

A `BLOCKED` readiness verdict with stale candle or stale workers is a safe
outcome, not a defect: the workers are not running in a normal session.

## 11. Secrets and data that must never be displayed

Never print, paste, commit or send to an agent:

- the contents of `.env` or `backend/.env`
- `MB_CLIENT_ID`, `MB_CLIENT_SECRET`, `MB_ACCOUNT_ID`
- `DATABASE_URL` / `TEST_DATABASE_URL` with a real password
- `POSTGRES_PASSWORD`
- any API key or OAuth token
- anything under `backend/data_exports/` (not public, outside Git)

`MB_ACCOUNT_ID` is not the same as `MB_CLIENT_ID`; it comes from
`GET /api/v4/accounts` after authentication. `backend/.env.example` ships it
commented out for that reason.

When reporting a connection string, mask the password (`tgr01:***@`).

## 12. The 12-24 hour run: objective and frozen state

The next session's main goal is a **12-24 hour real paper-trading run against the
live pipeline**, with roughly **R$20 of balance in the Mercado Bitcoin account**.
It is an **observability and evaluation experiment**, not a profitability test.
The financial result of the window is one data point, not proof of edge.

**Before the run starts, freeze:** code, prompts, configuration, models, the Risk
Manager, the database schema and all parameters.

**After the run starts, no agent may modify** files, prompts, configs, rules,
database/schema, or pipeline behaviour. Any correction happens before the run or
after it ends. The run must not be used to test new features.

### Per-decision persistence required

For every decision and model, persist:

`reasoning_raw`, `reasoning_summary`, `output_raw`, `output_validated`,
`decision_proposed`, `decision_final`, `risk_manager_input`,
`risk_manager_output`, `risk_adjustments`, `latency_ms`, `token_usage`,
`estimated_cost`, `model`, `provider`, `prompt_hash`, `input_hash`,
`snapshot_hash`, `commit_hash`, `run_id`, `decision_id`, `timestamp`, plus the
execution data and the later outcome.

**This schema does not exist yet.** The current `trading_runs` and `trade_logs`
tables store a subset (model, decision, risk verdict, snapshot, execution
audit). Building the full column set above is the first task of the next session
and must be finished and frozen **before** the run starts. Treat it as new
schema work requiring its own review, not as a quick addition mid-run.

### Kimi and GLM must be recorded separately

Record Kimi and GLM separately, and when they are compared guarantee they
receive the **same logical snapshot**. Register `snapshot_hash` and `as_of`
unequivocally so two rows can be proven to describe the same market instant.

### Also record

Errors, timeouts, retries, fallbacks, rejected validations, and decisions blocked
by the Risk Manager. A blocked decision is a result, not a failure.

### During the run

Reviewers and auxiliary agents must not interfere with decisions. Analysis
happens **after** the run ends, on the frozen data.

### At the end

Evaluate prompts, exposed reasoning, decisions, the Risk Manager, latency, cost,
integration errors and model behaviour. The financial result of the window is an
additional data point only.

## 13. Remaining pending work

1. **Build and freeze the decision-observability schema** described in section
   12. This blocks the run.
2. **Decide the model comparison design**: how Kimi and GLM share one logical
   snapshot, and how `snapshot_hash` is computed so equality is provable.
3. **Confirm the real account fee.** `PAPER_FEE_RATE=0.003` is a configuration
   premise, not a measurement. `backend/tests/validate_mb_order_dry_run.py`
   reads the real fees from the authenticated exchange client, but it needs all
   three credentials in `backend/.env` (`MB_CLIENT_ID`, `MB_CLIENT_SECRET`,
   `MB_ACCOUNT_ID`). The cost reading does not scale linearly: the minimum
   slippage of 0.05% per side remains as a floor.
4. **Define the kill conditions for the run** before it starts: what stops it
   early (worker death, repeated timeouts, capital anomaly, clock drift) and who
   decides.
5. **Reconcile the paper position** before the run so the starting balance is
   explicit and auditable.

## 14. Decisions explicitly NOT taken

Do not treat any of these as settled; each needs the operator.

1. **Leverage / horizon.** Either a longer horizon (hours to days) or trading
   rarely with the cost in the payload. Not chosen.
2. **Real fee confirmation.** Not done, blocked on credentials.
3. **Opening the sealed `holdout` partition.** Still sealed; requires
   `--holdout-approval` with the exact `dataset_id`
   (`8504c29db1253e2a5d8bdafe`) and only after prompts and rules are frozen.
   The `validation` partition was **already used** by the 2026-08-10 campaign;
   it is not untouched.
4. **Whether exposure reduction should stay possible in a crash.** The exclusive
   confidence floor currently holds every SELL when the ATR ratio is extreme,
   because reliability is capped at 0.5 and the floor is exclusive. This is the
   fail-closed behaviour and it is deliberate, but it is a design decision the
   operator has not revisited. Changing it needs sign-off and a code change, not
   a comment.
5. **Reopening the TCN.** Archived, no executable edge demonstrated. Reopening
   requires a new pre-registered experiment per
   `docs/research/tcn_reopening_protocol.md`.
6. **npm devDependency advisories.** Four of six clear with in-range upgrades;
   `electron`/`extract-zip` need `--force`, which leaves the pinned range. Not
   done; it is a dependency change, not consolidation.

## 15. What not to trust

- **Do not trust a test count written anywhere.** Run the suite and report what
  you saw.
- **Do not trust a reviewer's "I did not edit anything".** Check the diff.
- **Do not trust the Codex sandbox's failure reports.** It frequently cannot run
  the suite or see the venv; that is its sandbox, not this environment.
- **Do not trust synthetic fixtures as evidence about live data.** The single
  highest-value bug in this repository's UI work came from running the real app
  against live data, because every fixture used the wrong payload shape.
- **Do not describe `validation` as untouched.** It was used on 2026-08-10.
  Only `holdout` is sealed.
- **Do not treat the pre-fix reachability numbers in
  `PROJECT_CLOSURE_2026-09-17.md` as current behaviour.** They describe the code
  before the fix and are retained so the paths cannot return unnoticed.

## 16. Pointer

- Closing record with the fixed findings and pre-fix measurements:
  `docs/reports/PROJECT_CLOSURE_2026-09-17.md`
- Paper-only acceptance boundary: `docs/reports/FINAL_ACCEPTANCE.md`
- Cost and edge diagnostic: `docs/reports/EDGE_AND_COST_DIAGNOSTIC_2026-09-16.md`
- Previous handoffs: `docs/reports/SESSION_HANDOFF_2026-09-17.md` and
  `docs/reports/SESSION_HANDOFF_2026-09-16.md`
