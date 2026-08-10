# TCN Research Boundary

Status: offline research reopened on 2026-08-09; still excluded from the active
trading pipeline.

The code in this directory remains reproducible research. It is not imported by
the live-data paper cycle, Decision Agent, Risk Manager, paper simulator,
operational command catalog, TUI, or Electron application. `TCNAdvisor` can be
invoked only through the explicit offline inspection command.

## Final Evidence

- Reserved-test balanced accuracy: 51.90% at 15 minutes and 54.32% at 60 minutes.
- SELL precision: 54.95% and 57.74%.
- BUY precision: 45.23% and 44.09%.
- Return regression did not beat the zero-return baseline.
- The cost-aware policy produced no executable trades.

Generated datasets and checkpoints remain ignored under `backend/reports`.
The experiment ledger is in the
[TCN research report](../../docs/research/tcn_neural_research_report.md).

## Reopening Protocol

The previous experiment failed to demonstrate an economic edge. New work must
follow the versioned [reopening protocol](../../docs/research/tcn_reopening_protocol.md)
and provide:

1. additional causal information beyond one-minute OHLCV derivatives;
2. walk-forward evaluation across multiple market regimes;
3. simple non-neural baselines on the identical split;
4. confidence intervals and net performance after fees and slippage;
5. evidence that adding the model improves the frozen pipeline out of sample.

The first reopening changes align first-touch probabilities with an abstaining,
cost-aware evaluation policy, select barrier checkpoints by validation
direction loss, and add purged walk-forward folds. They do not satisfy the
whole protocol and do not change the operational boundary.

Until every criterion is met on unseen data, neural output must not enter an
LLM payload or any order-approval path.
