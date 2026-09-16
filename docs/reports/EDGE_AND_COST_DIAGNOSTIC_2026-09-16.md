# Diagnostico De Edge E Custo - 2026-09-16

## Resumo

Apos corrigir uma incompatibilidade de calibracao que bloqueava 100% das ordens,
uma campanha historica de 120 amostras foi executada na particao `development`
para medir se o desenho atual consegue operar com margem liquida positiva.

**Resultado observado: nao conseguiu nesta amostra.** Todas as medias de margem
liquida das acoes aprovadas foram negativas, e o custo configurado e maior que o
movimento tipico do ativo no horizonte de decisao.

O que este resultado sustenta: **para a politica atual, nesta particao e com
estes custos, o edge bruto observado nao cobriu o custo de transacao.**

O que ele NAO sustenta: uma afirmacao universal de que "nenhum prompt resolve"
ou de que a proxima campanha necessariamente repetira este numero. Uma unica
particao `development`, com 24 janelas contextualmente relacionadas, nao exclui
comportamento seletivo melhor, efeitos de prompt nao testados, ou defeitos ainda
nao descobertos. A conclusao correta e mais estreita: o custo configurado excedeu
o edge bruto desta amostra, a margem e grande (cerca de uma ordem de grandeza: o
melhor ganho bruto observado foi 0.0720% contra 0.70% de custo), e a distancia e
suficiente para que a proxima campanha trate custo e horizonte como variavel
primaria. Ver a secao "Proximo experimento".

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

A `directional_edge_after_cost_pct` ja desconta o custo configurado por lado
(fee 0.30% + slippage minimo 0.05% = 0.35% por lado; 0.70% no round-trip de BUY).

| Horizonte | n | Media | Alinhadas | Contra o regime |
|---:|---:|---:|---:|---:|
| 5m | 29 | -0.5541% | -0.4560% | -0.6592% |
| 15m | 29 | -0.5020% | -0.3457% | -0.6695% |
| 30m | 29 | -0.4802% | -0.2151% | -0.7642% |
| 60m | 29 | -0.5747% | -0.0511% | -1.1356% |

Todas as medias sao negativas. Ate as acoes alinhadas ao regime perdem dinheiro.

## A causa aritmetica

Duas coisas diferentes aparecem no relatorio da campanha e precisam ser
separadas, porque a primeira versao deste documento as misturou:

**Custo de transacao** (`estimated_action_cost_pct`), configurado pelo projeto:
fee `0.003` + slippage minimo `0.0005` por lado = 0.35%/lado.

- BUY round-trip (2 lados): **0.70%**
- SELL saida (1 lado): **0.35%**

**Hurdle de classificacao** (`buy_round_trip_hurdle_pct`), que e o custo mais o
`--threshold-pct` de 0.20% que a campanha usa para rotular bom/neutro/ruim:

- BUY: `0.70 + 0.20` = **0.90%**
- SELL: `0.35 + 0.20` = **0.55%**

O threshold de 0.20% nao e um custo pago; e uma margem de indiferenca usada para
classificar o resultado. Os 0.90% citados adiante sao o **hurdle de BUY**; SELL
tem um hurdle menor (0.55%). A comparacao abaixo usa o hurdle de BUY para os dois
lados, o que e conservador para SELL mas nao o representa com precisao.

O movimento tipico do ativo, medido como a mediana do valor absoluto do
movimento futuro (a mediana com sinal fica proxima de zero, como esperado):

| Horizonte | \|movimento\| mediano | Atingem o custo de 0.70% | Atingem o hurdle de 0.90% |
|---:|---:|---:|---:|
| 5m | 0.099% | 0/119 (0%) | 0/119 (0%) |
| 15m | 0.159% | 6/119 (5%) | 3/119 (3%) |
| 30m | 0.224% | 16/120 (13%) | 10/120 (8%) |
| 60m | 0.470% | 38/120 (32%) | 25/120 (21%) |

No melhor caso (60m), o custo de BUY e **1.5x** o movimento tipico (0.70/0.470);
contra o hurdle completo de 0.90%, e **1.9x**. Em 5m, o custo e 7x o movimento
tipico.

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

**Isto e uma ilustracao, nao uma estimativa.** Multiplicar acerto por movimento
mediano ignora que acertos e erros podem ter magnitudes diferentes. Nesta amostra
de 15m eles tem: seguindo o MACD, o movimento absoluto medio foi 0.2837% nos 38
acertos e 0.2259% nos 27 erros, o que da um ganho bruto assinado medio de
**+0.0720%** — ou seja, a formula ilustrativa `(0.585 - 0.415) * 0.159 = 0.027%`
**subestima** o resultado desta amostra, nao o superestima.

A conclusao nao muda, e a distancia e o ponto: **+0.0720% de ganho bruto medio
contra um custo de 0.70% por round-trip.** O ganho bruto observado e cerca de um
decimo do custo. Mesmo com o sinal direcional do MACD em 15m sendo o melhor de
todos os testados, ele nao chega perto de pagar a transacao.

## Interpretacao

1. Este resultado **nao implica** que o codigo esteja errado. O pipeline executa
   o que foi desenhado: falha fechado, audita, e nao inventa maturacao.
2. A tese original ("um LLM interpretando evidencia ja calculada, com o codigo
   controlando risco") permanece testavel, mas o **horizonte de minutos e
   incompativel com o custo de 0.35%/lado assumido nesta amostra**.
3. O custo de 0.35%/lado (fee 0.30% + slippage minimo 0.05%) e uma premissa
   configuravel do projeto (`PAPER_FEE_RATE`, `PAPER_MIN_SLIPPAGE_RATE`), nao uma
   medicao feita nesta sessao. Ele nao foi verificado contra a tabela de fees da
   exchange. Se a fee real for menor, o custo cai, mas **nao proporcionalmente**:
   o slippage minimo (0.05%/lado) permanece como piso, e o edge liquido depende de
   como cada movimento cruza o novo hurdle. Com fee zero, o custo de BUY ainda
   seria 0.10%, nao zero. A conclusao precisa ser recalculada com a taxa real, nao
   escalada linearmente. Ver "Proximo experimento".

## Proximo experimento

**Decisao pendente do operador, nao do agente.** A proxima campanha com o desenho
atual teria que superar a mesma distancia entre ganho bruto e custo que esta
amostra mediu, sem nenhuma mudanca de premissa. Se o operador nao quiser mudar
nada, a campanha maior serve para estreitar o intervalo de confianca sobre um
resultado que ja aponta negativo, nao para descobrir um edge novo.

Verificacao que deve vir PRIMEIRO, porque e barata e determina se o problema e
real: **confirmar a taxa efetiva da conta**. `PAPER_FEE_RATE=0.003` e uma
premissa do arquivo de configuracao, nao uma medicao. O cliente autenticado ja
consulta as fees reais da exchange (`backend/execution/mb_private_client.py`,
usado por `backend/tests/validate_mb_order_dry_run.py`); se a taxa real for menor,
o custo cai (com o piso de slippage) e a leitura deve ser refeita.

Experimento proposto para a proxima campanha, se a taxa se confirmar alta:

- **Hipotese:** com horizonte de 4h ou 24h, o movimento tipico excede o custo de
  transacao com margem suficiente para um edge liquido positivo.
- **Escopo:** somente particao `development`. `validation` e `holdout` permanecem
  intocados. Prompts, thresholds do Risk Manager e regras de risco ficam
  congelados; nenhuma alteracao de codigo durante a coleta.
- **Metrica de aceitacao, pre-registrada:** media de
  `directional_edge_after_cost_pct` positiva, com intervalo de confianca de 95%
  por block-bootstrap sobre blocos contiguos de 24h, que nao cruze zero, em pelo
  menos 2 dos 3 regimes, com no minimo 30 acoes direcionais aprovadas **por
  regime** (90 no total).
- **Criterio de falha:** se a media continuar negativa, o desenho de horizonte
  curto esta encerrado como evidencia e o proximo passo vira a alavanca 2
  (operar raramente, apenas quando o movimento esperado exceder varias vezes o
  custo) ou uma mudanca de escopo maior.

Alavancas disponiveis, em ordem de custo de implementacao:

1. **Horizonte maior.** Operar em horas/dias, onde o movimento tipico supera o
   custo. Um movimento de 3-5% paga 0.70% com folga; um de 0.16% nunca paga.
2. **Operar raramente.** Fornecer o custo esperado ao LLM no payload para que ele
   calibre conviccao contra ele, e so agir quando o movimento esperado exceder
   varias vezes o custo. Hoje o LLM nao recebe nenhum campo de custo.
3. **Confirmar custo.** Verificar a taxa real antes de qualquer conclusao.
4. **Avaliar saidas, nao entradas.** O red team de 2026-08-01 ja apontava que
   "o principal trabalho restante e qualidade direcional em regimes bearish,
   avaliacao de saidas e maturacao estatistica".

## Limitacoes deste diagnostico

- 120 amostras em uma unica particao (`development`). Nao e walk-forward e nao
  cobre regimes fora de fev-jun/2026.
- `neutral-fresh` usa noticias sinteticas; nao valida desempenho com noticia real.
- A particao `validation` e o `holdout` selado nao foram tocados.
- Os 5 ciclos de cada janela comecam em timestamps diferentes e cobrem 60 minutos
  a partir de cada um. Eles **se sobrepoem** (o ciclo 1 cobre 15:10-16:10, o ciclo
  5 cobre 15:30-16:30) e os movimentos realizados diferem entre ciclos, mas as
  observacoes compartilham amplamente o mesmo trecho de mercado. As 120 amostras
  representam 24 janelas contextualmente relacionadas, nao 120 observacoes
  independentes, o que estreita de forma irreal qualquer intervalo de confianca
  calculado sobre elas.
- Nao ha claim de lucro nem de perda em producao: isto e uma avaliacao
  retrospectiva em condicoes conhecidas.
