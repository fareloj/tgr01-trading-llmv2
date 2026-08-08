# Historical Decision Campaign — development / balanced / technical-only (Ollama Cloud, gpt-oss:120b-cloud)

Status: **research artifact, not a profitability claim.** Same caveats as
[HISTORICAL_DEVELOPMENT_BALANCED_CORRECTED.md](HISTORICAL_DEVELOPMENT_BALANCED_CORRECTED.md) apply in full — retrospective,
stratified, technical-only, does not validate live profitability.

## 1. What this campaign tests

Same frozen `development` windows, same `balanced` prompt, same Risk Manager, same
`technical-only` news mode as the local LM Studio run — only the **provider and model**
differ: **Ollama Cloud**, model `gpt-oss:120b-cloud`, instead of LM Studio serving
`openai/gpt-oss-20b` locally. Purpose: compare the 120B cloud-hosted variant against the
20B local variant on the exact same evaluation harness.

## 2. Bug found and fixed before running: gpt-oss family misdetection under Ollama's tag convention

`DecisionAgent._request_limits()` and `PromptProfileRunner._request_limits()` granted the
larger reasoning completion budget (600–3000 tokens + `reasoning_effort=low`) only to models
whose name starts with `"openai/gpt-oss"` (LM Studio/HF style). Ollama tags the same model
family as `gpt-oss:120b-cloud` (colon, not slash) — this silently fell through to the
450-token default meant for non-reasoning models.

**Observed impact before the fix:** 5 identical calls (same payload, `temperature=0.0`) against
`gpt-oss:120b-cloud`:

| Attempt | finish_reason | completion_tokens | content |
|---|---|---:|---|
| 1 | stop | 447 | valid (conviction=45) |
| 2 | **length** | 450 | **empty** |
| 3 | stop | 424 | valid (conviction=**40**) |
| 4 | **length** | 450 | truncated |
| 5 | **length** | 450 | truncated |

**60% failure rate**, plus conviction drift (45 → 40) across identical inputs at
`temperature=0` — an apparent determinism break.

**Fix:** broadened both `startswith` checks to match `"openai/gpt-oss"` **or**
`"gpt-oss:"`. **After the fix, 5/5 repeat calls succeeded** (`finish_reason=stop`,
120–183 completion tokens, well under budget) and **conviction was identical (50) in all
5** — the apparent temperature=0 non-determinism was actually the model exhausting its
token budget mid-response in different ways each time, not genuine sampling variance.
Covered by 14 new regression tests in
[`backend/tests/test_gpt_oss_token_budget.py`](../../backend/tests/test_gpt_oss_token_budget.py).
Full suite: **252/252 passed** after the fix. Commit: `3f2d192`.

A second, unrelated change landed in the same session: the paper-trading decision-cycle
default interval was throttled from 60s to 900s (15 minutes) to match Mercado Bitcoin's
real BTC/BRL candle refresh cadence — `price_worker.py`'s own 30s market-data polling was
left untouched. Commit: `e721664`. Not exercised by this campaign (which uses its own
independent `--step-seconds`), documented here only because it landed in the same cycle.

## 3. Run identity

| Field | Value |
|---|---|
| Campaign ID | `459f18c9b3fab8336a659829f300cbd1711990d88610273d9b3cfb1bd31dcf70` |
| Commit | `3f2d192` (gpt-oss detection fix), preceded by `9ff2533b` (response_format fix) |
| Provider | `ollama-cloud`, via local Ollama daemon's OpenAI-compatible endpoint (`http://localhost:11434/v1`), authenticated to account `danielceragioliand11`. Note: the campaign JSON's `variants[].provider` field says `"groq"` — same pre-existing cosmetic label bug in `variant_descriptors()` noted in the prior report; does not affect the request path. |
| Model / quantization | `gpt-oss:120b-cloud` (quantization managed by Ollama Cloud, not user-configurable) |
| Prompt (variant) | `balanced`, sha256 = `65b39ed9b025628b7576ed3828d337b3dccd397211ba2240d9d618ddff1ff851` (identical prompt to the local 20B run) |
| Temperature | `0.0` |
| Completion budget | 600 tokens, `reasoning_effort=low` (after the fix in §2) |
| Dataset ID | `47b1caa29366832681ea3cf5` |
| Partition | `development` |
| News mode | `technical-only` |
| Selection strategy | `stratified`, 20 windows/regime × 3 regimes = 60 windows, 5 cycles/window = 300 calls |
| Exposure (frozen) | 40% |
| Started / completed | 2026-08-08 02:22:53 UTC → 02:37:39 UTC (**~15 min for 300 calls**, ~3s/call average — far faster than both the local LM Studio run (~61 min) and the rate-limited Groq run) |

## 4. Technical health

- **299/300 calls completed successfully, 1 error (0.33%).**
- The 1 error (`downtrend-20`, cycle 5) was a `ValidationError`: `Invalid JSON: expected
  ident at line 3 column 19`. Root cause: **UTF-8 encoding corruption in the response**,
  the same mojibake pattern observed during manual debugging ("preço" → "pre�o",
  "exposição" → "exposi��o") — accented Portuguese characters occasionally arrive
  malformed from Ollama Cloud's serving stack, and when a malformed byte lands inside a
  JSON string it breaks the parser. This is a provider-side encoding defect, not a bug in
  our request/parsing code — no fix was attempted or is possible on our side beyond
  retrying (the harness's built-in retry logic did not fire here because this manifested
  as a `ValidationError` after a 200 response, not a `RateLimitError`).
- **0 violations** of "never describe unavailable news as fresh/current/mixed/positive/negative" across all 299 successful results.
- **0 violations** of the conviction cap (directional conviction never exceeded 60).
- **0 contradictions** between the brief's first line and the recorded `llm_action`.
- `news_risk.risk_level` was `UNAVAILABLE` in all 299 successful results.

## 5. LLM vs. Risk Manager

| | Count (of 299 OK) |
|---|---:|
| LLM HOLD | 175 |
| LLM BUY | 59 |
| LLM SELL | 65 |
| Risk Manager HOLD (final) | **299 (100%)** |
| Risk Manager BUY/SELL (final) | **0** |

Same structural finding as the local 20B run: **100% of directional suggestions blocked**.
Breakdown of the 124 LLM-directional calls overridden:

| Risk Manager block reason | Count |
|---|---:|
| `Conviccao bruta da IA insuficiente (60%)` (min. 70% required) | 62 |
| `Directional Gate: BUY bloqueado por noticias stale` | 59 (100% of BUY) |
| `Directional Gate: SELL bloqueado por RSI OVERSOLD` | 3 |

The 120B model suggested directional action more often than the local 20B model (124/300 =
41.3% vs. 83/300 = 27.7% for 20B) — mostly more SELL calls (65 vs. 31) — but every one is
still blocked by the same structural ceiling documented in the prior report
(`is_news_stale` hard-blocks BUY; the 60-conviction cap sits below the 70% approval floor).

## 6. Regime alignment (raw LLM suggestion)

| Regime | Expected | Samples | LLM matched | Risk matched |
|---|---|---:|---:|---:|
| UPTREND | BUY | 100 | 25 | 0 |
| DOWNTREND | SELL | 99 | 28 | 0 |
| SIDEWAYS | HOLD | 100 | 52 | 100 |

## 7. Directional calibration (raw LLM suggestion, 60-conviction bucket, 124 samples)

| Horizon | "Good" rate |
|---:|---:|
| 5m | **0.0%** |
| 15m | **0.0%** |
| 30m | **9.18%** |
| 60m | **21.5%** |

Nearly identical shape to the local 20B run (0% / 0% / 7.35% / 19.44%) — poor short-horizon
calibration, marginal improvement by 60 minutes, in both model sizes and both providers.
This strengthens the read that the calibration weakness is a property of the
`technical-only` configuration (no news signal) and/or the `balanced` prompt, not a
model-size or provider artifact.

## 8. Provider comparison summary

| | LM Studio (local) | Ollama Cloud |
|---|---|---|
| Model | `openai/gpt-oss-20b` (MXFP4) | `gpt-oss:120b-cloud` |
| Calls | 300/300, 0 errors | 299/300, 1 encoding error |
| Wall time (300 calls) | ~61 min | ~15 min |
| Cost | $0 (own GPU) | Free-tier quota (unconfirmed official limits) |
| Network dependency | None | Yes — requires Ollama Cloud reachability |
| Directional suggestion rate | 27.7% | 41.3% |
| Risk Manager block rate | 100% | 100% |
| Short-horizon calibration | ~0% | ~0% |
| Encoding integrity | Clean (0 corrupted responses) | 1 corrupted response (accented characters) |

Both models are safe under the structural checks (no news hallucination, correct
conviction cap, correct deterministic brief, Risk Manager fully in control). The cloud run
is ~4x faster in wall-clock time and suggests directional action more often, but introduces
a network dependency and an observed (rare) encoding defect that the local run did not
exhibit. Neither provider currently produces an approved directional trade under this exact
`technical-only` + threshold configuration, so this campaign cannot compare *realized*
directional precision under Risk Manager approval between the two — only raw LLM
suggestion quality, which is similarly weak on both.

## 9. Interpretation guardrails

Identical to §4 of the corrected development report: retrospective regime selection is not
an unbiased backtest; `technical-only` is an explicit intervention; this result does not
validate profitability; costs/slippage/fees remain included in the LLM-suggestion horizon
scoring (though moot here since no directional trade was ever approved); samples are
correlated (5 cycles per window share a 60-minute price window).
