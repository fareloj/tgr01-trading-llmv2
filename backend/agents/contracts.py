from pydantic import BaseModel, Field
from typing import Literal

class DecisionOutput(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"] = Field(
        ..., description="Ação direcional recomendada baseada nos dados."
    )
    conviction: int = Field(
        ..., ge=0, le=100, description="Nível de confiança da IA na decisão (0 a 100)."
    )
    reasoning: str = Field(
        ..., description="Justificativa fria e puramente baseada nos dados enviados. Máximo 20 palavras."
    )
