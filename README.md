# TGR-01 Trading LLM V2

[![Trading CI](https://img.shields.io/github/actions/workflow/status/fareloj/tgr01-trading-llmv2/ci.yml?branch=main&label=Trading%20CI)](https://github.com/fareloj/tgr01-trading-llmv2/actions/workflows/ci.yml)
[![RAG CI](https://img.shields.io/github/actions/workflow/status/fareloj/hybrid-rag-engine/ci.yml?branch=main&label=RAG%20CI)](https://github.com/fareloj/hybrid-rag-engine/actions/workflows/ci.yml)

TGR-01 is a local BTC/BRL paper-trading research system. It combines public
market data, recent news, deterministic indicators, an LLM decision agent, a
deterministic Risk Manager, and auditable paper execution.

Its purpose is not to prove that an LLM can predict Bitcoin. The project tests
a narrower architecture:

> Can an LLM interpret already-calculated market evidence while code retains
> control of data validation, risk limits, sizing, execution, and audit?

The answer is still being evaluated. The repository has strong safety and
observability coverage, but it has **not demonstrated a profitable strategy**.

## Current Status

| Area | Status |
| --- | --- |
| Market | BTC/BRL, one-minute public data from Mercado Bitcoin; 15-minute decision cadence |
| Execution | Paper trading only |
| Real exchange orders | Not implemented; authenticated BUY/SELL validation is dry-run only |
| Active database | PostgreSQL 16 |
| Experimental agent models | CIO: `glm-5.2:cloud`; News/Technical: `deepseek-v4-flash:cloud` through Ollama |
| Operator interfaces | Python/Textual TUI and Electron console |
| Neural model | TCN archived as unsuccessful research |
| RAG | Official [Hybrid RAG Engine](https://github.com/fareloj/hybrid-rag-engine); local memory remains auxiliary |
| Latest backend validation | 340 Python tests passing |
| Latest desktop validation | 6 Node tests, Vite build, and Electron smoke passing |

These test counts describe the state recorded on 2026-08-14. They validate
contracts, failure behavior, accounting, and interfaces. They do not measure
future returns.

The current Ollama Cloud configuration is a **paper-only experiment**. GLM 5.2
acts as the CIO/Decision Agent, while DeepSeek V4 Flash handles the News and
Technical roles when the multi-agent pipeline is enabled. This assignment is
engineering configuration, not model-selection or profitability evidence.

The accepted paper-only boundary and known limitations are documented in
[Final Acceptance](docs/reports/FINAL_ACCEPTANCE.md). The latest adversarial
review is in [Operational Red Team](docs/reports/RED_TEAM_REPORT_2026-08-01.md).

## Interfaces

### Electron operations console

![Electron operations console](docs/assets/electron-ops-console.png)

### Textual terminal interface

![Textual terminal interface](docs/assets/tui-operational-console.png)

Both interfaces call the same allowlisted Python commands. Neither contains
trading logic or bypasses preflight and risk checks.

## How A Cycle Works

```text
Mercado Bitcoin public API        RSS news sources
             |                         |
             v                         v
      price_worker.py             news_worker.py
             |                         |
             +-----------+-------------+
                         v
                    PostgreSQL
                         |
                         v
               deterministic payload
          RSI / MACD / ATR / freshness / exposure
                         |
                         v
                LLM Decision Agent
           BUY / SELL / HOLD + evidence brief
                         |
                         v
              deterministic Risk Manager
                         |
                         v
                 paper simulator
          fees / slippage / cost basis / PnL
                         |
                         v
             audit and future evaluation
```

The LLM does not calculate indicators, edit balances, choose arbitrary code,
or submit an exchange order. Its structured response is validated with
Pydantic. An API error, malformed output, missing key, stale market, or invalid
numeric value fails to `HOLD` or aborts before the model is called.

## Deterministic Safety Boundary

The active path includes checks for:

- missing, stale, future, or malformed candles;
- missing, stale, or future worker heartbeats;
- local clock skew;
- incompatible RSI and MACD direction;
- prompt-like instructions embedded in news;
- stale news and negative-news red flags;
- minimum conviction and hybrid confidence;
- cooldown and maximum exposure;
- daily mark-to-market drawdown;
- invalid or inconsistent BRL/BTC position state;
- non-finite fee, slippage, price, ATR, and sizing inputs.

Negative news never authorizes a `BUY`. It can avoid a generic reliability
penalty for `SELL` only when market and news data are fresh, MACD is bearish,
RSI is not oversold, and no untrusted instruction was detected. The directional
gate still has final authority.

Paper orders run inside one PostgreSQL transaction. The audit stores expected
and effective price, fee, slippage, BRL/BTC deltas, average cost, realized PnL,
equity before and after execution, the LLM evidence brief, and the risk verdict.

Every live paper cycle also receives a UUID in the PostgreSQL `trading_runs`
table. The lifecycle record covers successful cycles, preventive aborts and
exceptions, recording the reached stage, duration, configured model/profile,
validated LLM decision, Risk Manager verdict, compact market snapshot,
execution audit and the related `trade_logs` row. Secrets, access tokens, full
prompts and unvalidated raw model output are deliberately excluded.

The active Decision Agent also receives a bounded episodic memory of at most
eight audited decisions from the preceding two hours. Each episode contains
only the proposed action, conviction, deterministic Risk Manager verdict and a
small whitelist of market-scenario fields and categorical justification tags.
Prior free-form reasoning, decision briefs, prompts, headlines and nested
snapshots are never reinjected. This memory can expose recent inconsistency but
is explicitly not market evidence and cannot approve or size an order.
Consecutive identical episodes are compacted with a bounded `repeat_count` so
rapid cycles cannot fill the prompt with duplicate context.

```powershell
py -3.11 .\backend\tests\analyze_trading_runs.py --limit 30
```

### Authenticated exchange dry-run

The optional Mercado Bitcoin validator authenticates with OAuth2 and reads the
configured account's balances, BTC/BRL fees, public symbol limits, and current
orderbook. It then builds and checks one market BUY candidate and one market
SELL candidate. The estimates are snapshots, not execution guarantees.

The client intentionally implements no order, cancellation, transfer, or
withdrawal method. Its only POST is the OAuth token exchange; all exchange data
operations are GET requests. `REAL_TRADING_ENABLED` must remain `false`.

```powershell
py -3.11 .\backend\tests\validate_mb_order_dry_run.py `
  --buy-brl 1.00 --sell-btc 0.00000150
```

A blocked candidate is a valid safety outcome. For example, an account with no
available BTC can validate the SELL schema and market constraints in tests, but
the live dry-run will block it on the real balance before any submission.

## Evaluation, Not An "Accuracy" Number

Decisions can be compared against later candles at 5, 15, 30, and 60 minutes.
The evaluator distinguishes:

- a matured observation;
- a decision whose horizon has not matured;
- a data gap where no candle exists close enough to the target timestamp.

The default maximum timestamp deviation is 90 seconds. A much later candle
cannot silently replace missing data. Reports include fees, slippage, exposure,
blocked actions, missed upside, and avoided downside. Their labels are review
aids, not ground truth about what a human trader should have done.

Useful commands:

```powershell
py -3.11 .\backend\tests\analyze_trade_logs.py --since-id 1 --limit 50
py -3.11 .\backend\tests\analyze_entry_decisions.py --since-id 1
py -3.11 .\backend\tests\evaluate_decisions.py --since-id 1 --horizons 5,15,30,60
py -3.11 .\backend\tests\trading_readiness_report.py
```

### Reproducible historical campaign

`run_historical_campaign.py` freezes non-overlapping uptrend, downtrend, and
sideways windows before it calls an LLM. Every selected prompt variant receives
the same timestamps, exposure assumption, horizons, and news mode. The campaign
compares the raw LLM action with the deterministic Risk Manager verdict and
scores matured observations after the paper fee and ATR-derived slippage
assumptions. It never writes an order, trade log, or portfolio balance.

Inspect the frozen sample without consuming provider quota:

```powershell
py -3.11 .\backend\tests\run_historical_campaign.py `
  --from-local "2026-06-06 00:00" --to-local "2026-06-07 23:59" `
  --variants current balanced --plan-only
```

Run the same stratified comparison after reviewing the call count:

```powershell
py -3.11 .\backend\tests\run_historical_campaign.py `
  --from-local "2026-06-06 00:00" --to-local "2026-06-07 23:59" `
  --variants current balanced --news-mode historical
```

Reports are timestamped under `backend/reports/` and are ignored by Git. The
selected regimes use the complete future window, so this evaluates behavior in
known conditions; it is not an unbiased backtest or evidence of profitability.

For larger experiments, the Mercado Bitcoin collector stores validated,
resumable chunks outside Git. A separate manifest hashes every chunk and creates
chronological `development`, `validation`, and sealed `holdout` partitions with
purged boundaries. This prevents future labels near a split from leaking into
prompt or rule selection.

```powershell
py -3.11 .\backend\tests\download_mb_history.py
py -3.11 .\backend\tests\prepare_historical_evaluation_dataset.py
py -3.11 .\backend\tests\import_historical_partition.py `
  --partition development
py -3.11 .\backend\tests\run_historical_campaign.py `
  --dataset-manifest .\backend\data_exports\historical_evaluation\manifest.json `
  --partition development --variants balanced --news-mode technical-only `
  --selection-strategy stratified
```

The holdout requires its exact `dataset_id` as an explicit approval argument.
It should only be opened after prompts, tools, thresholds, and risk rules are
frozen. Candle history alone cannot reconstruct point-in-time news, so older
periods must use technical-only/synthetic controls unless an independently
archived and timestamp-correct news corpus is available.

`stratified` samples multiple move and volatility severity bands and is the
default for model evaluation. `extreme` remains available for explicit stress
tests; it must not be treated as a representative market sample.

A 27-call technical-only development smoke test completed without provider
errors, but exposed a fixture contradiction: news had been removed while its
health fields still described it as fresh. The report is retained as an
invalidated development incident, not trading evidence. The fixture and
auditable decision context are now deterministic; the frozen 300-call campaign
is the next candidate evaluation. See
[Historical Development Campaign](docs/reports/HISTORICAL_DEVELOPMENT_CAMPAIGN_2026-08-04.md).

Paired campaign reports can be consolidated while verifying that price, RSI,
MACD, and ATR stayed identical across each news intervention:

```powershell
py -3.11 .\backend\tests\compare_historical_campaigns.py `
  "backend/reports/matrix_*.json"
```

The August 2026 diagnostic compared historical news, synthetic fresh-neutral
news, and an early empty-news fixture over 27 paired timestamps (81 decisions).
That early fixture incorrectly retained fresh news-health fields, so its result
is an ablation diagnostic rather than a valid unavailable-news campaign.
Removing or neutralizing news increased directional activity, but the added
paper actions had negative average edge after configured costs at every tested
horizon. This result supports keeping news as risk context; it does not justify
loosening the Risk Manager or using real funds. See the full
[paired news-mode comparison](docs/reports/HISTORICAL_NEWS_MODE_COMPARISON_2026-08-04.md).

### Experimental multi-agent validation

The shadow-only multi-agent prototype assigns GPT-OSS 20B to bounded news and
technical interpretation and GPT-OSS 120B to the final proposal. A frozen
50-snapshot validation produced seven Risk-approved directional actions: four
were favorable and three unfavorable after configured costs. Five of 50 final
outputs violated the stricter stale-news/evidence contract and failed closed or
were already HOLD. This is useful safety evidence, not evidence of a profitable
strategy. See the complete
[multi-agent historical validation](docs/reports/MULTI_AGENT_HISTORICAL_VALIDATION_2026-08-10.md).

## LLM Analysis Tools

An optional protocol lets the model request up to three bounded calculations:

- multi-timeframe trend alignment;
- Donchian range breakout;
- drawdown and downside semideviation;
- volume and OBV confirmation.

The model selects a strict contract. Python performs the calculation with
fixed limits and returns compact evidence. The model cannot provide executable
code, SQL, network targets, or arbitrary memory writes. This path is disabled
by default and remains paper-only.

Research basis and red-team evidence:

- [Trading tool research](docs/research/btc_trading_tools_research.md)
- [Tool protocol red team](docs/reports/LLM_TOOL_RED_TEAM_REPORT_2026-08-01.md)

## Official RAG

The official retrieval backend is
[fareloj/hybrid-rag-engine](https://github.com/fareloj/hybrid-rag-engine), a
separate six-service Docker project. Keeping it in its own repository makes it
reusable by other projects and gives retrieval its own CI, releases, failure
domain, and performance evaluation.

In the current TGR-01 implementation, the official RAG is used by operator
queries, health/readiness views, and review tooling. It is not yet injected
into the live Decision Agent. It also remains outside the deterministic trade
approval path: retrieval cannot approve, block, or size an order.

### Auxiliary local memory

The repository contains a small PostgreSQL-backed store for curated project
documents, recent news, past decision cases, chunks, and retrieval audit logs.
It predates the official engine and remains useful for lightweight audit and
decision-case inspection. It is not the primary RAG backend and is not in the
deterministic order-approval path.

```powershell
py -3.11 .\backend\tests\ingest_rag_sources.py --project-docs
py -3.11 .\backend\tests\ingest_rag_sources.py --news-hours 24 --news-limit 20
py -3.11 .\backend\tests\ingest_decision_cases.py --since-id 1 --limit 100
py -3.11 .\backend\tests\query_decision_memory.py --current-payload --limit 5
```

### Hybrid RAG service

[The official Hybrid RAG Engine](https://github.com/fareloj/hybrid-rag-engine)
service code is **not duplicated in this repository**. TGR-01 contains the HTTP
client, safety filters, health/query utilities, and a Compose override that
mounts this repository read-only for ingestion.

The official system combines local embeddings, PostgreSQL/pgvector, a C++
exact/HNSW dense index, Java Lucene/BM25, reciprocal-rank fusion, and a local
Qwen reranker on CUDA. In its own final acceptance it reported six healthy
containers, HNSW recall@10 of 1.0, search p95 around 310 ms, and modest MRR and
nDCG@5 improvements from reranking. Those are retrieval measurements from the
official RAG repository; cloning TGR-01 alone does not reproduce them, and they say
nothing about trading profitability.

TGR-01 treats every retrieved chunk as untrusted. It fixes the corpus, bounds
query and response sizes, rejects foreign-corpus and injection-like results,
and records retrieval mode and fallback reason. If the service or reranker is
unavailable, trading analysis continues without RAG evidence. RAG content
cannot approve, block, or size an order.

```powershell
py -3.11 .\backend\tests\query_external_rag.py --health
py -3.11 .\backend\tests\query_external_rag.py "where is stale market data rejected"
```

## TCN Research: Archived

A multi-task Temporal Convolutional Network was trained as an offline evidence
experiment using causal one-minute sequences and separate global/local data
domains. It did not meet the project's utility threshold.

On the reserved BTC/BRL temporal test:

- balanced accuracy was 51.90% at 15 minutes and 54.32% at 60 minutes;
- SELL precision was 54.95% and 57.74%;
- BUY precision was 45.23% and 44.09%;
- return-error performance was worse than a zero-return baseline;
- the calibrated policy produced no executable trades.

Those results are too weak for trading evidence. The TCN is frozen, excluded
from the Decision Agent, Risk Manager, paper executor, TUI, and Electron
console, and returns `RESEARCH_ONLY` if loaded. Reopening it requires a new
pre-registered experiment with a frozen out-of-sample test and a meaningful
net-of-cost improvement.

See [TCN archive boundary](backend/ml/ARCHIVED.md) and
[TCN research report](docs/research/tcn_neural_research_report.md).

## Setup

Requirements:

- Python 3.11;
- Docker Desktop or another Docker Compose runtime;
- Node.js for the Electron interface;
- one compatible LLM API key for decision experiments.
- Ollama signed in locally when using the experimental cloud-model default.

Create local configuration files:

```powershell
Copy-Item .\.env.example .\.env
Copy-Item .\backend\.env.example .\backend\.env
```

Set a strong local PostgreSQL password and keep `DATABASE_URL` consistent.
Never commit either `.env` file.

The example `backend/.env.example` selects Ollama's local OpenAI-compatible
endpoint and `glm-5.2:cloud`. `LLM_*` variables are canonical. Existing
`GROQ_*` variables remain supported as a legacy fallback for comparisons.

An experimental multi-agent configuration is also documented, but remains
disabled and shadow-only by default. It assigns `deepseek-v4-flash:cloud` to news
analysis and interpretation of the deterministic eight-hour technical context,
and the configured Decision model to the final proposal. Both configured tags
passed a live structured-contract smoke test on 2026-08-14. This is an
evaluation configuration, not evidence that multiple agents improve trading
results. Outputs retain source IDs and the final model also receives the
original snapshot to reduce correlated summary risk.

Start PostgreSQL and initialize the schema:

```powershell
docker compose up -d db
py -3.11 .\backend\core\database.py
```

Start workers and run strict preflight:

```powershell
py -3.11 .\backend\tests\start_workers.py
py -3.11 .\backend\tests\preflight_data_date.py --require-news-today --require-workers --require-clock-sync
```

Run paper trading only after preflight passes:

```powershell
py -3.11 .\backend\tests\run_paper_trading.py --cycles 10 --sleep 900
```

The worker keeps one-minute candles for indicator continuity. The Decision
Agent is paced every 15 minutes, and the shared freshness policy accepts at
most 20 minutes of source publication lag. A stale candle still aborts before
the model is called. Shorter intervals are diagnostic only and must not be
treated as independent market observations.

For auditable forward testing, collection and future evaluation are separate
phases. The finalizer refuses to score horizons that have not matured:

```powershell
py -3.11 .\backend\ops\run_forward_session.py collect --cycles 96 --sleep 900 --horizons 15,60,240,480
py -3.11 .\backend\ops\run_forward_session.py finalize --session forward_YYYYMMDD_HHMMSS_utc
```

Each session records its starting trade-log ID, model configuration, command
return codes, raw outputs, and a maturity deadline under
`backend/reports/forward_sessions/`. This remains paper-only. The active live
runtime is still the single-agent pipeline; the multi-agent configuration is
recorded as shadow evidence and does not execute orders.

Open the TUI:

```powershell
.\run_tgr01.bat
```

Open the Electron console:

```powershell
Set-Location .\desktop
npm install
npm run dev
```

## Verification

### Continuous integration

The required `CI` workflow runs on every push to `main` and every pull request:

- Python 3.11 against a real PostgreSQL 16 service;
- backend compilation, deterministic tests, and chaos checks;
- Node tests and the Vite production build;
- Electron smoke testing under Xvfb.

The official RAG has its own CI for Python contracts, protobuf generation, C++,
Java, and Docker builds. TGR-01 also provides a manual `Official RAG Integration`
workflow. On a self-hosted Windows runner labeled `gpu`, it checks out both
repositories, starts all six Docker services with CUDA, ingests TGR-01, embeds
and reindexes it, then executes a real health check and retrieval through the
TGR-01 client. It is intentionally not part of every cloud-hosted pull request:
the complete stack requires an NVIDIA runtime, model downloads, and persistent
caches.

Backend:

```powershell
py -3.11 -m pytest backend\tests -q
py -3.11 .\backend\tests\chaos_monkey.py
py -3.11 .\backend\tests\redteam_llm_matrix.py
py -3.11 .\backend\tests\trading_readiness_report.py
```

Desktop:

```powershell
Set-Location .\desktop
npm test
npm run build
npm run test:electron
```

The LLM matrix consumes provider quota and its result can vary. The
deterministic tests should not depend on an external model.

## Repository Layout

```text
backend/
  agents/       LLM contracts and decision agent
  analysis/     bounded deterministic analysis tools
  core/         PostgreSQL schema, repository, clock and runtime safety
  data/         public market and news workers
  execution/    transactional paper simulator
  features/     indicators and payload construction
  ml/           archived neural research code
  ops/          allowlisted TUI/Electron commands
  rag/          auxiliary local store and official RAG service client
  risk/         deterministic Risk Manager
  tests/        tests and operational/research utilities
desktop/        React, Vite, and Electron operations console
docs/assets/    interface screenshots
docs/reports/   acceptance and red-team evidence
docs/research/  retained research with current relevance
```

Generated databases, reports, exports, model files, logs, secrets, and Node
dependencies are ignored by Git.

## Known Limitations

- No claim of profitability or predictive edge is supported by current data.
- The live Decision Agent has been checked on a small adversarial matrix, not a
  statistically representative market sample.
- The historical campaign runner exists, but a statistically useful conclusion
  still needs many independent periods with matured BUY and SELL observations.
- News red flags are lexical heuristics and can produce false positives.
- Public APIs and RSS feeds can be delayed, unavailable, or structurally
  inconsistent; the safe response is to stop or hold.
- The official [Hybrid RAG Engine](https://github.com/fareloj/hybrid-rag-engine)
  improves retrieval, not price prediction.
- There is no real-order endpoint, fill reconciliation, exchange balance source
  of truth, or production incident process.

This repository is suitable for local research and paper trading. Real-money
execution would require a separate architecture, threat model, reconciliation
system, and acceptance process.

## License

No license file is currently included. Public visibility does not grant
permission to reuse, redistribute, or commercialize the code.
