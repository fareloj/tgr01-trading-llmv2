# Deterministic Tool Protocol Red Team - 2026-08-01

## Scope

This review covers the new LLM-selected, application-executed market-analysis
tools. It does not authorize real trading. No benchmark mutated the paper
portfolio and no exchange order path exists.

## Implemented boundary

The LLM can request at most three distinct contracts from this allowlist:

1. `multi_timeframe_trend`
2. `donchian_breakout`
3. `drawdown_profile`
4. `volume_confirmation`

Pydantic forbids extra fields, unknown tools, duplicate tools, duplicate trend
windows, and lookbacks outside fixed enumerations. The executor reads at most
1,500 one-minute candles and filters every frame to
`timestamp <= as_of_timestamp`. Tool results contain facts, status, latency and
audit state; they contain no trade action and cannot call the paper simulator.

## Adversarial checks

The automated suite covered:

- unknown `shell`/`run_sql` tool requests;
- a valid tool carrying an extra `command` field;
- duplicate tools and duplicate windows;
- unbounded lookbacks;
- a loader that incorrectly returns future candles;
- unavailable data storage;
- unavailable audit storage;
- insufficient historical data;
- unsupported timeframes;
- repeated drawdown-event detection;
- an end-to-end LLM plan -> Python execution -> LLM decision round trip.

Observed behavior:

- malformed plans failed closed to an empty plan;
- future rows did not change historical results;
- data failures became `ERROR`, not `HOLD`, `BUY`, or `SELL` evidence;
- audit failure remained visible while the read-only calculation survived;
- a qualifying drawdown event was inserted once and then deduplicated;
- planner rationale was not returned to the final decision pass.

## Calculation validation

The first historical probe exposed an overly sensitive trend classifier: tiny
negative returns in the sideways window were labeled bearish. A deadband now
requires both an absolute return of at least `0.15%` and an EMA spread of at
least `0.02%` in the same direction.

After correction:

| Historical window | Tool alignment | Per-window result |
|---|---|---|
| Uptrend (+1.78% hour) | BULLISH | 15m MIXED, 60m BULLISH, 240m BULLISH |
| Downtrend (-1.53% hour) | BEARISH | 15m/60m/240m BEARISH |
| Sideways (+0.002% hour) | MIXED | 15m/60m/240m MIXED |

## LLM experiments

Models:

- Groq `llama-3.3-70b-versatile`
- Groq `openai/gpt-oss-120b`
- Groq `openai/gpt-oss-20b`
- Groq `qwen/qwen3.6-27b`

Prompts:

- `evidence_balanced`
- `trend_following`
- `contradiction_averse` is implemented for subsequent experiments.

Both models generated valid allowlisted plans. On the same reversal sample,
Llama selected trend, Donchian and drawdown; GPT-OSS selected trend, Donchian
and volume. The complete production round trip returned a validated HOLD after
the script executed all three requested calculations.

In the controlled midpoint matrix, both models proposed SELL during an
observed bearish setup that reversed strongly upward afterward. The Risk
Manager blocked both proposals. GPT-OSS also proposed SELL in a sideways sample
where Llama returned HOLD; the Risk Manager again blocked it. Parameter count
did not produce a quality advantage in this small sample.

The three-point Llama lifecycle run produced:

- 7 HOLD, 2 SELL, 0 BUY proposals;
- all final Risk Manager actions were HOLD;
- both SELL proposals were blocked;
- one blocked SELL preceded a `+1.69%` 15-minute and `+2.36%` 60-minute move;
- stale-news conviction reduction kept all directional proposals below the
  executable threshold.

After configuring Qwen's documented non-thinking mode, Qwen 27B was the only
tested model that honored the explicit `RSI OVERSOLD` contradiction in the
midpoint downtrend sample and returned HOLD before the Risk Manager. GPT-OSS
20B behaved similarly to 120B in the three midpoint samples. Qwen still
proposed the same bad SELL before the sharp upward reversal, so the improvement
was contract adherence rather than prediction.

Groq has announced that hosted Llama 3.3 70B will be shut down on August 16,
2026 and recommends GPT-OSS 120B or Qwen 3.6 27B. The code default was moved to
GPT-OSS 120B; Qwen remains a strong candidate for a larger out-of-sample
comparison. See the [Groq deprecation notice](https://console.groq.com/docs/deprecations)
and [Qwen model guidance](https://console.groq.com/docs/model/qwen/qwen3.6-27b).

This is evidence of conservatism and useful defense in depth, not evidence of a
profitable strategy. Trend tools lagged a sharp reversal because all observable
15m/60m/240m inputs were still bearish before the move.

## Operational findings

### High: real-money readiness is not established

The sample is too small, covers overlapping windows, and does not test complete
position lifecycles after costs. Real execution remains out of scope.

### High: stale-news policy still prevents directional paper execution

The Decision Agent caps directional conviction at 60 with stale news, while the
Risk Manager requires at least 70 and independently blocks stale-news BUY.
Tools do not and should not bypass this conflict. The policy must be evaluated
explicitly on a larger out-of-sample dataset.

### Medium: larger model did not fix lagging evidence

The 120B model interpreted more details but followed the same wrong directional
setup at the reversal. Better prose is not a substitute for predictive data.

### Medium: provider rate limits affect benchmark completeness

The harness now uses explicit timeouts, zero hidden retries and one bounded
rate-limit wait. Rate limits are reported as errors and never converted to
analytical HOLDs.

### Medium: event thresholds need broader calibration

The 3%/7% drawdown severities are transparent safeguards, not statistically
calibrated BTC regime thresholds. Event persistence stays off in backtests.

## Verification

- Python: `122 passed`
- Electron/Node: `6 passed`
- npm production dependency audit: `0 vulnerabilities`
- live historical tool probe: uptrend/downtrend/sideways classification matched
  the corrected expected regime
- actual Groq planner probe: both tested models returned valid bounded plans

## Decision

The tool protocol is acceptable for opt-in paper experiments. It is not
acceptable as a reason to enable real orders. The next quality gate is a
non-overlapping historical dataset with fees/slippage, calibrated labels,
complete position accounting, simple numerical baselines and an untouched
out-of-sample period.
