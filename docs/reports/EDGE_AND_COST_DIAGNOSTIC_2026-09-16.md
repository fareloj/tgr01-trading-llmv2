# Diagnostico De Edge E Custo - 2026-09-16

## Resumo

Apos corrigir uma incompatibilidade de calibracao que bloqueava 100% das ordens,
uma campanha historica de 120 amostras foi executada na particao `development`
para medir se o desenho atual consegue operar com margem liquida positiva.

**Resultado: nao consegue, e a causa e aritmetica, nao de prompt.**

O movimento tipico do BTC/BRL no horizonte de decisao e menor que o custo de
transacao do proprio projeto. Nenhum ajuste de prompt resolve isso.

## O que foi medido

Campanha: `25c0851fbca76e8dcacb0e3004f60941f7e4d5978da738d81a4d3427f888dcfa`
Dataset: `8504c29db1253e2a5d8bdafe`, particao `development`
Range: 2026-02-28 a 2026-06-18, `neutral-fresh`, 120 amostras, 0 erros.

### Acoes

| Metrica | Valor |
|---|---:|
| Propostas do LLM | HOLD 90, SELL 15, BUY 15 |
| Aprovadas pelo Risk Manager | HOLD 91, SELL 14, BUY 15 |
| Concordancia acao do Risk vs regime esperado | 49/120 (40.8%) |
| Direcionais alinhadas ao regime | 15 |
| Direcionais **contra** o regime | 14 |

A concordancia acima usa a acao final do Risk Manager. A acao bruta do LLM
concorda em 50/120; a diferenca de uma amostra e o unico SELL que o Risk
converteu em HOLD.

### Margem liquida das acoes aprovadas

A `directional_edge_after_cost_pct` ja desconta fee e slippage configurados
(0.30%/lado).

| Horizonte | n | Media | Alinhadas | Contra o regime |
|---:|---:|---:|---:|---:|
| 5m | 29 | -0.5541% | -0.4560% | -0.6592% |
| 15m | 29 | -0.5020% | -0.3457% | -0.6695% |
| 30m | 29 | -0.4802% | -0.2151% | -0.7642% |
| 60m | 29 | -0.5747% | -0.0511% | -1.1356% |

Todas as medias sao negativas. Ate as acoes alinhadas ao regime perdem dinheiro.

## A causa aritmetica

O projeto cobra `PAPER_FEE_RATE=0.003` por lado. O hurdle efetivo e:

- BUY: `0.9%` ida-e-volta (fee + slippage ATR-derivado)
- SELL: `0.55%` saida

O movimento tipico do ativo, medido como a mediana do valor absoluto do
movimento futuro (a mediana com sinal fica proxima de zero, como esperado):

| Horizonte | \|movimento\| mediano | Atingem o hurdle de 0.9% |
|---:|---:|---:|
| 5m | 0.099% | 0/119 (0%) |
| 15m | 0.159% | 3/119 (3%) |
| 30m | 0.224% | 10/120 (8%) |
| 60m | 0.470% | 25/120 (21%) |

No melhor caso (60m), o hurdle e **1.9x** o movimento tipico. Em 5m, e 9x.

## Poder preditivo dos indicadores

Testado contra o movimento futuro real das mesmas linhas:

| Horizonte | MACD | RSI |
|---:|---:|---:|
| 5m | 53.0% | 60.0% (n=5) |
| 15m | 58.5% | 40.0% (n=5) |
| 30m | 53.0% | 60.0% (n=5) |
| 60m | 50.0% | 40.0% (n=5) |

Baseline aleatorio = 50%. O MACD tem um sinal fraco em 15m (58.5%) que
desaparece em 60m (50.0%). As amostras de RSI sao poucas demais para concluir.

**Mesmo o melhor caso nao paga o custo.** Com 58.5% de acerto e movimento
tipico de 0.159% em 15m, o ganho bruto esperado e
`(0.585 - 0.415) * 0.159 = 0.027%`, contra um hurdle de 0.9%. O edge disponivel
e ~3% do custo necessario.

## Interpretacao

1. Este resultado **nao implica** que o codigo esteja errado. O pipeline executa
   o que foi desenhado: falha fechado, audita, e nao inventa maturacao.
2. A tese original ("um LLM interpretando evidencia ja calculada, com o codigo
   controlando risco") permanece testavel, mas o **horizonte de minutos e
   incompativel com a taxa de 0.30%/lado**.
3. A taxa de 0.30%/lado e uma premissa configuravel do projeto
   (`PAPER_FEE_RATE`), nao uma medicao feita nesta sessao. Se a taxa real de
   execucao for menor, o hurdle cai na mesma proporcao e a conclusao deve ser
   recalculada; ela nao foi verificada contra a tabela de fees da exchange.

## O que mudar para ter uma chance

Nenhuma destas e uma correcao de bug; todas sao decisoes de escopo que exigem
sign-off do operador:

1. **Horizonte maior.** Operar em horas/dias, onde o movimento tipico supera o
   custo. Um movimento de 3-5% paga 0.9% com folga; um de 0.16% nunca paga.
2. **Operar raramente.** Aceitar que a maior parte do tempo a resposta correta e
   HOLD e so agir quando o movimento esperado exceder varias vezes o hurdle.
   O gate de custo ja existe conceitualmente (`directional_edge_after_cost_pct`),
   mas o LLM nao o recebe no payload para calibrar conviccao.
3. **Reduzir custo.** Inviavel dentro do escopo atual; a taxa e do exchange.
4. **Avaliar saidas, nao entradas.** O red team de 2026-08-01 ja apontava que
   "o principal trabalho restante e qualidade direcional em regimes bearish,
   avaliacao de saidas e maturacao estatistica".

## Limitacoes deste diagnostico

- 120 amostras em uma unica particao (`development`). Nao e walk-forward e nao
  cobre regimes fora de fev-jun/2026.
- `neutral-fresh` usa noticias sinteticas; nao valida desempenho com noticia real.
- A particao `validation` e o `holdout` selado nao foram tocados.
- Os 5 ciclos por janela compartilham o mesmo movimento de 60 minutos, entao as
  120 amostras representam 24 janelas contextualmente relacionadas, nao 120
  observacoes independentes.
- Nao ha claim de lucro nem de perda em producao: isto e uma avaliacao
  retrospectiva em condicoes conhecidas.

## Proximo passo recomendado

Nao ajustar prompts para "melhorar acerto". O gargalo e o custo contra o
horizonte. Antes de qualquer campanha maior, decidir explicitamente qual das
alavancas acima entra no escopo; caso contrario a proxima campanha reproduzira
este mesmo resultado com mais amostras e o mesmo custo.
