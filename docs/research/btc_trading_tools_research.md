# Pesquisa para ferramentas deterministicas do Decision Agent

## Objetivo

Este documento traduz literatura de trading e praticas publicas de gestao de risco em calculos verificaveis. Nao copia calls de traders e nao promete prever o BTC. O modelo escolhe quais fatos adicionais deseja consultar; o backend valida o contrato, calcula os dados e devolve um resultado compacto. O LLM nunca executa SQL, codigo, rede ou escrita livre em memoria.

## Evidencias usadas

### Momentum e trend following

A pesquisa de time-series momentum da AQR documenta persistencia do proprio retorno passado em varias classes de ativos. O estudo de Rohrbach, Suremann e Osterrieder revisita estrategias com misturas de medias moveis e inclui criptomoedas. Isso sustenta uma ferramenta multi-horizonte, mas nao a conclusao de que toda tendencia continuara.

- [AQR - Time Series Momentum](https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Original-Paper-Data)
- [Momentum and Trend Following Trading Strategies for Currencies Revisited](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2949379)

Implementacao: `multi_timeframe_trend` calcula retorno, spread entre EMAs e inclinacao em janelas fixas de 5, 15, 30, 60 ou 240 minutos. O resultado e BULLISH, BEARISH ou MIXED; nao contem uma acao.

### Rompimento de faixa

Corbet, Eraslan, Lucey e Sensoy avaliaram regras de media movel e trading-range breakout em BTC de alta frequencia. As regras podem produzir informacao, mas os resultados variam por regra e lado da operacao. As regras publicas associadas ao experimento Turtle popularizaram canais de 20/55 periodos e risco ajustado por volatilidade. O projeto usa apenas o componente mensuravel do canal.

- [Effectiveness of Technical Trading Rules in Cryptocurrency Markets](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID3454216_code2200102.pdf?abstractid=3454216&mirid=1)
- [Turtle Trading Rules](https://www.turtletrader.com/rules/)

Implementacao: `donchian_breakout` compara o fechamento atual ao maximo/minimo dos candles anteriores. O candle atual e excluido do canal para evitar uma regra circular.

### Drawdown e risco assimetrico

Analises institucionais recentes da Coinbase destacam que variancia convencional trata movimentos positivos e negativos igualmente, enquanto risco de queda exige medidas direcionais. A CFTC alerta para volatilidade, flash crashes, manipulacao e risco operacional em mercados de ativos virtuais.

- [Coinbase Institutional - Monthly Outlook May 2026](https://www.coinbase.com/institutional/research-insights/research/monthly-outlook/monthly-outlook-may-2026)
- [CFTC - Understand the Risks of Virtual Currency Trading](https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/understand_risks_of_virtual_currency.html)

Implementacao: `drawdown_profile` calcula drawdown corrente, maximo drawdown e semidesvio negativo. Somente eventos objetivos com queda de pelo menos 3% podem ser persistidos. O modelo nao escolhe o texto, severidade nem identificador do evento.

### Confirmacao e disciplina de risco

Peter Brandt descreve gestao agressiva de risco e administracao da operacao como pilares mais importantes que copiar a selecao de trades de outra pessoa. Isso reforca a separacao arquitetural: ferramentas e LLM oferecem evidencia e proposta; o Risk Manager continua responsavel por aprovacao e tamanho.

- [Peter Brandt - Risk Management](https://www.peterlbrandt.com/risk-management-trading/)
- [Peter Brandt - Four Key Pillars](https://www.peterlbrandt.com/knowledge-center/four-key-pillars-factor/)

Implementacao: `volume_confirmation` calcula z-score do volume e inclinacao de OBV. Volume confirma ou enfraquece uma tese; nunca autoriza uma ordem sozinho.

### Custos, frequencia e validade estatistica

Nakano e coautores avaliaram trading intraday de BTC com dados de 15 minutos e custos de execucao. A analise institucional da Coinbase sobre narrativas de tendencia tambem alerta para escolha oportunista de janelas e testes multiplos. Isso impede que um resultado visualmente bom em uma unica hora seja tratado como estrategia validada.

- [Application of Deep Learning to Algorithmic Trading](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3128726)
- [Coinbase Institutional - Gold-to-Bitcoin rotation analysis](https://www.coinbase.com/institutional/research-insights/research/weekly-market-commentary/weekly-2026-01-30)

Consequencia: o benchmark registra horizontes futuros, mas o proximo gate deve incluir fees, slippage, turnover, posicoes completas, janelas nao sobrepostas e periodo out-of-sample intocado.

## Ideias deliberadamente nao implementadas

- **Copiar calls de traders famosos:** o proprio Peter Brandt recomenda desenvolver e testar um metodo proprio em vez de seguir outro operador. Uma call nao e uma regra reproduzivel.
- **Suporte/resistencia desenhado pelo LLM:** pivots dependem fortemente de parametros e facilitam explicacao retrospectiva. Donchian foi preferido por ter contrato objetivo.
- **Memoria livre como “BTC esta sangrando”:** texto livre pode persistir erro, prompt injection ou uma observacao vencida. Apenas eventos derivados de metricas podem ser armazenados.
- **Sentimento de rede social:** o projeto ainda nao tem uma fonte com proveniencia, cobertura e timestamp confiaveis.
- **Funding, liquidacoes, order book e on-chain:** podem agregar valor, mas nao existem no banco atual. O LLM nao deve inventar esses campos; novas ferramentas exigem primeiro gateways somente leitura e testes de qualidade.
- **Stop ou sizing escolhidos pelo modelo:** ficam fora das ferramentas porque pertencem ao Risk Manager e ao simulador de execucao.

## Controles de seguranca

1. Apenas quatro nomes de ferramenta existem no schema Pydantic.
2. Janelas sao enumeradas e o plano aceita no maximo tres chamadas sem repeticao.
3. A consulta le no maximo 1.500 candles e sempre aplica `timestamp <= as_of_timestamp`.
4. Ferramentas nao recebem strings de SQL, URLs, paths, codigo ou payload livre.
5. Falha de dados, calculo ou auditoria vira `ERROR`/`INSUFFICIENT_DATA`; nunca vira sinal direcional.
6. Resultados nao possuem campo de acao e nao chamam o simulador.
7. Memoria de drawdown e estruturada, deduplicada e desativada em backtests.
8. O caminho e opt-in por `LLM_TOOLS_ENABLED`; a ativacao nao remove nenhuma regra do Risk Manager.

## Como avaliar

O benchmark historico compara modelos e prompts nos mesmos timestamps e com o mesmo plano de ferramentas. Ele registra decisao do LLM, veredito do Risk Manager e movimento futuro em 15/60 minutos. HOLD nao e tratado como verdade absoluta: pode ser `GOOD`, `MISSED_UPSIDE` ou `AVOIDED_DOWNSIDE`.

Os resultados precisam ser avaliados em varias janelas independentes, incluindo custos, slippage, exposicao e ciclo completo de posicao antes de qualquer discussao sobre dinheiro real.
