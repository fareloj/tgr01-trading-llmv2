# ML Dataset And Training Plan

## Purpose

This stage answers a question that must come before choosing a neural-network
architecture: does the stored BTC/BRL history contain enough clean,
out-of-sample information for a learned model to beat simple rules after
estimated costs?

The learned model, if eventually justified, will never replace the Risk
Manager. It may produce a directional probability or expected return. Python
will continue to validate inputs, size paper positions, apply exposure limits,
and reject unsafe actions.

## Dataset Contract

Each row represents a closed, observed BTC/BRL one-minute candle at timestamp
`t`. Feature columns use only candles at or before `t`:

- returns over 1, 5, 15, and 60 minutes;
- realized volatility over 15 and 60 minutes;
- RSI 14, normalized MACD histogram, and normalized ATR 14;
- EMA 9/21 spread and Bollinger z-score;
- volume z-score and prior Donchian-channel position;
- drawdown over 60 and 240 minutes;
- observed-data coverage over 60 and 240 minutes.

Labels use an exact future timestamp at 15 or 60 minutes. A future return must
exceed the configured round-trip cost plus the minimum desired net edge to be
labelled `BUY` or `SELL`; otherwise it is `HOLD`.

No future price, future return, or label column is present in the feature
allowlist.

## Missing Candles

The stored Mercado Bitcoin history contains frequent gaps of one to three
minutes and a smaller number of long collection interruptions. Treating all of
them equally either destroys the dataset or silently crosses outages.

The implemented policy is explicit:

1. Gaps up to 15 minutes are regularized with zero-volume synthetic candles
   whose OHLC equals the last observed close.
2. Gaps longer than 15 minutes start a new segment.
3. A segment must rebuild 240 candles of history before producing rows.
4. A decision row must be an observed candle.
5. By default, the exact future candle used for a label must also be observed.
6. Rolling observed coverage must be at least 80%.

This policy can be changed only through explicit configuration and remains
recorded in dataset metadata.

## Temporal Evaluation

Rows are sorted chronologically and split into train, validation, and test
partitions. The default ratio is 60/20/20. A 60-minute purge removes rows at the
end of train and validation whose future labels would cross into the next
partition.

Baselines are fixed rules rather than fitted models:

- always HOLD;
- 60-minute momentum;
- EMA/MACD trend confirmation;
- RSI mean reversion.

Accuracy is not trusted alone because HOLD is the majority class. Reports also
show macro F1, signal count, accepted non-overlapping trades, skipped overlaps,
directional trade accuracy, post-cost win rate, compounded return proxy, and
maximum drawdown.

## Current Snapshot

The local run on 2026-08-01 produced:

- 4,941 source candles;
- 2,203 eligible rows;
- 8 represented calendar days;
- 536 BUY, 1,212 HOLD, and 455 SELL labels;
- 92.84% mean observed coverage;
- 5 independent continuous segments.

All baselines that opened positions produced negative post-cost returns in both
validation and test. Always-HOLD produced zero return and relatively high raw
accuracy only because HOLD dominates the labels; its macro F1 remained low.

These findings do not prove that trading is impossible. They show that the
current sample is too small and that simple rules have not established an edge
that a more complex model should be expected to improve.

## Training Gate

The default conservative gate requires:

- at least 30,000 eligible rows;
- at least 30 calendar days;
- at least 1,000 rows for each BUY/HOLD/SELL label;
- at least 80% mean observed coverage.

Passing permits offline experiments only. It does not authorize paper or live
execution and does not prove profitability.

## Model Order

Once the gate passes, candidates should be tested in this order:

1. regularized multinomial logistic regression;
2. tree boosting such as LightGBM or XGBoost;
3. calibrated probability outputs and abstention thresholds;
4. only then, a compact temporal neural model such as TCN or a small LSTM.

A large Transformer is not justified by the current sample size. Model
selection must use validation only. The untouched test split is evaluated once
after all feature, threshold, and hyperparameter choices are frozen.

## Commands

```powershell
py -3.11 .\backend\tests\build_ml_dataset.py
py -3.11 .\backend\tests\evaluate_ml_baselines.py
```

Generated CSV, JSON, and Markdown reports are written to `backend/reports`,
which is ignored by Git because datasets and local runtime evidence should not
be committed.
