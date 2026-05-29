# Plano de Estudo de Cripto para Refinar o TGR-01 V2

Objetivo: estudar cripto de forma pragmatica para melhorar o bot sem transformar o sistema em uma caixa preta. O conhecimento deve virar features deterministicas, gates de risco, relatorios e testes.

Regra central do projeto:

- LLM interpreta contexto.
- Python calcula matematica.
- Risk Manager decide.
- Executor obedece.
- Em duvida: HOLD.

## 1. Microestrutura Basica do Mercado

O que estudar:

- Livro de ofertas.
- Spread.
- Slippage.
- Ordem market vs limit.
- Liquidez real em corretoras.
- Diferença entre preco teorico e preco executado.

Como usar no TGR-01:

- Calcular spread antes de permitir ordem real.
- Bloquear trade se spread estiver alto.
- Estimar slippage no paper trading.
- Registrar `expected_price`, `execution_price`, `slippage_pct`.
- Adicionar limite: nao executar se slippage estimado > X%.

Uso no Risk Manager:

- Gate: `spread_too_wide -> HOLD`.
- Gate: `low_liquidity -> HOLD`.

## 2. Taxas e Custo Real de Operacao

O que estudar:

- Taxa taker/maker.
- Custo total de entrada e saida.
- Breakeven minimo.
- Impacto de taxas em trades pequenos.

Como usar no TGR-01:

- Corrigir paper trading para descontar taxa por operacao.
- Medir se o alvo esperado supera taxa + slippage.
- Criar relatorio de PnL liquido, nao apenas preco.

Uso no Risk Manager:

- Gate: se movimento esperado < custo total, HOLD.
- Sizing: reduzir posicao quando custo relativo for alto.

## 3. Estrutura de Mercado

O que estudar:

- Tendencia de alta, baixa e lateralizacao.
- Topos e fundos ascendentes/descendentes.
- Rompimento e falso rompimento.
- Pullback.
- Range.

Como usar no TGR-01:

- Criar feature `market_structure_status`:
  - `UPTREND`
  - `DOWNTREND`
  - `RANGE`
  - `BREAKOUT`
  - `FALSE_BREAKOUT_RISK`
- Usar candles anteriores para detectar direcao.

Uso no Risk Manager:

- BUY mais permissivo em `UPTREND`.
- BUY mais restrito em `DOWNTREND`.
- SELL ou HOLD em falso rompimento.
- HOLD em range sem vantagem clara.

## 4. RSI do Jeito Certo

O que estudar:

- RSI oversold nao significa compra automatica.
- RSI overbought nao significa venda automatica.
- RSI em tendencia forte pode ficar extremo por muito tempo.
- Divergencia de RSI.

Como usar no TGR-01:

- Manter RSI como contexto, nao gatilho unico.
- Combinar RSI com MACD, estrutura e volume.
- Auditar quando LLM tenta comprar apenas por RSI.

Uso no Risk Manager:

- Gate ja existente: bloquear BUY em overbought.
- Novo refinamento: BUY em oversold exige confirmacao de reversao.
- Exemplo: `RSI_OVERSOLD + MACD_BEARISH -> HOLD`.

## 5. MACD e Momentum

O que estudar:

- Cruzamento MACD.
- Histograma expandindo ou contraindo.
- Momentum bullish/bearish.
- Divergencia.

Como usar no TGR-01:

- Refinar estados atuais:
  - `BULLISH_EXPANDING`
  - `BULLISH_WEAKENING`
  - `BEARISH_EXPANDING`
  - `BEARISH_WEAKENING`
  - `NEUTRAL`
- Diferenciar queda acelerando de queda perdendo forca.

Uso no Risk Manager:

- Bloquear BUY se `BEARISH_EXPANDING`.
- Permitir observacao em `BEARISH_WEAKENING`, mas nao compra automatica.
- Comprar apenas se outros fatores confirmarem.

## 6. ATR e Volatilidade

O que estudar:

- ATR como medida de volatilidade.
- Volatilidade normal vs extrema.
- Stop baseado em volatilidade.
- Tamanho de posicao ajustado por volatilidade.

Como usar no TGR-01:

- Criar `atr_status`:
  - `LOW`
  - `NORMAL`
  - `HIGH`
  - `EXTREME`
- Ajustar sizing pelo ATR.
- Bloquear trades em volatilidade extrema.

Uso no Risk Manager:

- Gate: `ATR_EXTREME -> HOLD`.
- Sizing: volatilidade alta reduz exposicao.

## 7. Volume

O que estudar:

- Movimento com volume.
- Movimento sem volume.
- Climax de venda/compra.
- Volume em rompimento.

Como usar no TGR-01:

- Adicionar feature `volume_status`:
  - `LOW`
  - `NORMAL`
  - `HIGH`
  - `CLIMAX`
- Comparar volume atual com media dos ultimos N candles.

Uso no Risk Manager:

- Bloquear breakout sem volume.
- Dar mais peso a reversao com volume alto.
- Reduzir confianca em movimento sem volume.

## 8. Candles e Rejeicao

O que estudar:

- Pavio superior/inferior.
- Candle de reversao.
- Engolfo.
- Doji.
- Fechamento forte/fraco.

Como usar no TGR-01:

- Criar features simples:
  - `long_lower_wick`
  - `long_upper_wick`
  - `strong_close`
  - `weak_close`
  - `reversal_candle`

Uso no Risk Manager:

- BUY em oversold exige sinal de rejeicao ou candle de reversao.
- SELL em overbought exige rejeicao no topo.

## 9. Liquidez e Stop Hunting

O que estudar:

- Regioes onde stops ficam acumulados.
- Varredura de liquidez.
- Falso rompimento.
- Pavio buscando liquidez.

Como usar no TGR-01:

- Detectar rompimento rapido seguido de retorno ao range.
- Criar estado `liquidity_sweep_risk`.

Uso no Risk Manager:

- Evitar BUY logo apos rompimento sem confirmacao.
- Preferir HOLD quando houver risco de fakeout.

## 10. Regimes de Mercado

O que estudar:

- Mercado em tendencia.
- Mercado lateral.
- Mercado de alta volatilidade.
- Mercado de baixa liquidez.

Como usar no TGR-01:

- Criar `market_regime`:
  - `TRENDING_UP`
  - `TRENDING_DOWN`
  - `RANGING`
  - `HIGH_VOLATILITY`
  - `LOW_LIQUIDITY`

Uso no Risk Manager:

- Estrategias diferentes por regime.
- Em `RANGING`, menos trades.
- Em `TRENDING_DOWN`, BUY muito restrito.

## 11. Timeframes

O que estudar:

- 1m, 5m, 15m, 1h.
- Confluencia entre timeframes.
- Ruido de timeframe curto.

Como usar no TGR-01:

- Manter 1m para execucao/observacao.
- Adicionar 5m/15m para contexto de direcao.
- Payload pode mostrar:
  - `short_term_status`
  - `medium_term_status`

Uso no Risk Manager:

- BUY em 1m so passa se 5m/15m nao estiverem claramente bearish.
- Evitar overtrading por ruido de 1m.

## 12. Noticias e Eventos

O que estudar:

- Hack de exchange.
- Regulacao.
- ETF.
- FOMC/CPI.
- Falas de governos.
- Falencia/liquidacao de empresas cripto.

Como usar no TGR-01:

- Melhorar `news_risk`.
- Classificar noticias por:
  - `REGULATORY_RISK`
  - `EXCHANGE_RISK`
  - `MACRO_RISK`
  - `ETF_FLOW`
  - `SECURITY_INCIDENT`

Uso no Risk Manager:

- Bloquear BUY em red flag forte.
- Reduzir confianca quando noticia importante estiver velha.
- Exigir noticia recente em eventos macro.

## 13. Dados Macroeconomicos

O que estudar:

- Juros dos EUA.
- DXY.
- Nasdaq/S&P 500.
- CPI.
- FOMC.
- Liquidez global.

Como usar no TGR-01:

- Adicionar contexto macro simples.
- Exemplo:
  - `risk_assets_status`
  - `dxy_status`
  - `macro_event_today`

Uso no Risk Manager:

- Reduzir BUY se cripto sobe contra macro muito ruim.
- HOLD antes de evento macro importante.

## 14. Correlacao BTC com Outros Ativos

O que estudar:

- BTC vs ETH.
- BTC vs Nasdaq.
- BTC vs DXY.
- Dominancia BTC.

Como usar no TGR-01:

- Criar feature `correlation_context`.
- Se BTC sobe sozinho com mercado de risco caindo, sinal pode ser menos confiavel.

Uso no Risk Manager:

- Penalizar sinais isolados sem confirmacao.
- Melhorar relatorios de decisao.

## 15. Derivativos: Funding e Open Interest

O que estudar:

- Funding rate.
- Open interest.
- Long squeeze.
- Short squeeze.
- Liquidation heatmap.

Como usar no TGR-01:

- Ainda nao implementar agora se o bot opera spot.
- Futuramente adicionar como contexto de risco.

Uso no Risk Manager:

- Evitar comprar quando mercado esta alavancado demais em longs.
- Detectar risco de squeeze.

## 16. Gestao de Posicao

O que estudar:

- Entrada parcial.
- Saida parcial.
- Stop.
- Take profit.
- Trailing stop.
- Rebalanceamento.

Como usar no TGR-01:

- Hoje o bot compra 5% via paper.
- Futuro: permitir reduce/exit no paper.
- Criar logica de gestao da posicao aberta.

Uso no Risk Manager:

- Nao apenas decidir entrada.
- Decidir quando reduzir, segurar ou sair.

## 17. Drawdown e Risco Diario

O que estudar:

- Drawdown maximo.
- Perda diaria.
- Sequencia de perdas.
- Risco de ruina.

Como usar no TGR-01:

- Criar daily risk ledger.
- Pausar trades apos perda diaria.
- Reduzir sizing apos sequencia ruim.

Uso no Risk Manager:

- Gate: `daily_loss_limit_hit -> HOLD`.
- Gate: `loss_streak -> reduce_size_or_hold`.

## 18. Kelly Criterion

O que estudar:

- Kelly completo.
- Kelly fracionado.
- Por que Kelly depende de win rate e risk/reward confiaveis.
- Risco de superestimar vantagem.

Como usar no TGR-01:

- Manter Kelly deterministico.
- Nunca deixar LLM calcular sizing.
- Ajustar Kelly com dados reais de paper trading.

Uso no Risk Manager:

- Sizing maximo.
- Reducao por volatilidade/drawdown.
- Nao usar Kelly agressivo antes de estatistica suficiente.

## 19. Backtesting e Walk-Forward

O que estudar:

- Backtest simples.
- Lookahead bias.
- Overfitting.
- Walk-forward testing.
- Separar treino/teste.

Como usar no TGR-01:

- Rodar estrategias sobre dados historicos.
- Comparar regras antes de colocar em paper live.
- Evitar ajustar regra olhando apenas um resultado.

Uso no projeto:

- Criar suite de simulacao historica.
- Relatorios por periodo.
- Testar robustez de gates.

## 20. Metricas de Avaliacao

O que estudar:

- Win rate.
- Profit factor.
- Max drawdown.
- Sharpe/Sortino.
- Expectancy.
- Average win/loss.
- Exposure time.

Como usar no TGR-01:

- Melhorar `evaluate_decisions.py`.
- Separar qualidade do LLM e qualidade do Risk Manager.
- Julgar HOLD, BUY e SELL por horizonte.

Uso em relatorios:

- BUY aprovado: resultado 5m/15m/30m/60m.
- BUY bloqueado: teria sido bom ou ruim?
- HOLD: evitou queda ou perdeu upside?

## 21. Auditoria e Explicabilidade

O que estudar:

- Logs auditaveis.
- Causa de decisao.
- Snapshot de input.
- Reprodutibilidade.

Como usar no TGR-01:

- Gravar snapshot compacto do payload em `trade_logs`.
- Salvar:
  - RSI value/status
  - MACD histogram/status
  - ATR/status
  - news_risk
  - data_health
  - exposure

Uso no projeto:

- Explicar cada BUY/HOLD/SELL sem depender de memoria.
- Facilitar revisao com LLM revisor.

## 22. RAG e Banco Vetorial

O que estudar:

- Embeddings.
- Similaridade semantica.
- Chunking.
- Recuperacao de memoria.
- Riscos de contexto irrelevante.

Como usar no TGR-01:

- Nao usar RAG para calcular trade.
- Usar RAG para memoria e revisao:
  - trades parecidos anteriores;
  - eventos historicos;
  - padroes de erro do LLM;
  - notas de estudo;
  - relatorios passados.

Uso seguro:

- RAG informa contexto.
- Risk Manager ainda decide.
- Se RAG discordar dos dados atuais, HOLD.

## 23. Multiagente

O que estudar:

- Agente decisor.
- Agente critico.
- Agente revisor.
- Perigo de consenso falso.

Como usar no TGR-01:

- Nao usar varios agentes votando automaticamente agora.
- Futuro:
  - LLM A sugere.
  - LLM B critica.
  - Risk Manager bloqueia/aprova.
  - Humano decide em modo semi-auto.

Uso seguro:

- Multiagente para revisao, nao para liberar risco sem regra.

## 24. Execucao Real

O que estudar:

- API da corretora.
- Ordem market/limit.
- Saldos.
- Taxas.
- Falha de rede.
- Ordem parcialmente executada.

Como usar no TGR-01:

- Criar executor real separado do paper.
- Default sempre `REAL_TRADING_ENABLED=false`.
- Limite por ordem em BRL.
- Limite diario de perda.
- Confirmacao manual.

Uso no Risk Manager:

- Mesmo se LLM e Risk aprovarem, executor real ainda valida:
  - saldo;
  - limite;
  - stale data;
  - spread;
  - modo real habilitado.

## 25. Plano de Implementacao por Prioridade

Prioridade 1: agora

- Snapshot compacto do payload em `trade_logs`.
- Melhorar relatorio focado em BUY aprovado/bloqueado.
- Rodar novo teste de 100 ciclos com prompt hardened.
- Avaliar se `RSI oversold` deixou de gerar compra ruim.

Prioridade 2: proxima fase

- Taxa por operacao no paper trading.
- Slippage estimado.
- Volume status.
- ATR status explicito.
- Market regime simples.

Prioridade 3: depois

- Multi-timeframe 5m/15m.
- Backtest historico.
- Walk-forward.
- Relatorio de performance mais completo.

Prioridade 4: futuro

- RAG.
- Banco vetorial.
- Review Agent com outro modelo.
- Modo semi-auto.
- Executor real com limite pequeno.

## 26. Perguntas para o NotebookLM

Use estas perguntas ao estudar os documentos:

1. Quais sinais tecnicos sao mais perigosos se usados isoladamente?
2. Em quais situacoes RSI oversold continua caindo?
3. Como diferenciar reversao real de queda em continuidade?
4. Quais sinais de volume confirmam rompimento ou reversao?
5. Quais eventos de noticias devem bloquear compra de cripto?
6. Como estimar custo total de trade em corretora spot?
7. Quais metricas avaliam melhor um bot conservador?
8. Como evitar overfitting ao ajustar gates?
9. Quando um HOLD deve ser considerado acerto?
10. Quando um BUY bloqueado deve ser considerado erro do Risk Manager?

## 27. Como Transformar Estudo em Codigo

Para cada novo conceito estudado, responder:

1. Isso e dado, feature, gate, sizing ou relatorio?
2. Pode ser calculado em Python?
3. Precisa de LLM ou e regra deterministica?
4. Qual e o estado qualitativo?
5. Qual e a acao segura se estiver incerto?
6. Como auditar no SQLite?
7. Como testar com cenario unitario?
8. Como medir no paper trading?

Exemplo:

Conceito: RSI oversold em queda forte.

- Feature: `rsi.status = OVERSOLD`.
- Feature complementar: `macd.status = BEARISH_EXPANDING`.
- Gate: bloquear BUY.
- Auditoria: motivo `Directional Gate: BUY bloqueado por MACD BEARISH_EXPANDING`.
- Teste: LLM BUY + RSI OVERSOLD + MACD BEARISH_EXPANDING => HOLD.

## 28. Principio Final

O objetivo nao e prever o mercado perfeitamente.

O objetivo e construir um sistema que:

- nao opera com dados ruins;
- nao compra por impulso;
- sabe explicar cada decisao;
- aprende com relatorios;
- melhora por regras testaveis;
- preserva capital quando nao ha vantagem clara.

