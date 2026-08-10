# Pipeline Multiagente de Analise de Mercado

## Status

Proposta experimental. Esta arquitetura ainda nao faz parte da pipeline ativa e
nao possui permissao para executar ordens reais. Sua utilidade deve ser
demonstrada em paper trading e avaliacao fora da amostra antes de qualquer
promocao.

## Objetivo

Separar a interpretacao de noticias, a leitura da janela tecnica e a decisao de
trading em responsabilidades explicitas. A proposta nao consiste em colocar
varios modelos para votar. Cada agente possui um contrato limitado, e o Risk
Manager deterministico continua sendo a autoridade final.

```text
Noticias verificadas -> Agente de Noticias ------------------+
                                                            |
Candles 15m -> calculos deterministicos -> Agente Tecnico 8h +-> Decisor LLM
                                                            |       |
Snapshot original -------------------------------------------+       v
                                                              Risk Manager
                                                                   |
                                                            Paper execution
```

## Principios

1. O Python calcula todos os indicadores e estatisticas.
2. Os agentes interpretam evidencias; eles nao recalculam indicadores.
3. Toda afirmacao relevante precisa apontar para um campo ou ID de origem.
4. O decisor recebe o snapshot original, nao apenas resumos de outros agentes.
5. Nenhum agente pode executar ordens ou alterar limites de risco.
6. Erros, timeouts e respostas invalidas produzem degradacao controlada.
7. O sistema deve provar ganho sobre o agente unico antes de ganhar autoridade.

## Modelos Experimentais por Papel

A configuracao experimental inicial separa capacidade e custo por funcao:

| Papel | Modelo inicial | Responsabilidade |
|---|---|---|
| Noticias | `gpt-oss:20b-cloud` | Classificar relevancia, impacto, conflitos e lacunas das noticias persistidas |
| Tecnica 8h | `gpt-oss:20b-cloud` | Interpretar estatisticas calculadas pelo Python e relaciona-las ao contexto noticioso |
| Decisao final | `gpt-oss:120b-cloud` | Produzir `BUY`, `SELL` ou `HOLD` a partir do snapshot e dos dois relatorios |

Os nomes sao configuraveis por `NEWS_AGENT_MODEL`, `TECHNICAL_AGENT_MODEL` e
`LLM_MODEL`. Durante a verificacao local, `qwen3.5:cloud` exigiu uma assinatura
indisponivel e variantes Nemotron acessiveis nao cumpriram de forma confiavel o
contrato tecnico completo. Por isso, o mesmo GPT-OSS 20B ocupa temporariamente
os dois papeis analiticos. Isso reduz diversidade de modelo e nao deve ser
interpretado como ganho comprovado. O default da funcionalidade e
`MULTI_AGENT_ENABLED=false` com `MULTI_AGENT_SHADOW_MODE=true`: os relatorios
podem ser comparados, mas nao influenciam nem mesmo a execucao paper.

Todos os modelos usam temperatura zero e contratos JSON validados. O Agente de
Noticias nao recebe instrucao para buscar fatos externos e deve tratar o texto
das noticias como entrada nao confiavel, inclusive contra prompt injection.
O GPT-OSS 20B e apenas o candidato atual desses papeis. O relatorio sempre
preserva os IDs e as manchetes originais, e o decisor recebe tambem o snapshot
original, para reduzir propagacao de erro entre agentes da mesma familia.

## Etapa 1: Agente de Noticias

### Entrada

- noticias coletadas e persistidas pelo worker;
- ID, timestamp, fonte e manchete de cada noticia;
- idade dos dados e sinalizadores de stale;
- resultados relevantes recuperados pelo RAG oficial, quando disponiveis;
- termos de risco identificados deterministicamente.

### Saida estruturada

- resumo curto do contexto;
- eventos relevantes com os IDs das noticias usadas;
- impacto provavel: `POSITIVE`, `NEGATIVE`, `NEUTRAL` ou `UNCERTAIN`;
- horizonte do impacto: intraday, dias ou indefinido;
- relevancia especifica para BTC;
- conflitos entre fontes;
- confianca limitada pela qualidade e idade dos dados;
- lacunas conhecidas.

### Restricoes

- nao inventar eventos, precos, fontes ou noticias externas;
- nao transformar noticias gerais sobre cripto em impacto certo sobre BTC;
- noticias stale reduzem confianca, mas nao substituem a analise tecnica;
- uma noticia sem ID de origem nao pode sustentar uma decisao direcional.

## Etapa 2: Agente Tecnico da Janela de 8 Horas

Com candles de 15 minutos, a janela principal contem 32 candles. Antes da
chamada ao modelo, scripts deterministicos calculam:

- retorno em 1h, 4h e 8h;
- inclinacao e forca da tendencia;
- RSI, MACD, EMAs e seus estados normalizados;
- ATR e volatilidade realizada;
- volume relativo e anomalias de volume;
- drawdown, distancia do topo e amplitude da janela;
- regime tecnico: tendencia, lateralizacao ou alta volatilidade;
- qualidade, continuidade e idade dos candles.

O agente tecnico recebe esses valores e o relatorio do Agente de Noticias. Sua
funcao e interpretar se o movimento tecnico confirma, contradiz ou nao possui
relacao clara com o contexto noticioso.

### Saida estruturada

- regime tecnico;
- direcao e forca observadas;
- evidencias tecnicas usadas;
- relacao entre tecnica e noticias: `ALIGNED`, `CONFLICTING`, `PARTIAL` ou
  `UNRELATED`;
- contraevidencias;
- confianca e condicoes de invalidacao.

## Etapa 3: Decisor Principal

O decisor recebe simultaneamente:

1. snapshot deterministico original;
2. relatorio estruturado do Agente de Noticias;
3. relatorio estruturado do Agente Tecnico;
4. evidencias, contraevidencias e indicadores de qualidade dos dados;
5. contexto do portfolio permitido para decisao.

Ele retorna `BUY`, `SELL` ou `HOLD`, conviccao, tese curta, evidencias usadas,
contraevidencias e condicoes que invalidariam a tese. O decisor nao recebe uma
frase simplificada como unica fonte, pois isso criaria propagacao de erro entre
agentes.

Exemplo conceitual:

```json
{
  "technical_regime": "SIDEWAYS",
  "eight_hour_return_pct": -0.12,
  "news_bias": "NEGATIVE_UNCERTAIN",
  "technical_news_alignment": "PARTIAL",
  "action": "HOLD",
  "conviction": 60,
  "supporting_evidence": ["return_8h", "atr_regime", "news:278"],
  "counter_evidence": ["macd_bullish_expanding"]
}
```

## Risk Manager e Execucao

O Risk Manager permanece deterministico e soberano. Ele valida frescor,
exposicao, cooldown, gates direcionais, sizing e demais limites. Nenhum texto ou
conviccao de agente pode contornar essas regras.

Politica inicial de falhas:

- Agente de Noticias indisponivel: marcar contexto ausente e limitar conviccao;
- dados tecnicos incompletos ou Agente Tecnico invalido: `HOLD` ou abortar ciclo;
- Decisor invalido: `HOLD`;
- Risk Manager indisponivel: abortar ciclo;
- RAG indisponivel: continuar apenas com noticias persistidas, registrando a
  degradacao;
- timeout ou schema invalido: uma tentativa controlada e fallback seguro.

Os resultados devem ser associados ao hash do snapshot para evitar misturar
analises produzidas sobre estados diferentes do mercado.

## Plano de Validacao

A primeira versao opera em `shadow mode`: gera e registra relatorios, mas a
pipeline atual continua responsavel pela decisao de paper trading.

Comparacoes obrigatorias:

- agente unico atual contra pipeline especializada;
- mesmos snapshots, modelos, temperatura e limites de contexto;
- cenarios de alta, queda, lateralizacao e alta volatilidade;
- noticias frescas, stale, conflitantes, irrelevantes e indisponiveis;
- respostas repetidas para medir estabilidade;
- falhas simuladas de cada agente e do RAG.

Metricas:

- precisao e retorno apos custos de `BUY` e `SELL` em 4h e 24h;
- drawdown e exposicao;
- oportunidades perdidas e perdas evitadas;
- calibracao da conviccao;
- contradicoes entre acao, brief e evidencias;
- afirmacoes sem fonte ou dados inventados;
- divergencia entre agentes;
- intervencoes corretas e incorretas do Risk Manager;
- latencia, tokens e consumo de quota;
- ganho marginal sobre o agente unico.

## Criterio de Promocao

A arquitetura so deve influenciar decisoes quando demonstrar, fora da amostra:

- melhora reproduzivel sobre o agente unico;
- ausencia de aumento relevante em alucinacoes e contradicoes;
- calibracao igual ou melhor;
- tolerancia comprovada a falhas parciais;
- custo e latencia operacionais aceitaveis;
- nenhum enfraquecimento do Risk Manager.

Se esses criterios nao forem atendidos, os agentes especializados permanecem
como ferramentas de auditoria e geracao de relatorios, sem autoridade sobre a
execucao.
