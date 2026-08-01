# Active Trading Pipeline Roadmap

The TCN work is archived research. It cannot authorize, block, size, or execute
an order. Active development now targets the auditable paper-trading pipeline.

## Current Boundary

```text
live read-only data -> deterministic indicators -> LLM suggestion
                    -> deterministic Risk Manager -> paper executor
                    -> PostgreSQL audit -> future evaluation
```

There is no private exchange order path. Moving from paper capital to real
capital is explicitly outside the current acceptance and requires a separate
design and red team.

## Completed Foundations

- PostgreSQL-only runtime and isolated test database;
- strict worker, clock, candle, news, and API-key preflight;
- Pydantic-validated LLM output with a short evidence brief;
- deterministic RSI/MACD/ATR, directional, cooldown, exposure, and drawdown gates;
- action-aware negative-news policy: it blocks BUY and may only confirm SELL
  when fresh bearish technical evidence agrees and RSI is not oversold;
- transactional paper execution with configurable fee and slippage assumptions;
- position, cost-basis, realized-PnL, equity, and daily-drawdown audit state;
- strict future evaluation that separates immature decisions from data gaps;
- shared TUI/Electron operational command allowlist;
- internal memory and optional external RAG kept outside order approval;
- fail-closed neural boundary documented in `backend/ml/ARCHIVED.md`.

## Current Validation Phase

1. Build larger historical replay sets across uptrend, downtrend, sideways,
   volatility shock, stale-news, and contradictory-signal regimes.
2. Evaluate LLM suggestion and final Risk Manager action separately.
3. Mature 5/15/30/60-minute outcomes using exchange candles inside the allowed
   timestamp tolerance; classify missing candles as data gaps.
4. Include fees, slippage, cooldown, exposure, and opportunity cost in every
   result instead of reporting a single misleading accuracy number.
5. Compare prompt/model profiles on frozen scenario sets before changing the
   production profile.
6. Review false positives and false negatives manually with the persisted
   payload snapshot and decision brief.

## Acceptance Gates Before Any New Scope

- all deterministic and interface tests pass;
- LLM adversarial matrix preserves expected LLM and final risk actions;
- no stale/future/malformed data can establish equity state or reach execution;
- position and virtual balances reconcile exactly after concurrent tests;
- every approved paper order has complete fee, slippage, balance, cost-basis,
  and future-evaluation evidence;
- long replay reports show enough matured BUY and SELL samples to estimate
  behavior by regime;
- no research model or RAG result can bypass the Risk Manager.

## Explicitly Deferred

- TCN retraining or neural architecture search;
- fine-tuning an LLM;
- autonomous real-money execution;
- exchange credentials, private endpoints, fills, and reconciliation;
- automatic strategy changes based only on an LLM review.

The next milestone is not "more trades." It is a statistically useful,
reproducible paper dataset that explains when the LLM, Risk Manager, or data
pipeline helped or failed.
