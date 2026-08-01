# Aceitacao Final - Escopo Paper Trading

Data da aceitacao: 2026-08-01

## Veredito

O TGR-01 Trading LLM V2 esta aceito para pesquisa local e paper trading
auditavel. O projeto nao implementa, habilita ou simula um endpoint privado de
ordens reais. Qualquer transicao para capital real e um projeto separado.

## Evidencias Reproduziveis

- PostgreSQL 16 e o unico banco do caminho ativo.
- Banco pytest isolado do banco da aplicacao e protegido contra duas suites
  simultaneas por advisory lock.
- Suite Python: 104 testes aprovados.
- Backend compilado com `python -m compileall`.
- Interface Electron/Vite compilada para producao.
- Dump PostgreSQL mais recente validado por `pg_restore --list`: formato
  custom, 78 entradas de catalogo e dados.
- RAG externo: 800 chunks densos e 800 lexicais, HNSW carregado e reranker em
  CUDA.
- Paper position reconciliada a partir dos logs legados e fechada exatamente
  com os saldos observados.

## Propriedades De Seguranca Validadas

- Falha fechada para candle stale, ausente, futuro ou malformado.
- Falha fechada para worker sem heartbeat e clock fora da tolerancia.
- Nenhuma mutacao de capital sem transacao PostgreSQL unica.
- Concorrencia de BUY e reconciliacao sem gasto duplo ou auditoria duplicada.
- Rejeicao de valores nao finitos, saldo negativo e posicao divergente.
- Taxa, slippage, notional, deltas, custo medio e PnL persistidos por execucao.
- RAG fora do caminho de aprovacao de trades e filtrado contra corpus estranho,
  paths operacionais, segredos e prompt injection.
- TUI e Electron executam apenas comandos presentes na allowlist do backend.

## Estado Vivo Na Aceitacao

- Capital paper preservado durante os red teams.
- Posicao BTC/BRL possui custo medio reconstruido e proveniencia dos logs.
- Ultimo ciclo validado terminou em HOLD sem alterar carteira.
- O provedor Mercado Bitcoin apresentou candle temporariamente stale durante a
  auditoria final; o preflight bloqueou o pipeline como projetado.

## Limitacoes Conhecidas

- A disponibilidade do candle depende da API publica do Mercado Bitcoin. Nao ha
  fallback silencioso para outro preco, pois misturar provedores alteraria a
  semantica do experimento.
- `/ingest`, `/embed` e reindexacoes do RAG devem ser serializados. Uma chamada
  concorrente de embed foi recuperada reiniciando somente o orquestrador RAG;
  o trading permaneceu inalterado.
- Metricas de acerto do LLM sao observacionais e dependem de horizonte,
  threshold, custos e regime. Elas nao sao uma verdade absoluta.
- O modelo pode permanecer em HOLD por longos periodos. Isso deve ser avaliado
  por cenarios historicos, nao corrigido reduzindo guardrails no caminho vivo.

## Comandos De Verificacao

```powershell
python -m pytest -q
python -m compileall -q backend
python .\backend\tests\trading_readiness_report.py
python .\backend\tests\dashboard_state.py
python .\backend\tests\query_external_rag.py --health
cd desktop
npm run build
```

Um resultado `BLOCKED` no readiness por dados stale e um resultado seguro. O
pipeline somente fica pronto quando dados, workers e clock estiverem dentro das
tolerancias configuradas.
