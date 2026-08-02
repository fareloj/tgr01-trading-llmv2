# TGR-01 Trading LLM V2

[![CI](https://github.com/fareloj/tgr01-trading-llmv2/actions/workflows/ci.yml/badge.svg)](https://github.com/fareloj/tgr01-trading-llmv2/actions/workflows/ci.yml)
[![Official RAG CI](https://github.com/fareloj/hybrid-rag-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/fareloj/hybrid-rag-engine/actions/workflows/ci.yml)

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
| Market | BTC/BRL, one-minute public data from Mercado Bitcoin |
| Execution | Paper trading only |
| Real exchange orders | Not implemented |
| Active database | PostgreSQL 16 |
| Default decision model | `openai/gpt-oss-120b` through an OpenAI-compatible Groq endpoint |
| Operator interfaces | Python/Textual TUI and Electron console |
| Neural model | TCN archived as unsuccessful research |
| RAG | Official [Hybrid RAG Engine](https://github.com/fareloj/hybrid-rag-engine); local memory remains auxiliary |
| Latest backend validation | 209 Python tests passing |
| Latest desktop validation | 6 Node tests, Vite build, and Electron smoke passing |

These test counts describe the state recorded on 2026-08-01. They validate
contracts, failure behavior, accounting, and interfaces. They do not measure
future returns.

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

Create local configuration files:

```powershell
Copy-Item .\.env.example .\.env
Copy-Item .\backend\.env.example .\backend\.env
```

Set a strong local PostgreSQL password and keep `DATABASE_URL` consistent.
Never commit either `.env` file.

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
py -3.11 .\backend\tests\run_paper_trading.py --cycles 10 --sleep 30
```

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
- Historical evaluation still needs more matured BUY and SELL observations
  across multiple regimes and after realistic costs.
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
