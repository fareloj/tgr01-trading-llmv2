# TGR-01 Trading LLM V2 - Super Resumo do Projeto

Data deste resumo: 2026-05-22

## 1. Objetivo do Projeto

O TGR-01 Trading LLM V2 e um bot de trading experimental para BTC/cripto, rodando localmente em Python no Windows 11, com foco em seguranca, auditoria e comportamento defensivo.

O objetivo nao e criar um "bot magico de IA". O objetivo e construir um sistema em que:

- a IA interpreta contexto;
- Python calcula indicadores e matematica;
- o Risk Manager toma a decisao final;
- o executor apenas obedece;
- HOLD e o estado seguro padrao;
- qualquer erro, duvida, dado stale ou inconsistencia vira HOLD ou abort antes do LLM;
- tudo fica auditado em SQLite.

O projeto esta em modo paper trading. Nao ha execucao real de ordens ainda.

## 2. Filosofia de Arquitetura

Princípios centrais:

- LLM nunca calcula indicadores.
- LLM nunca calcula sizing.
- LLM nunca tem permissao direta para enviar ordem.
- Kelly e gestao de risco sao determinísticos.
- Se houver dados insuficientes, o ciclo aborta antes do LLM.
- Se o LLM falhar, o sistema registra falha tecnica e retorna HOLD.
- Se o Risk Manager discordar do LLM, o Risk Manager vence.
- O executor nao interpreta mercado; ele apenas executa a decisao aprovada.

Essa arquitetura foi pensada para evitar os problemas tipicos de bots de IA:

- prompt mandando comprar sem base;
- LLM inventando calculo;
- decisao sem auditoria;
- execucao real acoplada ao texto da IA;
- overtrading;
- falta de rastreabilidade.

## 3. Stack Atual

Stack usada:

- Python
- SQLite
- Pandas
- Pydantic
- Groq API
- OpenAI-compatible clients
- Mercado Bitcoin em modo read-only
- RSS de noticias cripto
- Scripts `.bat` para operacao no Windows

Providers/modelos considerados:

- Groq `llama-3.3-70b-versatile`
- Groq `openai/gpt-oss-120b`
- OpenRouter como candidato futuro
- DeepSeek como candidato futuro
- Gemini/Gemma como candidatos para revisao ou tarefas auxiliares

## 4. Fluxo Principal do Sistema

Fluxo ideal:

1. `price_worker` coleta preco/candles reais do Mercado Bitcoin.
2. `news_worker` coleta noticias reais via RSS.
3. SQLite central armazena dados.
4. `payload_builder` monta contexto tecnico e qualitativo.
5. Python calcula RSI/MACD/ATR.
6. O LLM recebe payload mastigado e retorna JSON estrito.
7. Pydantic valida o JSON.
8. Risk Manager aplica regras deterministicas.
9. Executor simula operacao em paper trading.
10. Tudo e registrado em `trade_logs`.
11. Relatorios analisam decisoes e resultados futuros.

## 5. Estado Atual do Projeto

O projeto esta funcional em paper trading e passou por varios ciclos de hardening.

Estado atual:

- Banco SQLite central funcionando.
- Workers reais funcionando.
- Preflight de data funcionando.
- Payload tecnico funcionando.
- Decision Agent funcionando com Groq.
- Risk Manager com gates direcionais.
- Cooldown anti-overtrading.
- Paper trading auditado.
- Relatorio deterministico de decisoes.
- Comparador de modelos LLM.
- Prompt hardened aplicado para evitar BUY por RSI oversold isolado.

Ainda nao implementado:

- RAG.
- Banco vetorial.
- Dashboard.
- Execucao real.
- Multiagente em producao.
- Backtesting historico robusto.
- Multi-timeframe real.
- Taxas/slippage completas no paper trading.

## 6. Estrutura Conceitual dos Modulos

### 6.1 Workers

`price_worker`

- coleta dados reais de preco;
- grava candles em SQLite;
- registra heartbeat;
- imprime DB path;
- nao tem write-access em exchange;
- escreve apenas no SQLite local.

`news_worker`

- coleta noticias de fontes RSS;
- deduplica entradas;
- grava noticias no SQLite;
- registra heartbeat;
- suporta modo real e modo mock, mas foco atual e real.

### 6.2 Banco SQLite

Banco oficial:

```text
D:\Projetos\tgr01-trading-llmv2\backend\trading_v2.db
```

Tabelas principais:

- `klines`
- `news`
- `system_health`
- `trade_logs`
- `virtual_portfolio`

Papel do banco:

- ser a fonte central auditavel;
- permitir diagnostico de dados;
- guardar historico de decisoes;
- permitir analise posterior;
- evitar dependencia de estado em memoria.

### 6.3 Payload Builder

Responsavel por montar o payload do LLM.

Inclui:

- `technical_context`
- `news_context`
- `data_health`
- `news_risk`
- `portfolio_context`

O payload nao entrega candles brutos sem tratamento. Ele entrega estados qualitativos calculados por Python.

Exemplo de estados:

- RSI `NEUTRAL`, `OVERSOLD`, `OVERBOUGHT`
- MACD `BULLISH_EXPANDING`, `BEARISH_EXPANDING`, `NEUTRAL`
- market data stale true/false
- news stale true/false
- news risk normal/elevated/high

### 6.4 Decision Agent

Responsavel por ler o payload e sugerir:

- `BUY`
- `SELL`
- `HOLD`

Contrato:

- resposta precisa ser JSON;
- validada por Pydantic;
- sem calculo de indicador;
- sem calculo de sizing;
- reasoning curto e objetivo;
- se contexto estiver confuso, HOLD.

O prompt foi endurecido para impedir um erro observado:

```text
RSI OVERSOLD sozinho NAO autoriza BUY.
Se MACD estiver BEARISH_EXPANDING ou BEARISH_DIVERGENCE, prefira HOLD.
```

### 6.5 Risk Manager

Parte mais importante da arquitetura.

Responsabilidades:

- validar se a direcao do LLM faz sentido;
- bloquear BUY/SELL incompatíveis com contexto tecnico;
- aplicar cooldown;
- aplicar confianca hibrida;
- aplicar Kelly;
- limitar exposicao;
- transformar qualquer incerteza em HOLD.

O Risk Manager e deterministico.

### 6.6 Executor

Executor atual e paper trading.

Responsabilidades:

- simular compra/venda;
- atualizar carteira virtual;
- registrar execucao;
- nunca decidir mercado.

Execucao real ainda nao foi implementada na V2.

## 7. Problema Inicial Resolvido: Dados Insuficientes no SQLite

Problema inicial:

```text
Dados insuficientes no SQLite
```

Causa investigada:

- payload/indicadores exigiam minimo de candles;
- possivel DB path divergente;
- workers poderiam nao estar populando;
- seed historico poderia estar ausente;
- schema antigo nao tinha `trade_logs`.

Correcoes feitas:

- DB path centralizado em `core/database.py`;
- `DB_PATH.resolve()` exposto;
- diagnostico de banco criado;
- contagem de candles por asset/timeframe;
- range de timestamps;
- health de workers;
- tabelas ausentes detectadas;
- `init_db()` idempotente;
- `trade_logs` criado sem apagar dados;
- payload passou a retornar erro tecnico enriquecido:
  - asset;
  - timeframe;
  - required_klines;
  - found_klines;
  - db_path.

Resultado:

- erro deixou de ser generico;
- preflight passou a explicar exatamente o problema;
- orchestrator aborta antes do LLM quando dados sao insuficientes.

## 8. Bootstrap e Operacao por `.bat`

Foi criado um `.bat` para simplificar operacao no Windows.

Funcoes do menu:

- diagnostico SQLite;
- iniciar workers reais;
- preflight de data para teste;
- teste curto de paper trading;
- preflight estrito para pipeline real;
- paper trading mais longo;
- analisar `trade_logs`;
- gerar relatorio operacional;
- avaliar decisoes por movimento futuro;
- revisar relatorio com LLM;
- ver processos.

Tambem foi criado um fluxo para 100 ciclos:

```text
run_100_eval.bat
```

Objetivo:

- rodar 100 ciclos;
- gerar relatorio limpo;
- gerar avaliacao deterministica;
- gerar revisao LLM quando possivel.

## 9. Preflight de Data

Um dos hardenings mais importantes foi o preflight de data.

Ele verifica:

- se o ultimo candle e do dia atual;
- se o candle esta fresco;
- se a noticia mais recente e do dia atual;
- se workers obrigatorios estao vivos;
- se o DB path e o correto.

Exemplo de bloqueio correto:

```text
Ultimo candle nao e de hoje: 2026-05-11 != 2026-05-19.
Nao rode pipeline real com dados fora do dia ou stale.
```

Esse bloqueio foi importante porque os workers tinham ficado parados desde 2026-05-11, e o sistema impediu rodar paper trading com dados velhos em 2026-05-19.

## 10. Data Health

O payload agora possui `data_health`.

Campos principais:

- `latest_kline_timestamp`
- `kline_age_seconds`
- `is_market_data_stale`
- `market_data_stale_threshold_seconds`
- `latest_news_timestamp`
- `news_age_seconds`
- `is_news_stale`
- `news_stale_threshold_seconds`

Uso:

- abort antes do LLM se market data estiver stale;
- penalizar confiabilidade se noticia estiver stale;
- auditar idade dos dados;
- evitar decisao com dados antigos.

## 11. News Risk

Foi criado um detector simples de red flags em noticias.

Termos monitorados:

- hack
- crash
- ban
- panic
- liquidation
- regulador
- proibicao
- queda
- suspende
- etc.

Estados:

- `NORMAL`
- `ELEVATED`
- `HIGH`

Uso:

- bloquear BUY se houver red flag negativa;
- penalizar confiabilidade;
- dar contexto ao LLM sem depender de RAG.

## 12. Directional Gate

O Directional Gate foi implementado porque o LLM pode estar confiante na direcao errada.

Regra para BUY:

Bloquear BUY se:

- market data stale;
- news stale;
- news red flag;
- RSI overbought;
- MACD bearish expanding;
- MACD bearish divergence;
- ATR extreme, quando existir.

Regra para SELL:

Bloquear SELL se:

- market data stale;
- RSI oversold;
- MACD bullish expanding;
- MACD bullish divergence.

HOLD sempre permitido.

Esse gate foi essencial no teste em que o LLM sugeriu varios BUY por RSI oversold enquanto MACD estava bearish.

## 13. Cooldown Anti-Overtrading

Foi implementado cooldown para nao repetir BUY/SELL em janela curta.

Exemplo:

```text
Cooldown: BUY repetido nos ultimos 15 minutos
```

Objetivo:

- evitar overtrading;
- impedir compras repetidas em sinais parecidos;
- reduzir ruido de ciclos curtos.

## 14. Kelly Criterion

Kelly continua deterministico.

O LLM nao calcula:

- tamanho de posicao;
- risco por trade;
- Kelly;
- capital alocado.

O Risk Manager calcula o sizing e aplica teto.

Estado atual:

- tamanho maximo por trade no paper: 5%;
- Kelly usado de forma conservadora;
- nao ha Kelly agressivo;
- ainda falta calibrar com estatistica real de paper trading.

## 15. Auditoria em `trade_logs`

Cada ciclo registra:

- timestamp;
- acao do LLM;
- reasoning do LLM;
- acao final;
- conviccao do LLM;
- system reliability;
- final confidence;
- executed size;
- execution price;
- reasoning final do Risk Manager.

Isso permite analisar:

- LLM sugeriu BUY e Risk bloqueou;
- LLM sugeriu HOLD;
- LLM falhou tecnicamente;
- ciclo abortou antes do LLM;
- trade foi aprovado;
- cooldown bloqueou;
- stale data bloqueou.

Ainda falta:

- gravar snapshot compacto do payload em cada decisao.

Esse e um dos proximos passos mais importantes.

## 16. Melhorias no Reasoning

Problemas antigos:

- `Noticias confusas`
- `LLM sugeriu acao neutra ou invalida`

Essas mensagens eram ruins porque escondiam causa real.

Melhorias feitas:

- diferenciar HOLD legitimo de acao invalida;
- diferenciar falha tecnica de HOLD;
- diferenciar stale data de erro LLM;
- substituir HOLD generico por motivo mais objetivo quando possivel;
- registrar `llm_reasoning` nos relatorios.

Ainda ha historico antigo com mensagens ruins, mas os logs novos estao melhores.

## 17. Testes de 20/100 Ciclos

Foram criados scripts de teste:

- `run_20_cycles.py`
- `run_paper_trading.py`
- `run_100_eval.bat`

Objetivo:

- rodar ciclos controlados;
- nao tocar em banco real quando usando smoke test;
- gerar relatorio de decisoes;
- avaliar comportamento em dados reais.

Testes passaram:

```text
python -m pytest .\backend\tests -q
26 passed
```

## 18. Relatorio Deterministico de Decisoes

Foi criado:

```text
evaluate_decisions.py
```

Ele avalia decisoes em horizontes futuros:

- 5m
- 15m
- 30m
- 60m

Classificacoes:

- `good`
- `bad`
- `neutral`
- `missed_upside`
- `avoided_downside`
- `not_matured`
- `not_applicable`

Objetivo:

- nao depender de opiniao subjetiva;
- avaliar se HOLD evitou queda;
- avaliar se BUY foi ruim;
- avaliar se Risk bloqueou oportunidade boa ou ruim.

Importante:

Essa metrica nao e verdade absoluta. Ela e ferramenta para discussao.

## 19. Revisao LLM do Relatorio

Foi criado:

```text
llm_review_decisions.py
```

Objetivo:

- usar outro LLM para revisar o relatorio deterministico;
- apontar possiveis erros do LLM decisor;
- apontar possiveis melhorias do Risk Manager;
- sugerir proximos testes.

Problema encontrado:

- mandar JSON completo para Groq estourou rate limit diario.

Correcoes:

- compactar relatorio antes da chamada;
- limitar `max_tokens`;
- gerar fallback offline se a API falhar;
- nao perder o relatorio deterministico quando a revisao LLM falha.

## 20. Comparador de Modelos

Foi criado:

```text
compare_llm_models.py
```

Objetivo:

- testar modelos sem executar trade;
- comparar respostas no mesmo payload;
- validar JSON com Pydantic;
- passar resposta pelo Risk Manager;
- gerar Markdown e JSON.

Cenarios testados:

1. payload real atual;
2. `RSI OVERSOLD + MACD BEARISH_EXPANDING`;
3. `RSI OVERSOLD + MACD BULLISH_EXPANDING`.

Modelos testados:

- Groq `llama-3.3-70b-versatile`
- Groq `openai/gpt-oss-120b`

Resultado:

- `llama-3.3` com prompt antigo comprava `RSI oversold` mesmo com MACD bearish.
- prompt hardened corrigiu isso.
- `gpt-oss-120b` entende melhor conflito tecnico, mas e mais conservador.
- `gpt-oss-120b` precisa de `max_completion_tokens` maior para funcionar bem no Groq.

## 21. GPT-OSS 120B via Groq

O `openai/gpt-oss-120b` foi testado via Groq.

Problemas:

- JSON mode falhava com budget pequeno;
- modelo reasoning gastava tokens antes de emitir JSON;
- playground precisava de budget alto.

Correcoes no comparador:

- para GPT-OSS via Groq:

```text
max_completion_tokens=6911
reasoning_effort=low
temperature=0
```

Configuravel via `.env`:

```text
GPT_OSS_MAX_COMPLETION_TOKENS=6911
GPT_OSS_REASONING_EFFORT=low
```

Resultado:

- GPT-OSS passou a retornar JSON valido;
- no caso bearish, preferiu HOLD;
- no caso bullish, sugeriu BUY com conviccao menor, e o Risk bloqueou se abaixo de 70%.

Leitura:

- bom candidato para Decision Agent futuro;
- bom candidato para Review Agent;
- mas nao foi adotado no pipeline principal ainda.

## 22. Prompt Hardened

O prompt hardened foi aplicado ao `DecisionAgent`.

Mudanca principal:

```text
RSI OVERSOLD sozinho NAO autoriza BUY.
Se MACD estiver BEARISH_EXPANDING ou BEARISH_DIVERGENCE, prefira HOLD.
```

Resultado apos teste curto:

- o LLM parou de transformar RSI oversold em BUY automatico;
- novos logs mostraram:
  - `RSI OVERSOLD, MACD BEARISH_EXPANDING -> HOLD`
  - `RSI OVERSOLD, MACD NEUTRAL -> HOLD`
  - sem BUY novo por oversold isolado.

Essa foi uma melhoria importante.

## 23. Resultado de Paper Trading Relevante

Antes do prompt hardened:

- LLM sugeriu BUY varias vezes por `RSI oversold`;
- Risk bloqueou 17 BUYs;
- 2 BUYs foram aprovados;
- um dos BUYs aprovados performou mal em horizontes curtos.

Depois do prompt hardened:

- nos logs recentes, o LLM passou a responder HOLD quando RSI estava oversold mas MACD nao confirmava;
- comportamento ficou mais alinhado com a arquitetura.

Conclusao:

- nao era necessario adicionar RAG para corrigir esse problema;
- o problema era prompt + regra direcional;
- Risk Manager ja estava segurando, mas agora o LLM tambem ficou menos ruidoso.

## 24. Situacao do Banco e Workers

Houve um caso importante:

- em 2026-05-19, o banco ainda tinha ultimo candle de 2026-05-11;
- preflight bloqueou corretamente;
- workers estavam parados desde 2026-05-11;
- apos reiniciar workers, preflight passou.

Preflight saudavel exemplo:

```text
KLINE local=2026-05-19 20:48:00 age=59s
price_worker heartbeat_age=2s
news_worker heartbeat_age=1s
Preflight aprovado
```

Isso provou que o sistema nao aceita rodar com dado antigo.

## 25. Fases do Projeto

### Fase 0 - V1 e aprendizado

V1 existiu como prototipo inicial.

Caracteristicas da V1:

- mais ambiciosa;
- usava muito prompt;
- tinha ideias de RAG, GUI, fine-tuning, LM Studio;
- tinha live trading gate;
- parser por regex;
- LLM tinha mais responsabilidade do que deveria;
- sizing/Kelly apareciam muito perto do prompt.

Licao da V1:

- a ideia era boa;
- a arquitetura era arriscada;
- a V2 nasceu para separar responsabilidades e reduzir magia.

### Fase 1 - Base defensiva da V2

Objetivo:

- criar arquitetura simples, local e auditavel.

Feito:

- Python local;
- SQLite central;
- workers desacoplados;
- payload builder;
- Pydantic;
- Risk Manager;
- paper trading.

### Fase 2 - Debug de ingestao e DB

Objetivo:

- resolver `Dados insuficientes no SQLite`.

Feito:

- DB path centralizado;
- diagnostico de banco;
- seed historico;
- schema idempotente;
- preflight SQLite;
- erro tecnico detalhado.

### Fase 3 - Worker hardening

Objetivo:

- garantir que dados sao reais, frescos e rastreaveis.

Feito:

- heartbeats;
- logs;
- DB path nos workers;
- excecoes visiveis;
- preflight de data;
- bloqueio de dado antigo.

### Fase 4 - Risk Manager hardening

Objetivo:

- impedir LLM de comprar/vender contra contexto tecnico.

Feito:

- Directional Gate;
- cooldown;
- confidence threshold;
- system reliability;
- Kelly deterministico;
- motivos auditaveis.

### Fase 5 - Paper trading e auditoria

Objetivo:

- rodar ciclos reais sem dinheiro real;
- auditar tudo;
- avaliar decisoes.

Feito:

- paper trading;
- carteira virtual;
- `trade_logs`;
- relatorios;
- evaluate decisions por horizonte.

### Fase 6 - Comparacao de modelos

Objetivo:

- testar se outro LLM segue melhor as regras.

Feito:

- comparador de modelos;
- Groq Llama;
- Groq GPT-OSS;
- prompt current vs hardened;
- analise de comportamento em cenarios sinteticos.

### Fase 7 - Estudos futuros e refinamento

Objetivo:

- transformar estudo de cripto em features e gates.

Feito:

- arquivo de estudo para NotebookLM:

```text
crypto_study_plan_for_tgr01.md
```

Ainda falta:

- transformar topicos em features reais;
- adicionar volume;
- adicionar regime de mercado;
- adicionar multi-timeframe;
- adicionar taxa/slippage.

### Fase 8 - RAG e memoria

Status:

- ainda nao implementado.

Uso planejado:

- memoria de trades;
- relatorios anteriores;
- eventos historicos;
- notas de estudo;
- revisao qualitativa.

Nao usar RAG para:

- calcular indicador;
- liberar ordem;
- substituir Risk Manager.

### Fase 9 - Execucao real controlada

Status:

- ainda nao implementado.

Premissas futuras:

- `REAL_TRADING_ENABLED=false` por padrao;
- limite pequeno por ordem;
- limite diario;
- confirmacao manual;
- executor real separado;
- validação de saldo, spread, taxa, stale data;
- nenhuma ordem real sem dupla confirmacao.

## 26. O Que Fizemos Fora do Plano Inicial

Algumas coisas surgiram durante debug e foram adicionadas por necessidade:

- `.bat` operacional completo;
- preflight de data;
- filtro de noticia futura;
- limpeza de noticias mockadas;
- revisor LLM;
- comparador de modelos;
- fallback offline de revisao;
- estudo para NotebookLM;
- suporte experimental para GPT-OSS no comparador;
- prompt hardened contra RSI oversold.

Essas mudancas nao poluiram a arquitetura porque todas ficaram alinhadas com:

- auditoria;
- safety;
- testabilidade;
- decisao deterministica.

## 27. O Que Ainda Falta Fazer

Prioridade alta:

- gravar snapshot compacto do payload em `trade_logs`;
- rodar novo `run_100_eval.bat` com prompt hardened;
- comparar resultados antes/depois;
- melhorar relatorio focado em BUY aprovado/bloqueado;
- investigar `system_reliability=0.7` em alguns ciclos recentes;
- separar resultados antigos dos novos com `since-id`.

Prioridade media:

- taxa por operacao no paper trading;
- slippage estimado;
- spread check;
- volume status;
- ATR status explicito;
- market regime simples;
- melhor classificacao de noticias.

Prioridade futura:

- multi-timeframe 5m/15m;
- backtesting historico;
- walk-forward;
- RAG;
- banco vetorial;
- Review Agent mais robusto;
- modo semi-auto;
- executor real com limite minimo.

## 28. RAG, Banco Vetorial e Kelly

Pergunta recorrente:

```text
Para funcionar ainda precisa de RAG, banco vetorial, Kelly?
```

Resposta:

- RAG: nao precisa para funcionar agora.
- Banco vetorial: nao precisa para o pipeline atual.
- Kelly: ja existe de forma deterministica.

RAG e banco vetorial podem ajudar no futuro, mas nao sao requisito para o bot funcionar.

O bot precisa primeiro de:

- dados frescos;
- indicadores corretos;
- Risk Manager robusto;
- auditoria;
- paper trading consistente;
- relatorios confiaveis.

## 29. Riscos Conhecidos

Riscos ainda existentes:

- o modelo pode continuar dando reasoning pobre;
- historico antigo polui estatisticas globais;
- ainda nao ha snapshot completo do payload por trade;
- paper trading ainda nao considera taxas/slippage reais;
- noticias podem estar velhas mas ainda dentro do limite;
- modelos diferentes podem ter comportamento inconsistente;
- teste curto nao prova edge estatistica;
- 1m tem muito ruido.

Mitigacoes atuais:

- HOLD default;
- preflight;
- Directional Gate;
- cooldown;
- Pydantic;
- Risk Manager;
- trade_logs;
- evaluate decisions;
- paper trading antes de qualquer real.

## 30. Proxima Acao Recomendada

Passo mais pragmatico agora:

1. Rodar `run_100_eval.bat` com workers vivos e prompt hardened.
2. Usar `since-id` para isolar somente logs novos.
3. Comparar:
   - BUY sugeridos;
   - BUY bloqueados;
   - BUY aprovados;
   - resultados 5m/15m/30m/60m;
   - motivos de HOLD.
4. Implementar snapshot compacto do payload em `trade_logs`.
5. Corrigir paper trading para incluir taxa/slippage.

Nao fazer ainda:

- dashboard;
- execucao real;
- RAG;
- multiagente automatico;
- troca definitiva de modelo sem batch comparativo.

## 31. Resumo Executivo

O TGR-01 V2 saiu de um estado de debug de banco e ingestao para um sistema de paper trading defensivo e auditavel.

O principal ganho ate agora foi transformar o projeto em uma arquitetura onde a IA nao manda sozinha.

O sistema ja:

- coleta dados reais;
- valida frescor dos dados;
- calcula indicadores em Python;
- monta payload qualitativo;
- consulta LLM com JSON validado;
- bloqueia decisoes ruins com Risk Manager;
- simula trade;
- audita tudo;
- gera relatorios.

O bug mais importante encontrado recentemente foi:

```text
LLM comprava por RSI oversold mesmo com MACD bearish.
```

Foi mitigado por:

- Directional Gate;
- prompt hardened.

O projeto ainda nao esta pronto para trading real, mas esta bem mais proximo de uma base seria do que de um prototipo solto.

O proximo salto de qualidade vira de:

- auditoria mais rica;
- taxas/slippage;
- volume/regime;
- multi-timeframe;
- mais testes longos;
- estudo de cripto transformado em regras deterministicas.

