from __future__ import annotations

from collections.abc import Callable

import pandas as pd


PredictionFunction = Callable[[pd.DataFrame], pd.Series]
LABELS = ("BUY", "HOLD", "SELL")


def always_hold(frame: pd.DataFrame) -> pd.Series:
    return pd.Series("HOLD", index=frame.index, dtype="object")


def momentum_60(frame: pd.DataFrame, threshold_pct: float = 0.30) -> pd.Series:
    prediction = pd.Series("HOLD", index=frame.index, dtype="object")
    prediction = prediction.mask(frame["return_60m_pct"] >= threshold_pct, "BUY")
    return prediction.mask(frame["return_60m_pct"] <= -threshold_pct, "SELL")


def trend_confirmation(frame: pd.DataFrame) -> pd.Series:
    prediction = pd.Series("HOLD", index=frame.index, dtype="object")
    bullish = (frame["ema_9_21_spread_pct"] > 0.02) & (frame["macd_hist_pct"] > 0)
    bearish = (frame["ema_9_21_spread_pct"] < -0.02) & (frame["macd_hist_pct"] < 0)
    prediction = prediction.mask(bullish, "BUY")
    return prediction.mask(bearish, "SELL")


def rsi_mean_reversion(frame: pd.DataFrame) -> pd.Series:
    prediction = pd.Series("HOLD", index=frame.index, dtype="object")
    prediction = prediction.mask(frame["rsi_14"] <= 30.0, "BUY")
    return prediction.mask(frame["rsi_14"] >= 70.0, "SELL")


BASELINES: dict[str, PredictionFunction] = {
    "always_hold": always_hold,
    "momentum_60": momentum_60,
    "trend_confirmation": trend_confirmation,
    "rsi_mean_reversion": rsi_mean_reversion,
}


def _macro_metrics(actual: pd.Series, predicted: pd.Series) -> dict[str, float]:
    precisions = []
    recalls = []
    f1_values = []
    for label in LABELS:
        true_positive = int(((actual == label) & (predicted == label)).sum())
        false_positive = int(((actual != label) & (predicted == label)).sum())
        false_negative = int(((actual == label) & (predicted != label)).sum())
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        precisions.append(precision)
        recalls.append(recall)
        f1_values.append(f1)
    return {
        "macro_precision": round(sum(precisions) / len(precisions), 6),
        "macro_recall": round(sum(recalls) / len(recalls), 6),
        "macro_f1": round(sum(f1_values) / len(f1_values), 6),
    }


def _maximum_drawdown_pct(net_returns_pct: pd.Series) -> float:
    if net_returns_pct.empty:
        return 0.0
    equity = (1.0 + (net_returns_pct / 100.0)).cumprod()
    running_peak = equity.cummax()
    drawdown = ((equity / running_peak) - 1.0) * 100.0
    return round(float(drawdown.min()), 6)


def evaluate_predictions(
    frame: pd.DataFrame,
    predicted: pd.Series,
    *,
    horizon_minutes: int,
    round_trip_cost_pct: float,
) -> dict:
    if len(frame) != len(predicted):
        raise ValueError("predictions must have the same length as the evaluation frame")
    if round_trip_cost_pct < 0:
        raise ValueError("round_trip_cost_pct must be non-negative")
    actual = frame[f"label_{horizon_minutes}m"].astype(str).reset_index(drop=True)
    predicted = predicted.astype(str).reset_index(drop=True)
    if not set(predicted.unique()).issubset(LABELS):
        raise ValueError("predictions contain unsupported actions")

    if "timestamp" not in frame.columns:
        raise ValueError("evaluation frame must contain timestamp")
    timestamps = frame["timestamp"].reset_index(drop=True)
    future_return = frame[f"future_return_{horizon_minutes}m_pct"].reset_index(drop=True)
    direction = predicted.map({"BUY": 1.0, "HOLD": 0.0, "SELL": -1.0})
    signaled = direction != 0.0
    executed = pd.Series(False, index=predicted.index)
    next_available_timestamp = -1
    for index in predicted.index:
        if signaled.iloc[index] and int(timestamps.iloc[index]) >= next_available_timestamp:
            executed.iloc[index] = True
            next_available_timestamp = int(timestamps.iloc[index]) + (horizon_minutes * 60)

    net_return = pd.Series(0.0, index=predicted.index)
    net_return.loc[executed] = (
        direction.loc[executed] * future_return.loc[executed] - round_trip_cost_pct
    )
    trade_returns = net_return.loc[executed]

    confusion = {
        actual_label: {
            predicted_label: int(((actual == actual_label) & (predicted == predicted_label)).sum())
            for predicted_label in LABELS
        }
        for actual_label in LABELS
    }
    result = {
        "rows": len(frame),
        "accuracy": round(float((actual == predicted).mean()), 6),
        **_macro_metrics(actual, predicted),
        "signal_count": int(signaled.sum()),
        "trade_count": int(executed.sum()),
        "overlap_skipped": int((signaled & ~executed).sum()),
        "trade_rate": round(float(executed.mean()), 6),
        "trade_direction_accuracy": round(
            float((actual.loc[executed] == predicted.loc[executed]).mean()) if executed.any() else 0.0,
            6,
        ),
        "win_rate_after_cost": round(float((trade_returns > 0).mean()) if not trade_returns.empty else 0.0, 6),
        "average_trade_return_pct": round(float(trade_returns.mean()) if not trade_returns.empty else 0.0, 6),
        "compounded_strategy_return_pct": round(float(((1.0 + net_return / 100.0).prod() - 1.0) * 100.0), 6),
        "maximum_drawdown_pct": _maximum_drawdown_pct(net_return),
        "confusion_matrix": confusion,
    }
    return result


def evaluate_baselines(
    frame: pd.DataFrame,
    *,
    horizon_minutes: int,
    round_trip_cost_pct: float,
) -> dict[str, dict]:
    return {
        name: evaluate_predictions(
            frame,
            predictor(frame),
            horizon_minutes=horizon_minutes,
            round_trip_cost_pct=round_trip_cost_pct,
        )
        for name, predictor in BASELINES.items()
    }
