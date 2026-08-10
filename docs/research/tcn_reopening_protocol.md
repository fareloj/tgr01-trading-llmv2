# TCN Reopening Protocol

## Decision

The archived TCN remains a failed trading experiment, not a production model.
Research is being reopened because the original evaluation contained an
important objective mismatch: the direction head learned first-touch barrier
classes, while the economic policy ignored that head and traded only when the
entire predicted endpoint-return interval crossed a fixed threshold. Zero
trades therefore did not test the direction head's economic usefulness.

This protocol permits a new experiment without changing the active trading
system. The TCN cannot authorize, size, block, or execute an order and is not
included in the Decision Agent payload.

## Corrections Already Implemented

1. Calibrated direction logits now feed an explicit abstaining probability
   policy.
2. Confidence and directional-margin thresholds are fitted on calibration data
   only.
3. Costs, non-overlapping positions, drawdown, and net returns are included in
   policy evaluation.
4. The policy disables itself when calibration finds no positive candidate
   with a minimum number of trades.
5. Barrier-trained checkpoints are selected by direction loss on validation,
   rather than by a combined loss dominated by endpoint regression.
6. Expanding walk-forward folds contain separate train, selection, calibration,
   and test windows with a time purge before every boundary.
7. The original quantile policy remains in reports as a frozen baseline.

## Next Dataset Version

The current 1-minute, 15/60-minute experiment is poorly matched to a strategy
whose decisions are refreshed around every 15 minutes and whose intended
holding period spans hours or days. The next dataset should be versioned rather
than replacing the archived files:

- decision cadence: one closed observation every 15 minutes;
- candidate horizons: 4 hours and 24 hours;
- initial context: at least 24 hours, with longer contexts tested explicitly;
- targets: endpoint returns and first-touch barriers whose distances exceed
  conservative fees and slippage;
- local domain: BTC/BRL remains the final evaluation market;
- global domain: BTCUSDT pretraining is an ablation, not an assumption;
- extra causal evidence: synchronized global-market features, calendar/session
  features, or other information available at decision time;
- no reconstructed historical news unless source timestamps and publication
  availability can be proven.

Feature names must state their true time units. A 15-minute bar must never be
silently stored under a field named `return_1m_pct`.

## Evaluation Contract

Every candidate must be compared on identical timestamps against:

- always HOLD;
- simple momentum;
- trend confirmation;
- RSI mean reversion;
- the archived TCN configuration;
- a small non-neural classifier when practical.

Model and policy thresholds may use train, selection, and calibration windows.
They may not use a fold's test window. The final holdout may be opened once
after all architecture and policy decisions are frozen.

Reports must include:

- balanced accuracy, macro F1, class precision and recall;
- calibration error, NLL, and Brier score;
- signal count, executed trades, overlap skips, and abstention rate;
- net return after fees and slippage, win rate, average trade, and drawdown;
- block-bootstrap confidence intervals across contiguous time blocks;
- metrics by regime and by walk-forward fold;
- all-zero/HOLD and simple-rule baselines on the same rows.

## Promotion Gate

The TCN may become read-only LLM evidence only when all of these are true:

1. Multiple walk-forward test folds show repeatable improvement over simple
   baselines after costs.
2. Confidence intervals do not indicate that the result is explained by one
   isolated regime.
3. The final untouched holdout confirms the frozen policy.
4. BUY and SELL behavior are both supported; aggregate accuracy cannot hide a
   one-sided model.
5. Dataset provenance, hashes, feature definitions, model configuration, and
   policy thresholds are persisted.
6. Failure or absence of the TCN degrades to `UNAVAILABLE`, never to permission
   to trade.

Passing this gate would allow a small, explicitly labeled research signal in
the LLM context. Deterministic freshness, exposure, position, cooldown, cost,
and risk rules would retain full authority.
