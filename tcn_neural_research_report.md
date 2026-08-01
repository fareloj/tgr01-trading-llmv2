# TCN Neural Research Report

## Purpose

The neural model is an offline evidence generator for the trading pipeline. It
does not place orders, choose position size, bypass deterministic risk rules, or
turn a weak forecast into a trade. Its purpose is to estimate uncertainty and
test whether recent BTC market structure contains a repeatable short-horizon
signal.

## Data And Causality

- Global pretraining: 4,648,648 causal BTCUSDT examples from the verified
  Binance one-minute archive.
- Local adaptation: 969,131 minute-regular BTC/BRL context rows from Mercado
  Bitcoin.
- Input: 240 prior one-minute rows and 17 price, trend, volatility, volume,
  drawdown, and data-coverage features.
- Architecture: six-level causal residual TCN, 32 channels, receptive field of
  253 minutes.
- Outputs: p10/p50/p90 future-return quantiles and calibrated SELL/HOLD/BUY
  first-touch probabilities at 15 and 60 minutes.

No feature contains a future price or label. Synthetic short-gap candles can
provide context but cannot become an endpoint or a valid first-touch path.
Windows with missing future candles, long gaps, or a candle touching both
barriers are excluded from direction training.

## Evaluation Protocol

The chronology is never shuffled across partitions:

1. The first 60% is used for training.
2. The next part is used for epoch selection.
3. A later, disjoint window calibrates probabilities and compares variants.
4. The final 20% remains untouched until the architecture and hyperparameters
   are frozen.
5. A 60-minute purge prevents labels from crossing every boundary.

The feature and target scalers are fitted on training data only. Class weights
are also fitted on training labels only. Temperature scaling is fitted on the
selection window and assessed on the later calibration window.

## Experiments

The initial quantile-only model predicted endpoint returns reasonably well but
improved MAE over a zero-return forecast by less than 1% and produced no
actionable interval. Robust target normalization improved optimization but did
not create a useful edge. A larger 64-channel model did not consistently beat
the 32-channel model. Removing Binance pretraining did not improve local
generalization, so negative transfer was not the primary bottleneck.

The final experiment added a direction head trained on first-touch paths:

- 15-minute barrier: +/-0.20%.
- 60-minute barrier: +/-0.40%.
- Quantile and direction losses share the causal encoder.
- Direction confidence is temperature calibrated.

Calibration balanced accuracy reached 51.77% at 15m and 50.79% at 60m. The
reserved temporal test, opened once after model selection, produced:

| Metric | 15m | 60m |
|---|---:|---:|
| Balanced accuracy | 51.90% | 54.32% |
| Macro F1 | 47.87% | 49.76% |
| SELL precision | 54.95% | 57.74% |
| BUY precision | 45.23% | 44.09% |
| Expected calibration error | 2.36% | 2.85% |
| p10-p90 return coverage | 75.57% | 77.52% |
| MAE improvement over zero return | -0.29% | -0.69% |

The result is statistically more informative than always choosing the majority
class, especially for downside evidence, but it is not a demonstrated trading
edge. BUY precision remains weak, endpoint regression loses to the zero-return
baseline, and the cost-aware quantile policy abstained on all test rows.

## Operational Boundary

`TCNAdvisor` is deliberately read-only and returns:

- `status=RESEARCH_ONLY`;
- `execution_eligible=false`;
- `can_authorize_order=false`.

It refuses research output if the checkpoint was not barrier-trained, lacks a
reserved temporal test, lacks calibrated temperatures, or falls below the
balanced-accuracy research floor. The checkpoint is loaded with PyTorch's
`weights_only=True` mode and a versioned schema.

The neural output may later be included as one small item of LLM context. It
must never override stale-data checks, exposure limits, costs, cooldown,
position state, or any other deterministic risk gate.

## Reproduction

```powershell
py -3.11 .\backend\tests\build_barrier_targets.py `
  .\backend\reports\binance_full_dataset.csv `
  --output .\backend\reports\binance_barrier_targets.npz `
  --horizons 15 60 --barrier-pct 0.20 0.40

py -3.11 .\backend\tests\build_barrier_targets.py `
  .\backend\reports\mb_tcn_dataset.csv `
  --output .\backend\reports\mb_barrier_targets.npz `
  --horizons 15 60 --barrier-pct 0.20 0.40

py -3.11 .\backend\tests\train_tcn.py `
  --device cuda --channels 32 --batch-size 1024 `
  --global-epochs 8 --local-epochs 12 `
  --direction-loss-weight 1.0 --class-weight-power 0.5 `
  --direction-target-mode barrier

# Run only once after every model choice is frozen.
py -3.11 .\backend\tests\train_tcn.py `
  --device cuda --channels 32 --batch-size 1024 `
  --global-epochs 8 --local-epochs 12 `
  --direction-loss-weight 1.0 --class-weight-power 0.5 `
  --direction-target-mode barrier --evaluate-test `
  --output-dir .\backend\reports\tcn_barrier_final

py -3.11 .\backend\tests\inspect_tcn_advisory.py `
  --checkpoint .\backend\reports\tcn_barrier_final\local_best.pt `
  --device cuda
```

Generated datasets, targets, checkpoints, and reports remain ignored runtime
artifacts under `backend/reports`.
