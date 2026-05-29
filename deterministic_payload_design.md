# Payload Determinístico e Lógica de Segurança

A premissa principal aqui é: **"Garbage In, Garbage Out"**. O Math Agent só será um bom analista se o Python entregar a ele a conclusão matemática já mastigada. O LLM não deve ver arrays de números; ele deve ver **Estados Qualitativos** derivados da matemática.

---

## 1. O Payload Ideal do Math Agent (O que o LLM vê)

O Python enviará um JSON estrito para o prompt do Math Agent. Nenhuma conta é exigida do LLM.

```json
{
  "context": {
    "asset": "BTC/BRL",
    "timeframe": "1D",
    "market_regime": "UPTREND_WEAKENING", 
    "current_price": 350000
  },
  "indicators": {
    "rsi_14": {
      "value": 78,
      "status": "OVERBOUGHT",
      "trend": "RISING"
    },
    "macd": {
      "histogram": -500,
      "status": "BEARISH_DIVERGENCE"
    },
    "volatility_atr": {
      "status": "HIGH",
      "warning": "Cuidado com falsos rompimentos"
    },
    "volume_profile": {
      "status": "BELOW_AVERAGE",
      "interpretation": "Alta sem volume (fraqueza)"
    }
  },
  "price_action": {
    "distance_to_nearest_support": "2.5%",
    "distance_to_nearest_resistance": "0.5%",
    "key_level_interaction": "TESTING_RESISTANCE"
  },
  "portfolio_context": {
    "current_exposure_level": "MODERATE",
    "is_in_drawdown": false
  }
}
```

### Limites de Conclusão do Math Agent:
* O que ele **DEVE** fazer: Ligar os pontos lógicos (ex: "Preço testando resistência + RSI sobrecomprado + MACD divergente = Forte sinal Bearish").
* O que ele **NÃO PODE** fazer: Sugerir um preço de compra, sugerir tamanho de posição, calcular onde colocar o stop loss.

---

## 2. O Que Fica Exclusivo do Risk Manager (O LLM NUNCA vê)

Para evitar que o LLM "tente ser esperto", o Python **jamais** enviará estes dados para os Agentes:
1. **Saldo da Conta (BRL/USD brutos):** O LLM não sabe se você tem 100 reais ou 1 milhão. Ele não deve ser afetado psicologicamente pelo montante.
2. **Histórico de PnL Bruto:** O LLM não deve saber se o bot está perdendo muito dinheiro para não tentar "recuperar a perda" (Revenge Trading alucinado).
3. **Cálculo de Kelly:** As variáveis de *Win Rate*, *Risk/Reward Ratio* e o output do Fractional Kelly são privados do código Python.
4. **Preço Exato do Stop Loss Diário:** Controlado pela engine determinística.

---

## 3. O Multiplicador de Confiança (`system_reliability`)

A confiança final da operação é: `CIO_Conviction * system_reliability`.
O `system_reliability` começa em `1.0` e sofre **penalizações determinísticas no Python** baseadas no ambiente:

* **Conflito de Agentes:** Math(BUY) vs Analyst(SELL) -> `x 0.5`
* **Idade das Notícias:** Se não houver notícias relevantes nas últimas 24h (Fim de semana/Feriado) -> `x 0.7`
* **Volatilidade Extrema (ATR explodiu):** Mercado em surto irracional -> `x 0.6`
* **Liquidez/Volume Baixo:** Falsos rompimentos iminentes -> `x 0.8`
* **Latência de API:** Se demorou mais de X segundos para buscar preços da Exchange (risco de descompasso) -> `x 0.5`

Exemplo: Se o CIO está 90% confiante numa COMPRA, mas é domingo (sem notícias = 0.7) e o volume está baixo (0.8), a confiança final matemática vira `90% * 0.7 * 0.8 = 50.4%`.

---

## 4. Condições de HOLD Automático (Trava Total)

O Risk Manager (Python puro) intercepta o fluxo e dita `HOLD` automático antes mesmo de chamar o LLM (poupando custo de API) ou logo após a saída do CIO, se ocorrer:

### Condições de "Short-Circuit" (Antes de chamar o LLM)
1. **Drawdown Diário Atingido:** O bot perdeu X% no dia? Trava operações até meia-noite (Hard Stop Loss).
2. **Dados Ausentes:** Falha de conexão com Mercado Bitcoin ou falha no Worker de Notícias (Banco vetorial sem dados recentes).
3. **Exposição Máxima Atingida:** Se o portfólio já estiver em 100% BTC, não adianta gastar tokens de LLM para ele dizer "BUY".

### Condições de Interceptação Pós-CIO (Risk Manager bloqueia)
1. **Confiança Pós-Penalização:** Se a confiança penalizada (`system_reliability` aplicada) cair abaixo de `40%`.
2. **Veto de Red Flag:** Se o Analyst Agent levantou `black_swan_alert = TRUE`, qualquer ordem de `BUY` é convertida em `HOLD`.
3. **Matemática do Kelly:** Se o Kelly Fracionado sugerir comprar R$ 10, mas a Exchange exige um ticket mínimo de R$ 50, o Risk Manager aborta e gera `HOLD` por falta de capitalização segura.
