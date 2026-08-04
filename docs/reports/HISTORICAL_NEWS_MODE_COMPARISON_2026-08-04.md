# Historical News-Mode Comparison

Reports: `9` | sampling groups: `3` | paired points: `27`

All paired points retained identical price, RSI, MACD, and ATR values across news interventions.

## Aggregate

| Mode | Samples | LLM actions | Risk actions | Technical failures |
|---|---:|---|---|---:|
| historical | 27 | `{'HOLD': 25, 'BUY': 2}` | `{'HOLD': 25, 'BUY': 2}` | 0 |
| neutral-fresh | 27 | `{'HOLD': 20, 'BUY': 4, 'SELL': 3}` | `{'HOLD': 23, 'BUY': 4}` | 0 |
| technical-only | 27 | `{'BUY': 7, 'HOLD': 16, 'SELL': 4}` | `{'BUY': 7, 'HOLD': 20}` | 0 |

## Net Directional Evaluation

| Mode | Horizon | Matured | Directional | Good | Bad | Neutral | Precision | Average edge after costs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| historical | 5m | 27 | 2 | 0 | 2 | 0 | 0.0% | -0.9345% |
| historical | 15m | 26 | 2 | 1 | 1 | 0 | 50.0% | +0.4452% |
| historical | 30m | 27 | 2 | 1 | 1 | 0 | 50.0% | -0.0692% |
| historical | 60m | 27 | 2 | 1 | 1 | 0 | 50.0% | -0.3402% |
| neutral-fresh | 5m | 27 | 4 | 0 | 3 | 1 | 0.0% | -0.6989% |
| neutral-fresh | 15m | 26 | 4 | 1 | 3 | 0 | 25.0% | -0.2765% |
| neutral-fresh | 30m | 27 | 4 | 1 | 3 | 0 | 25.0% | -0.4567% |
| neutral-fresh | 60m | 27 | 4 | 1 | 3 | 0 | 25.0% | -0.5843% |
| technical-only | 5m | 27 | 7 | 0 | 6 | 1 | 0.0% | -0.8053% |
| technical-only | 15m | 26 | 7 | 1 | 6 | 0 | 14.3% | -0.6225% |
| technical-only | 30m | 27 | 7 | 1 | 6 | 0 | 14.3% | -0.8277% |
| technical-only | 60m | 27 | 7 | 1 | 6 | 0 | 14.3% | -1.0028% |

## Paired Changes

- `2026-06-04 08:30..2026-06-05 23:40` (America/Sao_Paulo): 9 points; changes vs historical: `{'neutral-fresh': 5, 'technical-only': 6}`
- `2026-06-06 00:00..2026-06-07 23:59` (America/Sao_Paulo): 9 points; changes vs historical: `{'neutral-fresh': 0, 'technical-only': 0}`
- `2026-08-01 02:00..2026-08-01 20:00` (America/Sao_Paulo): 9 points; changes vs historical: `{'neutral-fresh': 0, 'technical-only': 3}`

- `historical->neutral-fresh:HOLD->BUY:HOLD->BUY`: 2
- `historical->neutral-fresh:HOLD->SELL:HOLD->HOLD`: 3
- `historical->technical-only:HOLD->BUY:HOLD->BUY`: 5
- `historical->technical-only:HOLD->SELL:HOLD->HOLD`: 4

## Interpretation

Historical news produced fewer directional actions. Replacing news with neutral or empty context increased activity,
but the added trades did not produce positive average edge after the configured fee and slippage assumptions.
This supports retaining news as a risk context while revisiting the hard stale-news policy only through more paired tests;
it does not support removing news checks or lowering the Risk Manager threshold.

## Limitations

- Regimes were selected retrospectively and do not form an unbiased backtest.
- Synthetic neutral news and technical-only modes are interventions, not observed market states.
- Directional precision excludes neutral directional outcomes but HOLD labels remain review aids.
- The sample is too small to establish profitability or justify real-money execution.
