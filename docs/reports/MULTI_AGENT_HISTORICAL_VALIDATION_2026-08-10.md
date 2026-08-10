# Multi-Agent Historical Validation - 2026-08-10

## Scope

This experiment evaluated an experimental, shadow-only pipeline over frozen
BTC/BRL candles:

1. `gpt-oss:20b-cloud` classified bounded synthetic news fixtures;
2. `gpt-oss:20b-cloud` interpreted deterministic statistics from 32 15-minute
   candles (eight hours);
3. `gpt-oss:120b-cloud` proposed BUY, SELL, or HOLD;
4. the deterministic Risk Manager accepted or rejected the proposal.

The local Ollama OpenAI-compatible endpoint proxied the cloud models. All calls
used temperature zero and strict Pydantic contracts. No order, trade log, or
portfolio balance was written.

Historical news was not available. News inputs were explicitly synthetic
fixtures covering fresh, stale, positive, negative, conflicting, irrelevant,
empty, and prompt-injection cases. They are controls, not claims about events
that occurred alongside the candles.

## Development

The development campaign sent exactly 100 calls to each role (300 role calls):

| Role | Model | Valid responses |
|---|---|---:|
| News | `gpt-oss:20b-cloud` | 100/100 |
| Technical | `gpt-oss:20b-cloud` | 100/100 |
| Final decision | `gpt-oss:120b-cloud` | 98/100 |

Because the same 20B model served two bounded roles, it handled 200 calls in
total. The original final prompt produced directional proposals mostly at 60%
while the Risk Manager requires 70%; consequently all 100 development verdicts
were HOLD. The prompt was calibrated on development data only. It now documents
that 70 means a strong, coherent, gate-compatible setup and that stale news caps
directional conviction at 60. Risk thresholds were not loosened.

A final 10-snapshot development smoke test completed with 30/30 valid role
calls. It produced three Risk-approved directional actions, demonstrating that
the prompt and deterministic threshold were no longer structurally incompatible.

## Frozen Validation

After freezing the prompts, 50 stratified snapshots from the chronological
validation partition were evaluated. Future eight-hour labels were used only
after inference and were never included in model input.

### Operational results

| Metric | Result |
|---|---:|
| Role calls | 150 |
| News contract valid | 50/50 |
| Technical contract valid | 50/50 |
| Final JSON/basic contract valid | 48/50 |
| Final evidence contract valid after stricter audit | 45/50 |
| Risk-approved directional actions | 7/50 |
| Final Risk verdicts | 43 HOLD, 3 BUY, 4 SELL |

Mean latency was 1.864 seconds for news, 3.183 seconds for technical analysis,
and 2.878 seconds for the final decision. The corresponding p95 values were
3.588, 4.604, and 5.312 seconds.

Two final responses exceeded the 60% stale-news cap despite an explicit prompt
rule and a retry. Both failed closed to HOLD. A post-campaign evidence audit
found three additional inaccurate field paths. Those cases already had HOLD
Risk verdicts, so the stricter audit did not change an approved action. Runtime
validation now rejects absent evidence paths.

### Retrospective outcomes

The sample contained 17 future-up, 17 future-down, and 16 sideways labels.
Risk verdict evaluation produced:

| Status | Count |
|---|---:|
| Good | 19 |
| Avoided downside | 14 |
| Missed upside | 14 |
| Bad | 3 |

Among the seven approved directional actions, four were favorable and three
were unfavorable after the configured cost assumptions. Their independent
directional edges summed to approximately +1.995 percentage points, or +0.285
points per approved action. This is not a portfolio return: samples are
independent, sparse, and selected for balanced regimes.

All five prompt-injection fixtures were detected by the news agent and ended in
HOLD. Stale-news directional proposals were blocked by the Risk Manager.

## Verdict

The architecture passes the narrow safety objective: bounded agents can produce
structured evidence, malformed or policy-violating outputs fail closed, and the
Risk Manager remains sovereign. It does not pass a production or real-money
readiness bar.

Reasons:

- only seven directional actions matured, which is far too small for a return,
  drawdown, or calibration claim;
- synthetic news does not reproduce point-in-time historical information;
- the final model violated evidence/stale-news contracts in 5/50 cases under
  the stricter audit;
- the same 20B model serves both analytical roles, creating correlated-error
  risk;
- results are one validation slice, not walk-forward evidence.

The next valid experiment is a larger shadow campaign with the stricter evidence
validator, followed by forward paper trading with genuinely timestamped news.
Real-money execution remains out of scope.
