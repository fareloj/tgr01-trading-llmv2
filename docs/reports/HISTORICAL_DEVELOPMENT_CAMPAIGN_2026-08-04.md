# Historical Decision Campaign

> **Invalidated development run:** the `technical-only` fixture removed
> `news_context` but incorrectly left news health marked as fresh. Some model
> briefs consequently claimed fresh news that was not present. This report is
> retained as regression evidence only; its trading metrics must not be used to
> select a prompt or risk rule.

Campaign: `e8fa6291d099b86e2d889adfe82e38f6e03c4e70b69d96087ff0ed6267a02d82`
Status: `COMPLETED`
Database: `postgresql+psycopg://postgres:***@localhost:5432/tgr01`

> This is a stratified, retrospective evaluation. Regimes are selected using the full-window outcome,
> so results measure behavior under known conditions and must not be interpreted as an unbiased backtest.

No portfolio balance, order, or trade log is changed by this campaign.

## Configuration

- Range: `2023-03-31 10:28:00` to `2025-03-30 20:54:00`
- Dataset: `47b1caa29366832681ea3cf5`
- Dataset partition: `development`
- Selection strategy: `stratified`
- Variants: `balanced`
- News mode: `technical-only`
- Frozen exposure: `40.0%`
- Horizons: `[5, 15, 30, 60]` minutes
- Decision threshold: `0.2%` after estimated costs
- Fee assumption: `0.3000%` per side

## Frozen Windows

| ID | Regime | Local window | Move | Volatility | Cycles | Expected |
|---|---|---|---:|---:|---:|---|
| uptrend-01 | UPTREND | 2023-04-24 09:58:00 to 2023-04-24 10:58:00 | +0.5655% | 0.0821% | 3 | BUY |
| uptrend-02 | UPTREND | 2024-01-04 18:18:00 to 2024-01-04 19:18:00 | +0.7708% | 0.0990% | 3 | BUY |
| uptrend-03 | UPTREND | 2023-11-24 11:48:00 to 2023-11-24 12:48:00 | +1.2970% | 0.1203% | 3 | BUY |
| downtrend-01 | DOWNTREND | 2024-02-10 18:28:00 to 2024-02-10 19:28:00 | -0.5710% | 0.0901% | 3 | SELL |
| downtrend-02 | DOWNTREND | 2024-03-16 14:58:00 to 2024-03-16 15:58:00 | -0.7867% | 0.1701% | 3 | SELL |
| downtrend-03 | DOWNTREND | 2024-07-31 15:48:00 to 2024-07-31 16:48:00 | -1.3225% | 0.1437% | 3 | SELL |
| sideways-01 | SIDEWAYS | 2025-02-01 13:38:00 to 2025-02-01 14:38:00 | +0.0581% | 0.0472% | 3 | HOLD |
| sideways-02 | SIDEWAYS | 2024-02-11 00:38:00 to 2024-02-11 01:38:00 | -0.0614% | 0.0725% | 3 | HOLD |
| sideways-03 | SIDEWAYS | 2024-05-16 20:18:00 to 2024-05-16 21:18:00 | +0.0879% | 0.1144% | 3 | HOLD |

## Summary

### balanced

- Samples: `27`; errors: `0`
- LLM actions: `{'HOLD': 21, 'SELL': 2, 'BUY': 4}`
- Risk actions: `{'HOLD': 23, 'BUY': 4}`
- LLM to Risk: `{'HOLD->HOLD': 21, 'SELL->HOLD': 2, 'BUY->BUY': 4}`

| Regime | Expected | Samples | LLM matches | Risk matches |
|---|---|---:|---:|---:|
| DOWNTREND | SELL | 9 | 1 | 0 |
| SIDEWAYS | HOLD | 9 | 7 | 7 |
| UPTREND | BUY | 9 | 2 | 2 |

| Horizon | Matured | Gaps | Directional | D-good | D-bad | D-neutral | Precision | Avg net edge | Missed upside | Avoided downside |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5m | 26 | 1 | 3 | 0 | 3 | 0 | 0.0% | -0.6405% | 0 | 0 |
| 15m | 27 | 0 | 4 | 0 | 4 | 0 | 0.0% | -0.5263% | 0 | 1 |
| 30m | 26 | 1 | 4 | 0 | 2 | 2 | 0.0% | -0.3832% | 0 | 3 |
| 60m | 27 | 0 | 4 | 2 | 2 | 0 | 50.0% | -0.0660% | 1 | 5 |

## Observed Development Result

This technical-only smoke campaign completed 27 provider calls with no errors.
The LLM remained mostly defensive, matched 2 of 9 uptrend points and 1 of 9
downtrend points, and emitted 2 BUYs during sideways windows. The Risk Manager
blocked both SELL suggestions but approved all 4 BUY suggestions. Those BUYs had
negative average edge after configured costs at every measured horizon.

This is development evidence, not validation. It is too small to justify a
prompt or threshold change, and it must not be used as evidence for real-money
execution. The next candidate campaign is a frozen 300-call development plan;
validation remains untouched and holdout remains sealed.

## Interpretation Guardrails

- SELL is scored as avoided loss while reducing an existing BTC long exposure; it is not short PnL.
- BUY uses round-trip costs; SELL uses one exit cost because it reduces an existing long position.
- Size-weighted edge is a comparison proxy, not a reconstructed portfolio return.
- Synthetic or technical-only news modes are explicit interventions and cannot validate news performance.
- A useful conclusion requires enough matured BUY and SELL samples across multiple non-overlapping periods.
