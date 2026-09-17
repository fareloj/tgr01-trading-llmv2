# Project Closure - 2026-09-17

Consolidated closing record for the TGR-01 Trading LLM V2 paper-trading
research project. This document does not reopen scope and adds no feature. It
records what was verified, what is deliberately left open, and what still
depends on the operator.

Read `AGENTS.md` before acting on anything here. The deterministic Risk Manager
is the only component allowed to approve an order, and the findings in the
"Open findings" section touch it, so acting on them requires explicit operator
sign-off.

## Scope of this closure

The operator decision was: **finish and consolidate, add no new feature.** This
session therefore made no change to runtime logic. The only deliverables are
this record and corrections to stale acceptance documentation.

## Evidence verified on 2026-09-17

Every line below was executed in this environment, not copied from an earlier
report. The project interpreter is always the venv.

| Check | Command | Result |
| --- | --- | --- |
| Backend suite | `& ".\.venv\Scripts\python.exe" -m pytest backend\tests -q` | 438 passed in 228.08s |
| Backend compile | `& ".\.venv\Scripts\python.exe" -m compileall -q backend` | exit 0 |
| Desktop unit tests | `npm test` (in `desktop`) | 35 passed, 0 failed |
| Desktop build | `npm run build` | Vite 6.4.3 built, exit 0 |
| Electron smoke | `$env:ELECTRON_DISABLE_SANDBOX="1"; npm run test:electron` | exit 0, `rendererErrors: []` |
| PostgreSQL | `docker ps` | `tgr01-postgres` healthy on 5432 |
| Dashboard state | `& ".\.venv\Scripts\python.exe" .\backend\tests\dashboard_state.py` | exit 0, JSON state emitted (no readiness verdict in this script) |
| Readiness report | `& ".\.venv\Scripts\python.exe" .\backend\tests\trading_readiness_report.py` | verdict `BLOCKED` (stale candle and stale workers) |
| Doc link integrity | local markdown link check | 0 broken links in 18 files |
| Runtime npm deps | `npm audit --omit=dev` (in `desktop`) | 0 vulnerabilities |
| Full npm audit | `npm audit` (in `desktop`) | 6 advisories, all in devDependencies |
| External RAG | `& ".\.venv\Scripts\python.exe" .\backend\tests\query_external_rag.py --health` | `unavailable` (service not running) |

Notes on the two non-green-looking results, which are correct behaviour:

- `BLOCKED` readiness is a safe outcome. The workers were not running during
  this session, so the candle and heartbeats were stale. This is the intended
  fail-closed response, not a defect.
- The external Hybrid RAG service is a separate Docker project and was not
  started. Its availability is not part of the trading path and retrieval
  cannot approve, block or size an order.

Note on `npm audit`: the six advisories are `electron`, `extract-zip`,
`browserslist`, `baseline-browser-mapping`, `joi` and `nanoid`, reached through
devDependencies only. `npm audit --omit=dev` reports zero, so nothing ships in
the production renderer bundle. Four of the six clear with in-range upgrades
(`browserslist`, `baseline-browser-mapping`, `joi`, `nanoid`); only
`electron`/`extract-zip` need `npm audit fix --force`, which moves Electron
outside the pinned range. Fixing them is a dependency change, not consolidation,
so none was performed. The distinction matters because a blanket `--force` is
more disruptive than the situation requires.

## Partition status, stated precisely

An earlier handoff described the evaluation partitions imprecisely. The verified
state is:

- `development`: used by the 2026-09-16 edge and cost campaign (120 samples).
- `validation`: **already used** by the 2026-08-10 multi-agent campaign
  (50 stratified snapshots, 150 calls). It is not untouched.
- `holdout`: **sealed and never evaluated**. `run_historical_campaign.py:349`
  refuses to open it unless `--holdout-approval` carries the exact
  `dataset_id`. The manifest on disk reports
  `dataset_id = 8504c29db1253e2a5d8bdafe` with all three partitions present.

Do not describe `validation` as untouched in future work.

Dated reports still contain this wording in several places, and they were
deliberately left as historical records rather than rewritten. Rather than give a
census that will go stale the next time someone greps, the durable rule is:

> The authoritative partition status is this section, not any sentence in a dated
> report. Before 2026-08-10, `validation` really was untouched, so reports written
> then are not evidence of a mistake. From 2026-08-10 onward, any sentence saying
> `validation` was untouched is stale and must not be repeated.

The date boundary is the campaign that first used `validation`, documented in
`MULTI_AGENT_HISTORICAL_VALIDATION_2026-08-10.md`. A report dated *exactly*
2026-08-10 should not be assumed to predate that campaign; the report itself is
the only authority for what it did.

The examples below fall into three categories, and the rule applies to them
differently:

- **True when written, later superseded** (dated 2026-08-04, before first use):
  `HISTORICAL_DEVELOPMENT_CAMPAIGN_2026-08-04.md:78` says "validation remains
  untouched and holdout remains sealed", and
  `HISTORICAL_DEVELOPMENT_BALANCED_CORRECTED.md:213` says `validation` "has not
  been opened; `holdout` remains sealed and was never accessed". Neither was
  corrected, correctly.
- **Stale and superseded** (dated 2026-09-16, after first use):
  `SESSION_HANDOFF_2026-09-16.md:209` says `validation` and `holdout` "nunca
  foram tocados"; the next day's handoff corrected this in
  `SESSION_HANDOFF_2026-09-17.md:199-207`.
  `EDGE_AND_COST_DIAGNOSTIC_2026-09-16.md:187` repeats the wording in its
  limitations. That one is accurate for the diagnostic itself, which used only
  `development`, but it reads as a global claim.
- **Not a historical claim at all**:
  `EDGE_AND_COST_DIAGNOSTIC_2026-09-16.md:157-158` states, as a proposal for a
  *future* campaign, that `validation` and `holdout` would remain untouched. It
  describes what that experiment would do, not what happened before it, so the
  rule does not apply to it.

The list is illustrative, not exhaustive.

`README.md` originally repeated the stale claim and was corrected in this change,
because the README is current-state documentation rather than a dated record.

## Open findings in the Risk Manager, with reachability evidence

A risk auditor reported three pre-existing defects in
`backend/risk/risk_manager.py`. All three were reproduced in this session. The
operator decided to leave them open for now. What follows adds the reachability
evidence that was missing, so the decision is informed rather than assumed.

Reproduction harness for all three (run with the project venv and `PYTHONPATH`
set to the repository root):

```python
from backend.risk.risk_manager import RiskManager
import backend.core.repository as repo
repo.get_last_action_timestamp = lambda *a, **k: None
rm = RiskManager(max_exposure=80.0, cooldown_minutes=0)
```

### (a) `hybrid_confidence == 0.50` passes the `< 0.50` gate

`risk_manager.py:183` blocks when `hybrid_confidence < MINIMUM_HYBRID_CONFIDENCE`
(0.50), so exactly 0.50 is approved. The floor is inclusive while the comparison
reads as exclusive.

Reproduced:

- `SELL` with conviction 100, ATR at 20% of price (so `sys_rel` is exactly 0.5),
  fresh news and no red flag returns
  `{'action': 'SELL', 'reason': 'Aprovado. Confianca Hibrida: 50.0%. ...'}`.
- `SELL` with conviction 99 in the same payload returns `HOLD`
  ("Confianca Hibrida muito baixa (49.5%)"), confirming 50.0 is the exact
  boundary.

Reachability, measured rather than assumed:

- Exact equality needs `conviction * sys_rel == 50`. The reliability multipliers
  are `x0.7` no news, `x0.3` stale market, `x0.6` stale news, `x0.7` red flag and
  `x0.5` extreme ATR. The stale-market `x0.3` factor is **not** gate-reachable:
  stale market data blocks both BUY and SELL in `_directional_gate`
  (`risk_manager.py:230-231`) before the hybrid check, reproduced. Removing it,
  the reachable base multipliers are `{0.5, 0.6, 0.7}`, plus `1.0` for the
  no-penalty path. Their products are `sys_rel` values too, and `0.3` *is*
  reachable as a product (`0.6 * 0.5`); it just cannot hit the floor, because it
  would need conviction 166.7. The products that can reach the floor are `0.5`,
  `0.6`, `0.7` and `1.0`, with required convictions 100, 83.3, 71.4 and 50.
- Over **integer** convictions in `[1, 100]`, the exhaustive search over products
  of those multipliers finds only two exact hits: `conviction 100` with
  `sys_rel 0.5`, and `conviction 50` with `sys_rel 1.0`.
- `conviction 50` can never reach the gate: `MINIMUM_CONVICTION` (70) blocks it
  first. So at integer conviction the only gate-reachable path is conviction 100
  with `sys_rel` 0.5.
- Fractional convictions widen this. `evaluate_order` accepts a float
  (`risk_manager.py:114`), and `conviction = 71.428571...` with `sys_rel = 0.7`
  also lands exactly on 0.50 (measured). Every real producer truncates to int --
  `DecisionOutput` floors floats (`contracts.py:26-36`), the multi-agent schema
  caps at 80 (`contracts.py:230`) and the model-comparison harness casts `int()`
  -- so the integer result is the one that applies in practice. The float case is
  recorded so the claim is not overstated.
- On the live runtime path this is unreachable. `enforce_payload_decision_constraints`
  caps conviction at 80 (`decision_agent.py:216-217`), and 80 * 0.5 = 40, far
  below the floor. No product of the multipliers equals 0.625, which is what
  80 would need.
- It **is** reachable from the model-comparison harness.
  `compare_llm_models.py:203-211` calls `evaluate_order` with the decision
  conviction cast to `int` and does **not** apply the live 80 cap;
  `DecisionOutput` itself permits up to 100. The `int` cast means this harness
  exercises the integer case only, so it does not demonstrate the float case
  described above.

Impact: the boundary approves at the floor rather than below it, for a
conviction of 100 paired with a reliability of exactly 0.5. It does not bypass
any other gate: every check that would block the action still runs first.

Correction to an earlier draft of this record: a prior version said this "does
not create BUY exposure". That was **wrong**, and the counterexample is the ATR
shape. `_atr_status` only reads `status` when `volatility_atr` is a dict
(`risk_manager.py:306-310`), while `_atr_value` accepts a scalar. Measured, with
fresh news and no red flag:

| `volatility_atr` at 20% of price | `BUY` conviction 100 |
| --- | --- |
| scalar `10000` | approved, `executed_size 5.0` |
| dict `{value: 10000, status: "NORMAL"}` | approved, `executed_size 5.0` |
| dict `{value: 10000, status: "EXTREME"}` | `HOLD` (blocked by `ATR EXTREME`) |

So a scalar ATR, or a dict whose `status` does not say `EXTREME`, reaches the
hybrid floor with `sys_rel` 0.5 and approves a BUY. Two things keep this off the
live path today: conviction is capped at 80 before the Risk Manager sees it, and
`indicators.py:289-292` always emits the dict with a truthful `status`, so a real
live payload at an ATR above 5% of price carries `EXTREME` and is blocked. The
first two rows therefore need a non-live payload shape. It is still a genuine
BUY-approval path through the boundary rather than a theoretical one, which is
why it is recorded: the whole point of this section is measured reachability,
not a claim that every path is safe.

### (b) `technical_context` without `rsi`/`macd` approves BUY

`_directional_gate` reads `tech.get("rsi", {}).get("status")` and
`tech.get("macd", {}).get("status")` (`risk_manager.py:226-227`). A missing key
yields `None`, which matches no blocklist, so the BUY proceeds.

Reproduced: a payload whose `technical_context` contains only `current_price`
and `volatility_atr`, with fresh news, approved `BUY/90` with
`Confianca Hibrida: 90.0%`.

Reachability:

- On the live path both keys are always present. `calculate_technical_status`
  emits `rsi.status` and `macd.status` unconditionally
  (`indicators.py:264-271`), defaulting to `NEUTRAL` when the value is NaN, and
  `payload_builder.py:167` aborts the cycle when the context status is `ERROR`.
- It is reachable from test and campaign harnesses that build partial payloads.
  `chaos_monkey.py:14` and `test_pipeline_safety.py:202-204` omit `rsi`/`macd`
  from `technical_context` and do reach `_directional_gate`. Both cases still
  HOLD, but for a different reason each: `chaos_monkey.py:14` on the no-news
  conviction gate (75 < 80, "Noticias velhas/ausentes"), and
  `test_pipeline_safety.py:202` on low hybrid confidence (45.0%). Neither HOLD
  comes from the missing indicators, so the shape passes the gate unremarked. A
  missing `rsi`/`macd` is not the same as an explicitly neutral one, and the gate
  cannot tell them apart.
- Counterexample to avoid: `test_red_team_findings.py:50-55` also builds a
  partial `technical_context`, but that payload goes to `execute_paper_order`,
  and `paper_simulator.py` never calls `evaluate_order` or `_directional_gate`.
  It is not evidence for this finding.

Important shape distinction, measured: a **missing** key degrades silently to
"no blocklist", while an **explicit `None`** raises `AttributeError`. These are
different defects with different symptoms.

Impact: latent on the live path today. It becomes a real hole the moment a
caller supplies a partial `technical_context`, and the gate gives no signal that
the evidence was absent.

### (c) `news_context=None` raises `TypeError` instead of HOLD

`calculate_system_reliability` (`risk_manager.py:47-48`) and `evaluate_order`
(`risk_manager.py:169-170`) both call `len(news)` on
`payload.get("news_context", [])`. A missing key yields `[]`, but an explicit
`None` yields `TypeError: object of type 'NoneType' has no len()`.

Reproduced: `news_context=None` raises `TypeError` out of `evaluate_order` at
the no-news check (`risk_manager.py:169-170`), before
`calculate_system_reliability` is ever called. The gate order matters: with
`conviction < 70` the method returns HOLD at the `MINIMUM_CONVICTION` check
first (measured: conviction 60 and 69 return HOLD; conviction 70 and 90 raise
`TypeError`). So the crash happens only for a directional proposal strong enough
to pass the conviction gate, which is exactly the case that most needs a
decision.

Reachability: latent. Every live and campaign caller sets a list.
`payload_builder.py:174` assigns the output of `sanitize_news_context`, which
always returns a list, and `payload_builder.py:190` puts that list into the
payload. No current caller passes `None`.

Impact: none today, but this is the one finding that directly violates the
project rule to prefer failing closed. A malformed payload crashes instead of
returning HOLD, and a crash is not an audited safety decision.

### Broader fail-closed gap found while testing (c)

The same `payload.get(key, {}).get(...)` pattern is used for `data_health`,
`news_risk` and `technical_context`. Measured behaviour of `evaluate_order`
with the project venv, for a directional action (`BUY`/`SELL`):

| Payload field | Value | Result |
| --- | --- | --- |
| `news_context` | `None` | `TypeError`, only when conviction reaches the no-news check (>= 70) |
| `news_risk` | `None` | `AttributeError` in the directional gate, at any conviction |
| `data_health` | `None` | `AttributeError` in the directional gate, at any conviction |
| `technical_context` | `None` | `AttributeError` in the directional gate, at any conviction |
| `technical_context` | string | `AttributeError` in the directional gate, at any conviction |
| `technical_context` | list | `AttributeError` in the directional gate, at any conviction |
| `technical_context.rsi` | `None` | `AttributeError` in the directional gate, at any conviction |

The qualifier matters: `action == "HOLD"` returns at `risk_manager.py:130-131`
before the directional gate, so a malformed payload with a HOLD action does not
raise **for any of the fields above** (measured). These exception-type columns
describe only directional actions.

A HOLD is not universally exception-free, and the table above must not be read
that way. `portfolio_context=None` raises `AttributeError` at
`risk_manager.py:116-118` *before* the HOLD return, because the numeric-input
`try` there catches only `TypeError`, `ValueError` and `OverflowError`. Measured:
`evaluate_order("HOLD", 100, {"portfolio_context": None}, 10.0)` raises. So the
HOLD short-circuit protects against the gate fields, not against a malformed
`portfolio_context`.

These are one defect class, not seven independent bugs: a payload that violates
the expected shape raises instead of returning HOLD. It is the same class the
auditor reported as (c), recorded here at a broader breadth. The measured
exception types differ (`TypeError` for the `len()` call, `AttributeError` for
`.get()` on a non-dict), so a caller that catches only one will not catch the
other.

## Why these were not fixed

The operator chose documentation and validation only. Two project rules drive
this:

- `AGENTS.md` requires explicit operator sign-off to change Risk Manager
  thresholds or the conviction gate. Fix (a) changes when the floor blocks.
- `AGENTS.md` says to prefer failing closed, which makes (c) defensively
  relevant even though it is not reachable today.

They remain open findings, not resolved ones. Do not report them as fixed or as
"only theoretical". (c) in particular is a fail-closed violation that should be
fixed in a future session that has sign-off, because the fix is additive safety
(a malformed payload returns HOLD) and does not touch any threshold.

## Blocked on the operator, unchanged by this closure

1. **Confirm the real account fee.** `PAPER_FEE_RATE=0.003` is a configuration
   premise, not a measurement. `backend/tests/validate_mb_order_dry_run.py`
   already reads the real fees from the authenticated exchange client
   (`backend/execution/mb_private_client.py`), but the run needs three
   credentials in `backend/.env`: `MB_CLIENT_ID`, `MB_CLIENT_SECRET` and
   `MB_ACCOUNT_ID`. `MBCredentials.from_env` requires all three
   (`mb_private_client.py:55-57`); supplying only the first two fails at
   credential load. `MB_ACCOUNT_ID` is not the same value as `MB_CLIENT_ID`; it
   is returned by `GET /api/v4/accounts` after authentication, and
   `backend/.env.example` leaves it commented out until that read is done. This
   must come before any further edge conclusion, because the cost reading does
   not scale linearly: the minimum slippage of 0.05% per side remains as a
   floor.
2. **Leverage decision after (1).** Either a longer horizon (hours to days) or
   trading rarely with the cost in the payload. The edge and cost diagnostic of
   2026-09-16 measured that the configured BUY round-trip cost of 0.70% exceeded
   the median absolute 60-minute move of 0.470% on the `development` partition,
   with negative mean net margin at every tested horizon.

## What this closure does not claim

- No profitability or predictive edge. The measured net margin after configured
  costs was negative at every tested horizon, on one partition with overlapping
  windows and synthetic news.
- No production readiness for real money. Real execution would need a separate
  architecture, threat model, fill reconciliation and acceptance process.
- No statement that the three Risk Manager findings are harmless in general.
  They are latent on the live path, reachable from at least one harness, and
  left open by operator decision.

## Pointer

The prior session detail, including the cost diagnostic and the delegation
protocol, is in `docs/reports/SESSION_HANDOFF_2026-09-17.md`. The accepted
paper-only boundary is in `docs/reports/FINAL_ACCEPTANCE.md`, revalidated on
2026-09-17.
