# Historical Decision Campaign — development / balanced / technical-only (corrected)

Status: **research artifact, not a profitability claim.** This documents a retrospective,
stratified evaluation of the Decision Agent and Risk Manager on frozen historical BTC/BRL
data. It does not validate live profitability and must not be used to size real capital.

## 1. What changed in this cycle

1. **technical-only fix audited.** `apply_news_mode()` in
   [`backend/tests/run_historical_campaign.py`](../../backend/tests/run_historical_campaign.py)
   forces `news_context=[]`, `news_context_mode=UNAVAILABLE_BY_TEST_DESIGN`,
   `is_news_stale=true`, `news_age_seconds=null`, `news_risk.risk_level=UNAVAILABLE` for the
   `technical-only` mode. `enforce_payload_decision_constraints()` in
   [`backend/agents/decision_agent.py`](../../backend/agents/decision_agent.py) caps directional
   conviction at 60 whenever news is stale and always rebuilds `decision_brief` deterministically
   from the payload (never trusts the model to restate freshness/exposure). Verified by a
   dedicated subagent audit and by the existing test
   `test_technical_only_news_mode_marks_news_unavailable_and_stale` — PASS on all 7 structural
   checks (see conversation record; not reproduced here to avoid duplicating test code in docs).
2. **New bug found and fixed: `response_format` provider incompatibility.**
   Groq/OpenAI-style hosted endpoints accept `response_format={"type": "json_object"}`. LM
   Studio's OpenAI-compatible server rejects that value with `HTTP 400 — 'response_format.type'
   must be 'json_schema' or 'text'`. This broke 100% of calls in both `DecisionAgent`
   (`backend/agents/decision_agent.py`) and `PromptProfileRunner`
   (`backend/tests/compare_prompt_profiles.py`, used by every non-`current` variant, including
   `balanced`). Fixed by detecting a local `base_url` (`localhost`/`127.0.0.1`) and switching to a
   strict `json_schema` payload built from the Pydantic contract (`DecisionOutput` /
   `AnalysisPlan`), while hosted providers keep `json_object` unchanged. Covered by 7 new
   regression tests in
   [`backend/tests/test_llm_provider_response_format.py`](../../backend/tests/test_llm_provider_response_format.py).
   Full suite: **238/238 passed** after the fix.
   Commit: `9ff2533bc32c55ba3762bcf69b789de6990a531b`.

## 2. Campaigns run

| Campaign | Provider | Model | Calls | Status |
|---|---|---|---:|---|
| `development_balanced_corrected_27.json` | Groq (hosted) | openai/gpt-oss-120b | 27 | COMPLETED, 0 errors |
| `development_balanced_corrected_300_groq120b_INCOMPLETE_RATE_LIMITED.json` | Groq (hosted) | openai/gpt-oss-120b | 135/300 | **Manually stopped** — sustained rate-limit stalls across all 4 configured keys; preserved, not deleted, not merged with the local run |
| `development_balanced_corrected_27_gptoss20b.json` | LM Studio (local) | openai/gpt-oss-20b (MXFP4) | 27 | COMPLETED, 0 errors (after the response_format fix) |
| `development_balanced_corrected_300_lmstudio_gptoss20b.json` | LM Studio (local) | openai/gpt-oss-20b (MXFP4) | 300 | **COMPLETED, 0 errors** — this is the primary result below |

`prism-ml/bonsai-27b` (Q1_0) and `qwen/qwen3.5-9b` (Q8_0) were probed with a single real
decision payload each and **both exhausted their entire 450-token completion budget on internal
reasoning** (`finish_reason=length`, `content=''`) without ever emitting JSON. This is a harness
token-budget limitation for local "thinking" models outside the `openai/gpt-oss-*` family (only
that family gets `reasoning_effort=low` + a larger budget in `PromptProfileRunner`), not
something fixed with model quality. Both were skipped for the 300-call run by explicit decision;
no formal pass/fail verdict is recorded for them, and no budget-tuning change was made to
`compare_prompt_profiles.py` in this cycle.

## 3. Primary result: 300-call `openai/gpt-oss-20b` (local) campaign

### Run identity

| Field | Value |
|---|---|
| Campaign ID | `4938a2cd18c01a244e1ba91dcb03c6c1bee5a801b53a74757b95dc35fe7012c0` |
| Commit | `9ff2533bc32c55ba3762bcf69b789de6990a531b` |
| Provider | `lmstudio` (local OpenAI-compatible server, `http://localhost:1234/v1`). Note: the campaign JSON's `variants[].provider` field says `"groq"` — this is a cosmetic label bug in `variant_descriptors()` that always hardcodes `"groq"` regardless of actual `base_url`; it does not affect the request path or results. Left unfixed this cycle; flagged for a follow-up. |
| Model / quantization | `openai/gpt-oss-20b`, **MXFP4** |
| Prompt (variant) | `balanced`, sha256 = `65b39ed9b025628b7576ed3828d337b3dccd397211ba2240d9d618ddff1ff851` |
| Temperature | `0.0` |
| Context length (loaded) | `8192` tokens |
| Reasoning effort | `low` (`GPT_OSS_REASONING_EFFORT` default in `PromptProfileRunner._request_limits`), completion budget 600 tokens |
| Dataset ID | `47b1caa29366832681ea3cf5` |
| Partition | `development` (`1680269280` – `1743378840`, chronological, no validation/holdout access) |
| News mode | `technical-only` |
| Selection strategy | `stratified`, 20 windows/regime × 3 regimes = 60 windows, 5 cycles/window = 300 calls |
| Exposure (frozen) | 40% |
| Execution costs | fee_rate 0.30%, slippage 0.05%–0.30% (ATR-scaled) |
| Started / completed | 2026-08-04 23:50:49 UTC → 2026-08-05 00:52:04 UTC (~61 min, ~12s/call average incl. resume overhead) |
| Resumability | Ran via `--resume` against a plan-only file with 0 prior results; fingerprint matched; idempotent (a second `--resume` on the completed file would append nothing, since `completed` keys already cover all 300 `variant:window:timestamp` triples) |

### Technical health

- **300/300 calls completed, 0 technical errors, 0 JSON validation failures.**
- **0 violations** of "never describe unavailable news as fresh/current/mixed/positive/negative" — checked across every `llm_decision_brief` + `llm_reasoning`.
- **0 violations** of the conviction cap (directional conviction never exceeded 60).
- **0 contradictions** between the brief's first line and the recorded `llm_action`.
- `news_risk.risk_level` was `UNAVAILABLE` in all 300 results (technical-only intervention held for the full run).

### LLM vs. Risk Manager

| | Count |
|---|---:|
| LLM HOLD | 217 |
| LLM BUY | 52 |
| LLM SELL | 31 |
| Risk Manager HOLD (final) | **300 (100%)** |
| Risk Manager BUY/SELL (final) | **0** |

**Every single directional suggestion was blocked by the Risk Manager.** Breakdown of the 83
LLM-directional calls that were overridden:

| Risk Manager block reason | Count |
|---|---:|
| `Directional Gate: BUY bloqueado por noticias stale` | 52 (100% of BUY) |
| `Conviccao bruta da IA insuficiente (60%)` (min. 70% required) | 26 |
| `Directional Gate: SELL bloqueado por RSI OVERSOLD` | 3 |
| `Directional Gate: SELL bloqueado por MACD BULLISH_EXPANDING` | 2 |

This is a structural consequence of the `technical-only` intervention interacting with the
current Risk Manager thresholds, not a bug: `is_news_stale=true` (forced by technical-only)
hard-blocks all BUY through the directional gate, and the conviction cap of 60 (also forced by
`is_news_stale`) sits below the Risk Manager's 70% minimum for any directional approval — so
**no BUY or SELL can ever clear the wall in this exact configuration**, regardless of how the
LLM behaves. Any future campaign that wants matured BUY/SELL samples under Risk Manager approval
must either relax `is_news_stale` (defeats the purpose of technical-only) or lower the Risk
Manager's approval thresholds (out of scope for this cycle — would require sign-off, since it
touches the Risk Manager itself).

### Regime alignment (raw LLM suggestion vs. expected regime action)

| Regime | Expected | Samples | LLM matched | Risk matched |
|---|---|---:|---:|---:|
| UPTREND | BUY | 100 | 19 | 0 |
| DOWNTREND | SELL | 100 | 15 | 0 |
| SIDEWAYS | HOLD | 100 | 63 | **100** |

Risk-matched is 100% only for SIDEWAYS because the expected action there is HOLD and the Risk
Manager's output is always HOLD in this run — not a meaningful signal of skill, just an artifact
of the 100% block rate above.

### Directional precision / calibration (raw LLM suggestion, since Risk Manager approved 0 trades)

Since the Risk Manager's own directional sample count is zero, "risk" precision is undefined
(`directional_precision: null` at every horizon). The only informative signal is the raw LLM
suggestion's calibration in its one directional conviction bucket (60, i.e. the "60-69" bucket),
83 samples:

| Horizon | Matured | Data gap | "Good" rate (60 conviction bucket) |
|---:|---:|---:|---:|
| 5m | 298 | 2 | **0.0%** (0/77+6 scored) |
| 15m | 299 | 1 | **0.0%** (0/72+11 scored) |
| 30m | 300 | 0 | **7.35%** (5 good) |
| 60m | 294 | 6 | **19.44%** (14 good) |

The LLM's directional calls were poorly calibrated at short horizons and only marginally better
at 60 minutes — still far from a usable edge. In this specific run, the Risk Manager blocking
100% of directional trades was the empirically correct protective outcome, not an
overly-conservative annoyance.

### HOLD correctness and missed opportunity

The HOLD-conviction 50–59 bucket (217 samples, technical-only always assigns 50 for a HOLD
absent other overrides) shows a "good_rate_among_good_bad" of 1.0 at every horizon — but this
metric only classifies whether a HOLD avoided a bad move vs. missed an upside; with `good`
counts far exceeding `missed_upside` at every horizon (e.g. 60m: 140 good vs. 20 missed upside,
53 avoided downside), HOLD was the dominant and largely justified action, consistent with a
conservative, uncertain regime under technical-only.

## 4. Interpretation guardrails (mandatory reading before citing this report)

- **Retrospective regime selection is not an unbiased backtest.** Windows were selected using
  the full-window outcome (stratified: 20 windows per regime × 3 regimes), so results measure
  behavior under known-in-hindsight conditions, not blind forward performance.
- **`technical-only` is an explicit intervention**, not a naturally occurring market condition.
  It removes all news signal and forces conviction ≤ 60 and `is_news_stale=true`. Results here
  say nothing about how the Decision Agent behaves with real, timely news.
- **This development-partition result does not validate profitability.** No conclusion here
  should be read as "the strategy makes money" — it is a correctness/safety check on the LLM and
  Risk Manager's joint behavior.
- **Costs, slippage, and fees remain included** in every horizon calculation (round-trip for
  BUY, one-way for SELL, ATR-scaled slippage) — see `execution_costs` in the run identity table.
- **Samples are correlated, not independent.** 5 cycles per window share the same underlying
  60-minute price window and regime; treat the 300 calls as 60 windows of contextually related
  decisions, not 300 independent draws.
- **The 135/300 Groq/120b partial run is preserved but not comparable 1:1** to the 300/300 local
  20b run — different model, different provider, and the 120b run never reached conviction/regime
  saturation before being stopped. Do not average or merge the two.

## 5. Known limitations from the absence of historical news

- `technical-only` cannot substitute for what a real, timely news feed would have added to the
  decision — it establishes only that the LLM and Risk Manager behave safely (no hallucinated
  freshness, correct conviction capping, correct deterministic brief) when news is structurally
  absent.
- The Risk Manager's directional gate treats `is_news_stale=true` as an unconditional BUY block.
  Combined with the 60-conviction cap, this makes the `technical-only` configuration incapable of
  producing a single approved directional trade — useful for testing LLM/brief correctness, but
  it cannot be used to measure realized directional precision under Risk Manager approval. A
  future design that wants both "no real news" and "some approved trades" would need a deliberate,
  separately-reviewed change to the Risk Manager or to the technical-only conviction cap — not
  attempted in this cycle.

## 6. Bugs found and fixed this cycle

1. `response_format` incompatibility between hosted (Groq/OpenAI `json_object`) and local
   (LM Studio, requires `json_schema`) providers — fixed in `decision_agent.py` and
   `compare_prompt_profiles.py`, covered by 7 new tests, commit `9ff2533b`.
2. No Risk Manager or prompt logic bugs were found or changed this cycle.

## 7. Readiness for `validation`

**Not yet.** Before touching the `validation` partition:
- The 100%-block-rate finding above means this exact `technical-only` + current-thresholds
  configuration cannot produce a directionally-scored campaign. Any `validation` run under the
  same config would face the identical structural ceiling. A decision is needed on whether
  `validation` should still run under `technical-only` (to re-confirm safety/no-hallucination
  behavior) or wait for a design that allows some approved trades.
- Model/provider choice for the campaign is not finalized: only `openai/gpt-oss-120b` (Groq,
  rate-limited, only 135/300 done) and `openai/gpt-oss-20b` (LM Studio, 300/300 done) have valid
  data. `prism-ml/bonsai-27b` and `qwen/qwen3.5-9b` were skipped, not evaluated to a verdict.
- No prompt, threshold, or Risk Manager change is pending — the frozen configuration used for
  this campaign is exactly documented in §3's run identity table and can be reused verbatim for
  `validation` once the above is decided.

## 8. What still blocks any real-money operation

- Paper trading remains the only permitted mode; no code path in this cycle touched
  `ENABLE_REAL_TRADING` or equivalent.
- `validation` has not been opened; `holdout` remains sealed and was never accessed.
- The Risk Manager's 100% block rate under `technical-only` has not been reconciled with a
  design that would ever approve a live trade under a similarly news-degraded condition — that
  question needs an explicit architecture decision before any live-adjacent testing.
