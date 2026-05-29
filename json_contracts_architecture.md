# Contratos JSON e Schemas de Agentes (V2)

Para garantir que o sistema não falhe na camada de comunicação, a troca de informações entre os agentes (LLMs) e o sistema determinístico (Python) deve ser feita estritamente através de contratos de dados (usando `Pydantic`).

Se o modelo de IA retornar qualquer coisa fora desse padrão (ex: alucinar um campo, retornar um texto solto, errar a tipagem), o validador Pydantic lançará um erro, e o fluxo será abortado (a operação volta para estado `HOLD`), blindando o seu capital.

Aqui estão os contratos (schemas) essenciais da V2.

## 1. Math Agent Schema
O papel do Math Agent é ler um resumo estático de indicadores (que o Python já calculou) e interpretá-los, dando um peso de direção.

```python
from pydantic import BaseModel, Field
from typing import Literal

class MathAgentSignal(BaseModel):
    analysis_summary: str = Field(
        ..., description="Uma breve explicação (max 3 frases) da leitura técnica."
    )
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL"] = Field(
        ..., description="A direção primária sugerida pelos indicadores quantitativos."
    )
    confidence: int = Field(
        ..., ge=0, le=100, description="Nível de confiança técnica de 0 a 100."
    )
    anomalies_detected: bool = Field(
        default=False, description="Existem divergências clássicas (ex: Preço subindo, RSI caindo)?"
    )
```

## 2. Analyst Agent Schema
O Analyst Agent lê as notícias e metadados injetados via RAG determinístico (Push) e devolve o sentimento do mercado.

```python
from typing import List

class AnalystAgentSignal(BaseModel):
    market_sentiment: Literal["BULLISH", "BEARISH", "NEUTRAL"] = Field(
        ..., description="O sentimento geral extraído das notícias e contexto macro."
    )
    sentiment_confidence: int = Field(
        ..., ge=0, le=100, description="Grau de certeza sobre o sentimento (0-100)."
    )
    red_flags: List[str] = Field(
        default_factory=list, description="Lista de riscos críticos identificados nas notícias (ex: 'Risco Regulatório', 'Aumento de Juros'). Se não houver, retorne lista vazia."
    )
    black_swan_alert: bool = Field(
        default=False, description="TRUE apenas se houver notícia de um evento catastrófico ou de pânico extremo no mercado."
    )
```

## 3. CIO Agent Schema (O Tomador de Decisão)
O CIO Agent recebe no seu prompt o `MathAgentSignal`, o `AnalystAgentSignal` e o `PortfolioState` (Exposição atual, banca, etc). Ele então emite o "Veredito".

> **Nota Crítica:** O CIO **NÃO** decide a quantidade ou a alocação. Ele apenas sugere a ação e expressa sua convicção baseada nos dois agentes anteriores.

```python
class CIODecision(BaseModel):
    reasoning: str = Field(
        ..., description="Justificativa da decisão confrontando o lado matemático com o lado qualitativo e considerando o portfólio atual."
    )
    action: Literal["BUY", "SELL", "HOLD"] = Field(
        ..., description="Ação final recomendada."
    )
    conviction_score: int = Field(
        ..., ge=0, le=100, description="Convencecimento final da operação. Se Math e Analyst divergirem, essa nota deve ser baixa."
    )
```

## 4. O Risk Manager (Python Determinístico)
O Risk Manager não é um LLM. É uma classe Python que recebe o `CIODecision` e os dados brutos da corretora para tomar a decisão matemática final.

```python
# Pseudo-código do Fluxo do Risk Manager

def execute_risk_management(cio_decision: CIODecision, portfolio, max_risk_per_trade: float):
    # Regra 1: Na dúvida, HOLD.
    if cio_decision.action == "HOLD":
        return "Nenhuma ação tomada."
        
    # Regra 2: CIO sem convicção não opera.
    if cio_decision.conviction_score < 70:
        log("Convicção do CIO baixa. Operação bloqueada.")
        return "HOLD"
        
    # Regra 3: Se o CIO quer comprar, mas já atingimos o teto de exposição.
    if cio_decision.action == "BUY" and portfolio.exposure >= portfolio.max_exposure_limit:
        log("Teto de alocação atingido. Proteção de patrimônio ativa.")
        return "HOLD"
        
    # Regra 4: O sizing (Matemática Pura).
    # O Kelly Fracionado (Half/Quarter Kelly) é calculado APENAS no Python.
    recommended_size = calculate_fractional_kelly(
        win_rate_history=portfolio.win_rate, 
        risk_reward_ratio=portfolio.rr_ratio
    )
    
    # Aplica um limitador duro: Nunca investir mais que o máximo permitido, mesmo que Kelly diga que sim.
    final_size = min(recommended_size, max_risk_per_trade)
    
    return ExecuteOrder(action=cio_decision.action, size=final_size)
```

## Vantagens desta Estrutura
1. **Tipagem Forte:** Se o modelo esquecer de fechar aspas ou mandar uma string em vez de inteiro no `confidence`, a biblioteca recusa a resposta.
2. **Prevenção de Cisne Negro:** Se o Analyst marcar `black_swan_alert = True`, você pode programar o Risk Manager para liquidar posições (SELL ALL) automaticamente, ignorando qualquer outra coisa.
3. **Escalabilidade:** Se no futuro você quiser adicionar um `OnChainAgent` (para rastrear movimentos de baleias na blockchain), basta criar um `OnChainSignal` e passá-lo para o CIO ler, sem mexer na estrutura core.
