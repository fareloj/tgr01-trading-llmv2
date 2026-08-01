import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DecisionOutput(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"] = Field(
        ..., description="Acao direcional recomendada baseada nos dados."
    )
    conviction: int = Field(
        ..., ge=0, le=100, description="Nivel de confianca da IA na decisao (0 a 100)."
    )
    reasoning: str = Field(
        ..., description="Justificativa curta e baseada nos dados enviados. Maximo 20 palavras."
    )
    decision_brief: str = Field(
        ...,
        max_length=420,
        description=(
            "Resumo humano em ate 3 linhas explicando por que escolheu a acao "
            "e quais dados do payload sustentam a decisao."
        ),
    )

    @field_validator("conviction", mode="before")
    @classmethod
    def normalize_fractional_conviction(cls, value):
        """Normalize JSON floats conservatively without increasing risk."""
        if isinstance(value, bool):
            raise ValueError("conviction must be numeric, not boolean")
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("conviction must be finite")
            return math.floor(value)
        return value

    @field_validator("decision_brief")
    @classmethod
    def decision_brief_has_at_most_three_lines(cls, value: str) -> str:
        lines = [line.strip() for line in (value or "").splitlines() if line.strip()]
        if len(lines) > 3:
            raise ValueError("decision_brief must have at most 3 non-empty lines")
        return "\n".join(lines)
