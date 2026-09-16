import math
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class StrictToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MultiTimeframeTrendRequest(StrictToolContract):
    tool: Literal["multi_timeframe_trend"]
    windows_minutes: list[Literal[5, 15, 30, 60, 240]] = Field(
        default=[15, 60, 240], min_length=1, max_length=4
    )

    @field_validator("windows_minutes")
    @classmethod
    def reject_duplicate_windows(cls, value: list[int]):
        if len(value) != len(set(value)):
            raise ValueError("trend windows cannot repeat")
        return value


class DonchianBreakoutRequest(StrictToolContract):
    tool: Literal["donchian_breakout"]
    lookback_candles: Literal[20, 55] = 20


class DrawdownProfileRequest(StrictToolContract):
    tool: Literal["drawdown_profile"]
    lookback_minutes: Literal[60, 240, 1440] = 240


class VolumeConfirmationRequest(StrictToolContract):
    tool: Literal["volume_confirmation"]
    lookback_candles: Literal[20, 60, 240] = 60


AnalysisToolRequest = Annotated[
    Union[
        MultiTimeframeTrendRequest,
        DonchianBreakoutRequest,
        DrawdownProfileRequest,
        VolumeConfirmationRequest,
    ],
    Field(discriminator="tool"),
]


class AnalysisPlan(StrictToolContract):
    """Bounded requests selected by the LLM; execution remains deterministic."""

    requests: list[AnalysisToolRequest] = Field(default_factory=list, max_length=3)
    rationale: str = Field(default="", max_length=240)

    @field_validator("requests")
    @classmethod
    def reject_duplicate_tools(cls, value: list[AnalysisToolRequest]):
        names = [request.tool for request in value]
        if len(names) != len(set(names)):
            raise ValueError("analysis plan cannot request the same tool twice")
        return value


class AnalysisToolResult(StrictToolContract):
    tool: Literal[
        "multi_timeframe_trend",
        "donchian_breakout",
        "drawdown_profile",
        "volume_confirmation",
    ]
    status: Literal["OK", "INSUFFICIENT_DATA", "ERROR"]
    as_of_timestamp: int
    data: dict = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=80)
    latency_ms: float = Field(ge=0)
    audit_persisted: bool | None = None


class NewsAnalysis(StrictToolContract):
    status: Literal["OK", "NO_NEWS", "DEGRADED"]
    bias: Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "UNCERTAIN"]
    confidence: int = Field(ge=0, le=100)
    summary: str = Field(
        max_length=480,
        description=(
            "One short paragraph of at most 480 characters (roughly 70 words). "
            "State relevance, direction and uncertainty concisely; do not repeat "
            "headline text and do not exceed the limit."
        ),
    )
    evidence_news_ids: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "IDs copied from the supplied news_context records. Never invent ids "
            "and never include prose. Empty when status is NO_NEWS."
        ),
    )
    conflicts: list[str] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "At most 3 very short phrases (under 80 characters each) naming "
            "conflicting signals. Never write full paragraphs here."
        ),
    )
    gaps: list[str] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "At most 3 very short phrases (under 80 characters each) naming "
            "missing information, such as 'no prices in headlines'. Never write "
            "full paragraphs here."
        ),
    )
    untrusted_instruction_detected: bool = False


class TechnicalAnalysis(StrictToolContract):
    status: Literal["OK", "INSUFFICIENT_DATA", "DEGRADED"]
    regime: Literal[
        "UPTREND",
        "DOWNTREND",
        "SIDEWAYS",
        "HIGH_VOLATILITY",
        "MIXED",
        "INSUFFICIENT_DATA",
    ]
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL", "UNCERTAIN"]
    confidence: int = Field(ge=0, le=100)
    summary: str = Field(
        max_length=480,
        description=(
            "One short paragraph of at most 480 characters (roughly 70 words). "
            "Keep it under 350 characters when possible and never exceed the limit."
        ),
    )
    evidence_fields: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Field paths into technical_context that support the assessment, "
            "written as dot-paths only -- for example 'rsi.status', "
            "'macd.histogram', 'ema.ema9', 'volatility_atr.value'. "
            "Allowed roots: current_price, status, returns, trend, rsi, macd, "
            "ema, ema_crossover, volatility, volatility_atr, volume, "
            "volume_profile, bollinger_bands, drawdown, range, data_quality, "
            "news, news_alignment. Put reasoning in summary; never write "
            "sentences or values in this list."
        ),
    )
    counter_evidence: list[str] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "Same dot-path format as evidence_fields, for facts that contradict "
            "the assessment. Never write sentences, values or explanations here."
        ),
    )
    news_alignment: Literal["ALIGNED", "CONFLICTING", "PARTIAL", "UNRELATED", "UNAVAILABLE"]
    invalidation_conditions: list[str] = Field(default_factory=list, max_length=4)


class MultiAgentDecision(StrictToolContract):
    action: Literal["BUY", "SELL", "HOLD"]
    conviction: int = Field(ge=0, le=80)
    thesis: str = Field(
        max_length=360,
        description=(
            "Why this action follows from the evidence, in at most 360 "
            "characters (roughly 50 words). This is the only place for "
            "reasoning; keep it concise and never exceed the limit."
        ),
    )
    evidence_fields: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Field paths into the accepted inputs that support the action, "
            "written as dot-paths only -- for example "
            "'technical_context.rsi.status', 'news_report.bias', "
            "'technical_report.direction', "
            "'original_snapshot.data_health.is_news_stale'. "
            "Allowed roots: original_snapshot, news_report, technical_report, "
            "data_health, news_risk, portfolio_context, technical_context. "
            "Put reasoning in thesis; never write sentences or values here."
        ),
    )
    counter_evidence: list[str] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "Same dot-path format as evidence_fields, for facts that contradict "
            "the proposal. Put reasoning in thesis; never write sentences, "
            "values or explanations here."
        ),
    )
    invalidation_conditions: list[str] = Field(default_factory=list, max_length=4)
