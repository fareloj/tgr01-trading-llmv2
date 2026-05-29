# Pipeline de Notícias e RAG: Mitigando Alucinações

Sua percepção sobre o risco do banco vetorial é cirúrgica. Dar ao LLM a liberdade de dizer *"estou com dúvida, deixe-me pesquisar no banco vetorial o que aconteceu na data X"* é transformá-lo num agente ReAct (Reason + Act). 

Embora agentes ReAct sejam maravilhosos para assistentes de chat, para **Trading** eles são um pesadelo:
1. **Loop Infinito:** O modelo pode não achar o que quer e ficar pesquisando a mesma coisa várias vezes, estourando tempo e custo.
2. **Alucinação de Query:** Ele pode alucinar datas ou fatos e pesquisar coisas como "Por que o BTC faliu em 2026?".
3. **Contaminação Temporal:** Ele pode puxar notícias do "Inverno Cripto" de 2022 e usar para justificar uma venda hoje.

## A Solução: Inversão de Controle (Contexto Push vs Pull)

Em vez do LLM pesquisar (Pull), o **Python deve injetar (Push)** o contexto de forma 100% determinística. 

O LLM não deve ter ferramentas (`tools`/`functions`) para pesquisar ativamente. Ele deve operar como um juiz: ele julga as evidências que já estão na mesa.

### Como Estruturar o Pipeline de RAG (Seguro)

1. **Ingestão Contínua (Python Cronjob)**
   * Um script Python baixa notícias a cada 1 hora de fontes confiáveis (CoinDesk, Bloomberg, etc).
   * As notícias ganham *Embeddings* e são salvas no Banco Vetorial (ex: Qdrant, ChromaDB, PGVector).
   * **Metadata Obrigatório:** `timestamp_unix`, `source_trust_score`.

2. **A Montagem do Contexto (Python Estrito)**
   Antes de chamar o `Analyst Agent`, o código Python executa uma busca híbrida de forma autônoma. O LLM não decide nada aqui.
   
   * **Bloco 1 (Urgência):** Notícias das últimas 24 horas (busca simples por cronologia).
   * **Bloco 2 (Macro Semântico):** Busca vetorial no banco apenas nos últimos 7 dias, focando em palavras-chave pré-definidas (ex: "FED", "Juros", "Baleias", "Hack", "Regulação").

3. **O Prompt Blindado do Analyst Agent**
   O Python injeta os blocos acima no prompt, garantindo que o modelo saiba exatamente a idade da informação.

```text
Você é o Analyst Agent. 
Hoje é: 09 de Maio de 2026.

[NOTÍCIAS DAS ÚLTIMAS 24H]
- [Ontem, 20:00]: Inflação americana vem abaixo do esperado.
- [Hoje, 09:00]: SEC aprova novo regulamento para stablecoins.

[CONTEXTO MACRO DOS ÚLTIMOS 7 DIAS]
- [Há 5 dias]: Binance pausa saques por 2h devido a manutenção técnica.

Analise o impacto destas notícias no preço do Bitcoin.
```

## Tratando a "Incerteza" da IA

E se a IA continuar incerta após ler as notícias? 
Nesse caso, nós queremos que ela **aborte a operação**. 

Em vez de deixar a IA "pesquisar mais", a IA deve apenas preencher o seu contrato JSON assim:
```json
{
  "market_sentiment": "NEUTRAL",
  "confidence_score": 30,
  "red_flags": ["Notícias escassas sobre volume do final de semana", "Cenário macroeconômico dúbio"],
  "action_recommendation": "HOLD"
}
```
Se a confiança for baixa, o `CIO Agent` ou o `Risk Manager` usarão isso para **bloquear compras**. 

No mercado financeiro, a ausência de dados não significa que você deve cavar mais fundo até achar um motivo para operar; significa simplesmente que **não é hora de operar**.

## Arquitetura do Módulo de Dados

Para a V2, recomendo esta divisão de processos:

1. **Worker de Notícias (Independente):** Um processo Python isolado que fica rodando e populando o banco vetorial 24/7.
2. **Worker de Preços (Independente):** Fica populando Klines em banco relacional/timeseries.
3. **Bot Principal:** Quando for rodar o ciclo de análise, ele só lê do banco (read-only). Ele nunca espera uma notícia baixar. Se a internet do worker cair e não tiver notícia das últimas 2h, o bot principal deve entrar em *Safe Mode* e não operar.
