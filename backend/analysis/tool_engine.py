import hashlib
import json
import math
import time
from typing import Callable

import pandas as pd

from backend.agents.contracts import (
    AnalysisPlan,
    AnalysisToolRequest,
    AnalysisToolResult,
    DonchianBreakoutRequest,
    DrawdownProfileRequest,
    MultiTimeframeTrendRequest,
    VolumeConfirmationRequest,
)
from backend.core import repository
from backend.features.indicators import get_historical_klines


MAX_CANDLES = 1500
MAX_TOOL_LATENCY_MS = 1000.0
TREND_RETURN_DEADBAND_PCT = 0.15
TREND_EMA_SPREAD_DEADBAND_PCT = 0.02


def _finite(value: float, digits: int = 4) -> float:
    number = float(value)
    if not math.isfinite(number):
        return 0.0
    return round(number, digits)


def _direction(return_pct: float, ema_spread_pct: float) -> str:
    if (
        return_pct >= TREND_RETURN_DEADBAND_PCT
        and ema_spread_pct >= TREND_EMA_SPREAD_DEADBAND_PCT
    ):
        return "BULLISH"
    if (
        return_pct <= -TREND_RETURN_DEADBAND_PCT
        and ema_spread_pct <= -TREND_EMA_SPREAD_DEADBAND_PCT
    ):
        return "BEARISH"
    return "MIXED"


class DeterministicToolEngine:
    """Executes allowlisted read-only calculations and returns compact facts.

    The model can select a contract, but cannot send SQL, code, URLs, arbitrary
    lookbacks, or persistence content. Every query is capped and filtered by
    ``as_of_timestamp`` so historical evaluations cannot see future candles.
    """

    def __init__(
        self,
        *,
        audit: bool = True,
        persist_events: bool = False,
        data_loader: Callable[..., pd.DataFrame] = get_historical_klines,
    ):
        self.audit = audit
        self.persist_events = persist_events
        self.data_loader = data_loader

    def execute_plan(
        self,
        plan: AnalysisPlan | dict,
        *,
        asset: str = "BTC/BRL",
        timeframe: str = "1m",
        as_of_timestamp: int,
    ) -> list[AnalysisToolResult]:
        validated = plan if isinstance(plan, AnalysisPlan) else AnalysisPlan.model_validate(plan)
        if timeframe != "1m":
            return [
                self._error_result(
                    request,
                    as_of_timestamp,
                    "UNSUPPORTED_TIMEFRAME",
                    asset,
                    timeframe,
                )
                for request in validated.requests
            ]

        required = max((self._required_candles(request) for request in validated.requests), default=0)
        if required == 0:
            return []
        required = min(required, MAX_CANDLES)

        try:
            frame = self.data_loader(
                asset=asset,
                timeframe=timeframe,
                limit=required,
                as_of_timestamp=as_of_timestamp,
            )
            if not frame.empty:
                frame = frame[frame["timestamp"] <= as_of_timestamp].copy()
                frame = frame.sort_values("timestamp").tail(MAX_CANDLES).reset_index(drop=True)
        except Exception as error:
            code = f"DATA_LOAD_{type(error).__name__}"[:80]
            return [
                self._error_result(request, as_of_timestamp, code, asset, timeframe)
                for request in validated.requests
            ]

        results = []
        for request in validated.requests:
            started = time.perf_counter()
            try:
                data = self._execute_one(request, frame)
                latency_ms = (time.perf_counter() - started) * 1000.0
                if latency_ms > MAX_TOOL_LATENCY_MS:
                    raise TimeoutError("deterministic tool exceeded latency budget")
                status = "OK" if data is not None else "INSUFFICIENT_DATA"
                result = AnalysisToolResult(
                    tool=request.tool,
                    status=status,
                    as_of_timestamp=as_of_timestamp,
                    data=data or {},
                    latency_ms=round(latency_ms, 3),
                )
            except Exception as error:
                result = AnalysisToolResult(
                    tool=request.tool,
                    status="ERROR",
                    as_of_timestamp=as_of_timestamp,
                    data={},
                    error_code=type(error).__name__[:80],
                    latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
                )

            if result.status == "OK" and request.tool == "drawdown_profile":
                result.data["event_memory"] = self._handle_drawdown_event(
                    asset=asset,
                    as_of_timestamp=as_of_timestamp,
                    metrics=result.data,
                )
            result.audit_persisted = self._audit(request, result, asset, timeframe)
            results.append(result)
        return results

    @staticmethod
    def _required_candles(request: AnalysisToolRequest) -> int:
        if isinstance(request, MultiTimeframeTrendRequest):
            return max(request.windows_minutes) + 1
        if isinstance(request, DonchianBreakoutRequest):
            return request.lookback_candles + 1
        if isinstance(request, DrawdownProfileRequest):
            return request.lookback_minutes
        if isinstance(request, VolumeConfirmationRequest):
            return request.lookback_candles + 1
        raise ValueError("unknown tool request")

    def _execute_one(self, request: AnalysisToolRequest, frame: pd.DataFrame) -> dict | None:
        if isinstance(request, MultiTimeframeTrendRequest):
            return self._multi_timeframe_trend(frame, request.windows_minutes)
        if isinstance(request, DonchianBreakoutRequest):
            return self._donchian_breakout(frame, request.lookback_candles)
        if isinstance(request, DrawdownProfileRequest):
            return self._drawdown_profile(frame, request.lookback_minutes)
        if isinstance(request, VolumeConfirmationRequest):
            return self._volume_confirmation(frame, request.lookback_candles)
        raise ValueError("unknown tool request")

    @staticmethod
    def _multi_timeframe_trend(frame: pd.DataFrame, windows: list[int]) -> dict | None:
        if frame.empty or len(frame) < max(windows):
            return None
        details = []
        votes = {"BULLISH": 0, "BEARISH": 0, "MIXED": 0}
        for window in windows:
            sample = frame.tail(window)
            close = sample["close"].astype(float)
            latest = float(close.iloc[-1])
            first = float(close.iloc[0])
            ema_fast = close.ewm(span=min(9, window), adjust=False).mean()
            ema_slow = close.ewm(span=min(21, window), adjust=False).mean()
            return_pct = ((latest / first) - 1.0) * 100.0 if first else 0.0
            spread_pct = ((float(ema_fast.iloc[-1]) / float(ema_slow.iloc[-1])) - 1.0) * 100.0
            slope_bars = min(5, len(ema_slow) - 1)
            slope_pct = 0.0
            if slope_bars > 0 and latest:
                slope_pct = (
                    (float(ema_slow.iloc[-1]) - float(ema_slow.iloc[-1 - slope_bars]))
                    / latest
                    * 100.0
                    / slope_bars
                )
            trend = _direction(return_pct, spread_pct)
            votes[trend] += 1
            details.append(
                {
                    "window_minutes": window,
                    "return_pct": _finite(return_pct),
                    "ema_spread_pct": _finite(spread_pct),
                    "slow_ema_slope_pct_per_bar": _finite(slope_pct, 6),
                    "trend": trend,
                }
            )
        aligned = "MIXED"
        if votes["BULLISH"] > len(windows) / 2:
            aligned = "BULLISH"
        elif votes["BEARISH"] > len(windows) / 2:
            aligned = "BEARISH"
        return {"alignment": aligned, "windows": details}

    @staticmethod
    def _donchian_breakout(frame: pd.DataFrame, lookback: int) -> dict | None:
        if len(frame) < lookback + 1:
            return None
        prior = frame.iloc[-lookback - 1 : -1]
        current = frame.iloc[-1]
        upper = float(prior["high"].max())
        lower = float(prior["low"].min())
        close = float(current["close"])
        width = upper - lower
        position = ((close - lower) / width) if width > 0 else 0.5
        state = "INSIDE"
        if close > upper:
            state = "BREAKOUT_UP"
        elif close < lower:
            state = "BREAKOUT_DOWN"
        return {
            "lookback_candles": lookback,
            "prior_upper": _finite(upper, 2),
            "prior_lower": _finite(lower, 2),
            "close": _finite(close, 2),
            "channel_position": _finite(position),
            "distance_to_upper_pct": _finite(((upper / close) - 1.0) * 100.0 if close else 0.0),
            "distance_to_lower_pct": _finite(((close / lower) - 1.0) * 100.0 if lower else 0.0),
            "state": state,
        }

    @staticmethod
    def _drawdown_profile(frame: pd.DataFrame, lookback: int) -> dict | None:
        if len(frame) < lookback:
            return None
        sample = frame.tail(lookback).copy()
        close = sample["close"].astype(float)
        cumulative_peak = close.cummax()
        drawdowns = (close / cumulative_peak - 1.0) * 100.0
        trough_position = int(drawdowns.to_numpy().argmin())
        peak_position = int(close.iloc[: trough_position + 1].to_numpy().argmax())
        returns = close.pct_change().dropna()
        downside_returns = returns.clip(upper=0.0)
        downside_semideviation = (
            math.sqrt(float(downside_returns.pow(2).mean())) * 100.0
            if not downside_returns.empty
            else 0.0
        )
        latest_peak_position = int(close.to_numpy().argmax())
        latest_peak = float(close.iloc[latest_peak_position])
        latest = float(close.iloc[-1])
        current_drawdown = ((latest / latest_peak) - 1.0) * 100.0 if latest_peak else 0.0
        max_drawdown = float(drawdowns.iloc[trough_position])
        severity = "NORMAL"
        if max_drawdown <= -7.0:
            severity = "SEVERE"
        elif max_drawdown <= -3.0:
            severity = "ELEVATED"
        elif max_drawdown <= -1.0:
            severity = "WATCH"
        return {
            "lookback_minutes": lookback,
            "current_drawdown_pct": _finite(current_drawdown),
            "max_drawdown_pct": _finite(max_drawdown),
            "downside_semideviation_pct": _finite(downside_semideviation),
            "peak_timestamp": int(sample.iloc[peak_position]["timestamp"]),
            "trough_timestamp": int(sample.iloc[trough_position]["timestamp"]),
            "peak_price": _finite(float(close.iloc[peak_position]), 2),
            "trough_price": _finite(float(close.iloc[trough_position]), 2),
            "severity": severity,
        }

    @staticmethod
    def _volume_confirmation(frame: pd.DataFrame, lookback: int) -> dict | None:
        if len(frame) < lookback + 1:
            return None
        prior = frame.iloc[-lookback - 1 : -1]
        current = frame.iloc[-1]
        prior_volume = prior["volume"].astype(float)
        mean = float(prior_volume.mean())
        std = float(prior_volume.std(ddof=0))
        current_volume = float(current["volume"])
        z_score = (current_volume - mean) / std if std > 0 else 0.0
        signed_volume = prior_volume.copy()
        close_delta = prior["close"].astype(float).diff().fillna(0.0)
        signed_volume.loc[close_delta < 0] *= -1.0
        signed_volume.loc[close_delta == 0] = 0.0
        obv = signed_volume.cumsum()
        obv_slope = float(obv.iloc[-1] - obv.iloc[0]) / max(1, len(obv) - 1)
        direction = "NEUTRAL"
        if z_score >= 2.0:
            direction = "HIGH_VOLUME"
        elif z_score <= -1.0:
            direction = "LOW_VOLUME"
        return {
            "lookback_candles": lookback,
            "current_volume": _finite(current_volume),
            "mean_volume": _finite(mean),
            "volume_z_score": _finite(z_score),
            "obv_slope_per_bar": _finite(obv_slope),
            "state": direction,
        }

    def _handle_drawdown_event(self, *, asset: str, as_of_timestamp: int, metrics: dict) -> dict:
        severity = metrics.get("severity", "NORMAL")
        candidate = severity in {"ELEVATED", "SEVERE"}
        response = {"candidate": candidate, "persistence": "DISABLED"}
        if not candidate or not self.persist_events:
            return response
        peak_timestamp = int(metrics["peak_timestamp"])
        trough_timestamp = int(metrics["trough_timestamp"])
        raw_key = f"{asset}|DRAWDOWN|{peak_timestamp}|{trough_timestamp}|{severity}"
        dedupe_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        try:
            inserted = repository.add_market_event(
                {
                    "asset": asset,
                    "event_type": "DRAWDOWN",
                    "event_timestamp": trough_timestamp,
                    "detected_at": int(time.time()),
                    "severity": severity,
                    "metrics_json": json.dumps(metrics, sort_keys=True, separators=(",", ":")),
                    "dedupe_key": dedupe_key,
                }
            )
            response["persistence"] = "INSERTED" if inserted else "DEDUPLICATED"
        except Exception as error:
            response["persistence"] = "ERROR"
            response["error_code"] = type(error).__name__[:80]
        return response

    def _audit(
        self,
        request: AnalysisToolRequest,
        result: AnalysisToolResult,
        asset: str,
        timeframe: str,
    ) -> bool | None:
        if not self.audit:
            return None
        try:
            repository.add_analysis_tool_call(
                {
                    "timestamp": int(time.time()),
                    "as_of_timestamp": result.as_of_timestamp,
                    "asset": asset,
                    "timeframe": timeframe,
                    "tool_name": request.tool,
                    "request_json": request.model_dump_json(),
                    "status": result.status,
                    "result_json": json.dumps(result.data, sort_keys=True, separators=(",", ":")),
                    "latency_ms": result.latency_ms,
                    "error_code": result.error_code,
                }
            )
            return True
        except Exception:
            # Analytical output remains usable if audit storage is unavailable.
            # The caller can detect database health separately and RiskManager
            # remains the only component allowed to approve a paper action.
            return False

    def _error_result(
        self,
        request: AnalysisToolRequest,
        as_of_timestamp: int,
        error_code: str,
        asset: str,
        timeframe: str,
    ) -> AnalysisToolResult:
        result = AnalysisToolResult(
            tool=request.tool,
            status="ERROR",
            as_of_timestamp=as_of_timestamp,
            error_code=error_code[:80],
            latency_ms=0.0,
        )
        result.audit_persisted = self._audit(request, result, asset, timeframe)
        return result
