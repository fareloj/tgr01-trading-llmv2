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

## Global BTC Pretraining

Mercado Bitcoin currently retains BTC/BRL one-minute candles from 2023-03-31,
which is useful for exchange-specific calibration but does not cover enough
global BTC regimes by itself. A later pretraining stage may use the official
Binance BTCUSDT one-minute archive from 2017 onward. Binance publishes monthly
and daily archives with checksums.

Global and local data must not be concatenated as if they came from one market.
The planned design is:

1. normalize inputs as returns, volatility, volume ratios, and relative
   distances instead of absolute USD/USDT/BRL prices;
2. retain exchange and quote-currency domain identifiers;
3. pretrain a compact temporal encoder on global BTCUSDT regimes;
4. fine-tune and calibrate on BTC/BRL Mercado Bitcoin data;
5. predict return distributions and uncertainty at 15/60/240 minutes;
6. translate distributions into candidate actions only after local fees,
   slippage, position state, and deterministic risk gates.

The first neural candidate should be a compact temporal convolutional network
(TCN), not a large Transformer. It must beat logistic regression and gradient
boosting in walk-forward validation before being connected to paper trading.
Random train/test splits, absolute-price targets, and direct model-controlled
order sizing remain prohibited.

The official Binance archive was collected through June 2026 with checksum
verification. It contains `4,656,799` BTCUSDT one-minute candles and yields
`4,648,648` eligible causal rows: `1,055,212 BUY`, `2,564,297 HOLD`, and
`1,029,139 SELL` under the global-domain cost assumption. The archive has 34
gaps larger than one minute; large gaps create new feature segments.

Two historical archive intervals required bounded timestamp normalization:
`21,602` rows had a source offset within 30 seconds of the minute grid. The
normalizer uses the following minute to avoid collision with the preceding
short candle. Per-month sidecars bind the normalization count to the official
archive SHA-256. This limitation must remain part of model provenance.

The host Python 3.11 environment was validated with `torch 2.12.1+cu130` on the
RTX 3060 (CUDA compute capability 8.6). The local wheel matched the SHA-256
published by the official PyTorch index before installation, and an actual CUDA
matrix multiplication completed successfully. Training code must still select
the device explicitly, record the PyTorch/CUDA versions in every checkpoint,
and remain offline with no paper or live order write path.

## Full Mercado Bitcoin Snapshot

The resumable download completed on 2026-08-01 with:

- `1,342,682` raw BTC/BRL one-minute candles;
- `175` independently validated seven-day chunks;
- `819,594` eligible causal examples after continuity and coverage filters;
- labels: `145,527 BUY`, `531,577 HOLD`, and `142,490 SELL`;
- chronological partitions with a 60-minute purge between them.

The readiness gate passed, but the deterministic baselines exposed the main
training risk. Momentum, trend confirmation, and RSI mean reversion all lost
approximately all simulated capital because they traded too frequently for the
configured round-trip cost. Always-HOLD preserved capital. Neural-model success
must therefore be measured against after-cost return, drawdown, turnover, and
calibration, not raw classification accuracy alone.

The network should output multi-horizon return distributions and uncertainty.
Entry policy remains deterministic and must require a net edge after costs,
signal persistence, cooldown, portfolio limits, and fresh market data.

## Commands

```powershell
py -3.11 .\backend\tests\build_ml_dataset.py
py -3.11 .\backend\tests\evaluate_ml_baselines.py
py -3.11 .\backend\tests\download_mb_history.py
py -3.11 .\backend\tests\download_binance_history.py
py -3.11 .\backend\tests\build_multidomain_ml_dataset.py
```

Generated CSV, JSON, and Markdown reports are written to `backend/reports`,
which is ignored by Git because datasets and local runtime evidence should not
be committed.
