# Aceitacao Final - Escopo Paper Trading

Data da aceitacao: 2026-08-01
Revalidada em: 2026-09-17

## Veredito

O TGR-01 Trading LLM V2 esta aceito para pesquisa local e paper trading
auditavel. O projeto nao implementa, habilita ou simula um endpoint privado de
ordens reais. Qualquer transicao para capital real e um projeto separado.

A revalidacao de 2026-09-17 refez as contagens e executou os comandos de
verificacao. O resultado consolidado, incluindo as divergencias encontradas
nesta propria pagina, esta em `PROJECT_CLOSURE_2026-09-17.md`. O veredito de
escopo acima permanece o mesmo; o que mudou foi o estado numerico do projeto.

## Evidencias Reproduziveis

- PostgreSQL 16 e o unico banco do caminho ativo.
- Banco pytest isolado do banco da aplicacao e protegido contra duas suites
  simultaneas por advisory lock.
- Suite Python: 209 testes aprovados na aceitacao original; **453 aprovados na
  revalidacao de 2026-09-17** (438 antes de fechar os 3 achados do Risk Manager,
  +15 testes de regressao), incluindo fronteira neural fail-closed e smoke test
  real da TUI.
- Desktop: 6 testes Node na aceitacao original; **35 na revalidacao de
  2026-09-17**, com build Vite e smoke Electron aprovados, exit code 0; 17 acoes
  operacionais cobertas e nenhum erro de renderer ou overflow horizontal.
- Auditoria npm: zero vulnerabilidades conhecidas. Na revalidacao,
  `npm audit --omit=dev` continua em zero; `npm audit` completo lista 6 avisos
  restritos a devDependencies (ver `PROJECT_CLOSURE_2026-09-17.md`).
- Backend compilado com `compileall` (via venv na revalidacao de 2026-09-17).
- Interface Electron/Vite compilada para producao.
- Dump PostgreSQL mais recente validado por `pg_restore --list`: formato
  custom, 78 entradas de catalogo e dados.
- RAG oficial (servico Docker separado): 800 chunks densos e 800 lexicais, HNSW carregado e reranker em
  CUDA. Este numero descreve a aceitacao do repositorio RAG, nao uma execucao
  neste ambiente; em 2026-09-17 o servico nao estava em execucao e o cliente
  reportou `unavailable`.
- Matriz adversarial do LLM: qualidade direcional 7/7 e seguranca 7/7.
- TCN multi-task avaliada em teste temporal reservado e exposta somente por um
  advisor `RESEARCH_ONLY`, sem capacidade de autorizar ordens.
- Paper position reconciliada a partir dos logs legados e fechada exatamente
  com os saldos observados.

## Propriedades De Seguranca Validadas

- Falha fechada para candle stale, ausente, futuro ou malformado.
- Falha fechada para worker sem heartbeat e clock fora da tolerancia.
- Nenhuma mutacao de capital sem transacao PostgreSQL unica.
- Concorrencia de BUY e reconciliacao sem gasto duplo ou auditoria duplicada.
- Rejeicao de valores nao finitos, saldo negativo e posicao divergente.
- Taxa, slippage, notional, deltas, custo medio e PnL persistidos por execucao.
- Baseline diario de equity e drawdown persistidos; BUY bloqueado ao atingir o
  limite diario, sem impedir SELL redutor de exposicao.
- Avaliacao futura rejeita candles distantes do horizonte e classifica a falta
  de dado como `data_gap`, sem fabricar maturacao.
- Red flag negativa nunca favorece BUY e so confirma SELL com dados frescos,
  MACD bearish, RSI nao oversold e ausencia de instrucao hostil.
- RAG fora do caminho de aprovacao de trades e filtrado contra corpus estranho,
  paths operacionais, segredos e prompt injection.
- TUI e Electron executam apenas comandos presentes na allowlist do backend.
- Timeout do reranker externo degrada para busca hibrida sem reranking, com
  motivo e modo registrados, sem entrar no caminho de aprovacao de trades.
- Checkpoints TCN usam schema versionado, carga `weights_only=True`,
  calibracao validada e falha fechada quando faltam evidencias de teste.

## Estado Vivo Na Aceitacao

- Capital paper preservado durante os red teams.
- Posicao BTC/BRL possui custo medio reconstruido e proveniencia dos logs.
- Ultimo ciclo validado terminou em HOLD sem alterar carteira.
- Readiness final retornou `PASS_WITH_WARNINGS`: dados e workers estavam
  frescos; os avisos restantes pertencem ao historico auditado de falhas LLM e
  stale data.

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
- A TCN nao demonstrou edge executavel. Balanced accuracy foi 51,90%/54,32%
  em 15m/60m, a precisao de BUY ficou abaixo de 46% e a regressao de retorno
  perdeu para o baseline zero. Ela permanece somente como evidencia de
  pesquisa.

Limitacoes adicionadas na revalidacao de 2026-09-17, sem alterar o veredito:

- O custo configurado por lado (`PAPER_FEE_RATE=0.003` mais slippage minimo)
  continua sendo premissa de configuracao, nao medicao da taxa real da conta.
- Os 3 achados do Risk Manager reportados por auditoria e reproduzidos em
  2026-09-17 foram **corrigidos** no mesmo dia, com sign-off explicito do
  operador: limite de confianca hibrida passou a ser exclusivo (0.50 exato nao
  aprova mais), `technical_context` sem `rsi`/`macd` ou com ATR ausente passou a
  bloquear a acao direcional, e payload malformado passou a retornar HOLD em vez
  de levantar excecao. Nenhum threshold foi afrouxado; as tres correcoes deixam
  o gate mais restritivo. Provas de reproducao pre-fix e as edicoes exatas estao
  em `PROJECT_CLOSURE_2026-09-17.md`.
- A particao `validation` **ja foi usada** pela campanha multiagente de
  2026-08-10. Apenas o `holdout` permanece selado e nunca avaliado.

## Comandos De Verificacao

O `python` global nao tem as dependencias do projeto. Use o venv.

```powershell
& ".\.venv\Scripts\python.exe" -m pytest backend\tests -q
& ".\.venv\Scripts\python.exe" -m compileall -q backend
& ".\.venv\Scripts\python.exe" .\backend\tests\trading_readiness_report.py
& ".\.venv\Scripts\python.exe" .\backend\tests\dashboard_state.py
& ".\.venv\Scripts\python.exe" .\backend\tests\query_external_rag.py --health
Set-Location .\desktop
npm test
npm run build
$env:ELECTRON_DISABLE_SANDBOX="1"; npm run test:electron
```

Um resultado `BLOCKED` no readiness por dados stale e um resultado seguro. O
pipeline somente fica pronto quando dados, workers e clock estiverem dentro das
tolerancias configuradas.

O `npm run test:electron` executa o build antes do smoke; confira o exit code, e
nao apenas a saida impressa.
