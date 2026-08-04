from __future__ import annotations

import hashlib
import json
from bisect import bisect_left
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence


REGIME_EXPECTED_ACTION = {
    "UPTREND": "BUY",
    "DOWNTREND": "SELL",
    "SIDEWAYS": "HOLD",
    "HIGH_VOLATILITY": None,
}


def conviction_bucket(value: float) -> str:
    if value < 50:
        return "0-49"
    if value < 60:
        return "50-59"
    if value < 70:
        return "60-69"
    return "70-80"


@dataclass(frozen=True)
class FrozenWindow:
    id: str
    regime: str
    source_label: str
    start_ts: int
    end_ts: int
    start_price: float
    end_price: float
    move_pct: float
    volatility_pct: float
    candles: int
    expected_action: str | None
    cycle_timestamps: tuple[int, ...]

    def to_dict(self) -> dict:
        item = asdict(self)
        item["cycle_timestamps"] = list(self.cycle_timestamps)
        return item


def _overlaps(left, right) -> bool:
    return int(left.start_ts) <= int(right.end_ts) and int(right.start_ts) <= int(left.end_ts)


def select_non_overlapping_windows(
    windows: Sequence,
    *,
    per_regime: int,
    include_high_volatility: bool = False,
    strategy: str = "extreme",
) -> list[tuple[str, object]]:
    """Select deterministic, non-overlapping examples from preclassified windows."""
    if per_regime <= 0:
        raise ValueError("per_regime must be positive")
    if strategy not in {"extreme", "stratified"}:
        raise ValueError("strategy must be extreme or stratified")

    ranked = {
        "UPTREND": sorted(
            (item for item in windows if item.label == "UPTREND"),
            key=lambda item: (-item.move_pct, -item.volatility_pct, item.start_ts),
        ),
        "DOWNTREND": sorted(
            (item for item in windows if item.label == "DOWNTREND"),
            key=lambda item: (item.move_pct, -item.volatility_pct, item.start_ts),
        ),
        "SIDEWAYS": sorted(
            (item for item in windows if item.label == "SIDEWAYS"),
            key=lambda item: (abs(item.move_pct), item.volatility_pct, item.start_ts),
        ),
    }

    selected: list[tuple[str, object]] = []
    used = []
    for regime in ("UPTREND", "DOWNTREND", "SIDEWAYS"):
        candidates = ranked[regime]
        count = 0
        if strategy == "stratified" and candidates:
            if regime == "UPTREND":
                candidates = sorted(candidates, key=lambda item: (item.move_pct, item.volatility_pct, item.start_ts))
            elif regime == "DOWNTREND":
                candidates = sorted(candidates, key=lambda item: (abs(item.move_pct), item.volatility_pct, item.start_ts))
            else:
                candidates = sorted(candidates, key=lambda item: (item.volatility_pct, abs(item.move_pct), item.start_ts))
            for index in range(per_regime):
                start = index * len(candidates) // per_regime
                end = (index + 1) * len(candidates) // per_regime
                band = candidates[start:end]
                midpoint = (len(band) - 1) / 2
                for band_index, candidate in sorted(
                    enumerate(band), key=lambda item: (abs(item[0] - midpoint), item[0])
                ):
                    if any(_overlaps(candidate, existing) for existing in used):
                        continue
                    selected.append((regime, candidate))
                    used.append(candidate)
                    count += 1
                    break

        for candidate in candidates:
            if count >= per_regime:
                break
            if any(_overlaps(candidate, existing) for existing in used):
                continue
            selected.append((regime, candidate))
            used.append(candidate)
            count += 1

    if include_high_volatility:
        candidates = sorted(
            windows,
            key=lambda item: (-item.volatility_pct, -abs(item.move_pct), item.start_ts),
        )
        count = 0
        for candidate in candidates:
            if any(_overlaps(candidate, existing) for existing in used):
                continue
            selected.append(("HIGH_VOLATILITY", candidate))
            used.append(candidate)
            count += 1
            if count >= per_regime:
                break
    return selected


def select_cycle_timestamps(
    candle_timestamps: Iterable[int],
    *,
    start_ts: int,
    end_ts: int,
    cycles: int,
    step_seconds: int,
) -> tuple[int, ...]:
    if cycles <= 0:
        raise ValueError("cycles must be positive")
    if step_seconds <= 0:
        raise ValueError("step_seconds must be positive")

    available = sorted({int(ts) for ts in candle_timestamps if start_ts <= int(ts) <= end_ts})
    selected = []
    next_allowed = int(start_ts)
    for timestamp in available:
        if timestamp < next_allowed:
            continue
        selected.append(timestamp)
        next_allowed = timestamp + step_seconds
        if len(selected) >= cycles:
            break
    return tuple(selected)


def freeze_windows(
    selected: Sequence[tuple[str, object]],
    candle_timestamps: Iterable[int],
    *,
    cycles: int,
    step_seconds: int,
) -> list[FrozenWindow]:
    frozen = []
    all_timestamps = tuple(candle_timestamps)
    counters: Counter[str] = Counter()
    for regime, item in selected:
        counters[regime] += 1
        cycle_timestamps = select_cycle_timestamps(
            all_timestamps,
            start_ts=int(item.start_ts),
            end_ts=int(item.end_ts),
            cycles=cycles,
            step_seconds=step_seconds,
        )
        frozen.append(
            FrozenWindow(
                id=f"{regime.lower()}-{counters[regime]:02d}",
                regime=regime,
                source_label=str(item.label),
                start_ts=int(item.start_ts),
                end_ts=int(item.end_ts),
                start_price=float(item.start_price),
                end_price=float(item.end_price),
                move_pct=float(item.move_pct),
                volatility_pct=float(item.volatility_pct),
                candles=int(item.candles),
                expected_action=REGIME_EXPECTED_ACTION[regime],
                cycle_timestamps=cycle_timestamps,
            )
        )
    return frozen


def campaign_fingerprint(config: dict, windows: Sequence[FrozenWindow], variants: Sequence[dict]) -> str:
    stable = {
        "config": config,
        "windows": [window.to_dict() for window in windows],
        "variants": list(variants),
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def find_future_candle(
    candles: Sequence[dict],
    *,
    decision_timestamp: int,
    horizon_minutes: int,
    max_delay_seconds: int,
) -> tuple[str, dict | None]:
    if not candles:
        return "not_matured", None
    timestamps = [int(item["timestamp"]) for item in candles]
    target = int(decision_timestamp) + (int(horizon_minutes) * 60)
    index = bisect_left(timestamps, target)
    if index < len(candles) and timestamps[index] <= target + max_delay_seconds:
        return "matured", candles[index]
    if timestamps[-1] < target:
        return "not_matured", None
    return "data_gap", None


def round_trip_cost_pct(*, fee_rate: float, slippage_rate: float) -> float:
    if fee_rate < 0 or slippage_rate < 0:
        raise ValueError("cost rates cannot be negative")
    return 2.0 * (float(fee_rate) + float(slippage_rate)) * 100.0


def one_way_cost_pct(*, fee_rate: float, slippage_rate: float) -> float:
    if fee_rate < 0 or slippage_rate < 0:
        raise ValueError("cost rates cannot be negative")
    return (float(fee_rate) + float(slippage_rate)) * 100.0


def classify_action_after_costs(
    action: str,
    *,
    raw_move_pct: float,
    buy_cost_pct: float,
    sell_cost_pct: float,
    threshold_pct: float,
) -> dict:
    action = str(action).upper()
    if threshold_pct < 0:
        raise ValueError("threshold_pct cannot be negative")

    if action == "BUY":
        edge = raw_move_pct - buy_cost_pct
        action_cost = buy_cost_pct
        status = "good" if edge >= threshold_pct else "bad" if edge <= -threshold_pct else "neutral"
    elif action == "SELL":
        # SELL is a reduction of an existing long position, not a synthetic short.
        edge = -raw_move_pct - sell_cost_pct
        action_cost = sell_cost_pct
        status = "good" if edge >= threshold_pct else "bad" if edge <= -threshold_pct else "neutral"
    elif action == "HOLD":
        edge = 0.0
        action_cost = 0.0
        if raw_move_pct >= threshold_pct + buy_cost_pct:
            status = "missed_upside"
        elif raw_move_pct <= -(threshold_pct + sell_cost_pct):
            status = "avoided_downside"
        else:
            status = "good"
    else:
        edge = 0.0
        action_cost = 0.0
        status = "not_applicable"

    return {
        "status": status,
        "directional_edge_after_cost_pct": round(edge, 6),
        "estimated_action_cost_pct": round(action_cost, 6),
        "buy_round_trip_hurdle_pct": round(threshold_pct + buy_cost_pct, 6),
        "sell_exit_hurdle_pct": round(threshold_pct + sell_cost_pct, 6),
    }


def evaluate_result_horizons(
    result: dict,
    candles: Sequence[dict],
    *,
    horizons: Sequence[int],
    threshold_pct: float,
    fee_rate: float,
    slippage_rate: float,
    max_delay_seconds: int,
) -> dict:
    evaluated = {}
    base_price = float(result.get("price") or 0.0)
    buy_cost_pct = round_trip_cost_pct(fee_rate=fee_rate, slippage_rate=slippage_rate)
    sell_cost_pct = one_way_cost_pct(fee_rate=fee_rate, slippage_rate=slippage_rate)
    for horizon in horizons:
        maturity, future = find_future_candle(
            candles,
            decision_timestamp=int(result["timestamp"]),
            horizon_minutes=int(horizon),
            max_delay_seconds=max_delay_seconds,
        )
        if base_price <= 0:
            evaluated[str(horizon)] = {"maturity": "not_matured", "reason": "invalid_base_price"}
            continue
        if maturity != "matured" or future is None:
            evaluated[str(horizon)] = {
                "maturity": maturity,
                "target_timestamp": int(result["timestamp"]) + int(horizon) * 60,
            }
            continue

        future_price = float(future["close"])
        raw_move_pct = ((future_price - base_price) / base_price) * 100.0
        llm_eval = classify_action_after_costs(
            result["llm_action"],
            raw_move_pct=raw_move_pct,
            buy_cost_pct=buy_cost_pct,
            sell_cost_pct=sell_cost_pct,
            threshold_pct=threshold_pct,
        )
        risk_eval = classify_action_after_costs(
            result["risk_action"],
            raw_move_pct=raw_move_pct,
            buy_cost_pct=buy_cost_pct,
            sell_cost_pct=sell_cost_pct,
            threshold_pct=threshold_pct,
        )
        executed_size = float(result.get("executed_size") or 0.0)
        risk_eval["size_weighted_edge_pct"] = round(
            risk_eval["directional_edge_after_cost_pct"] * executed_size / 100.0,
            6,
        )
        evaluated[str(horizon)] = {
            "maturity": "matured",
            "future_timestamp": int(future["timestamp"]),
            "future_price": round(future_price, 2),
            "raw_move_pct": round(raw_move_pct, 6),
            "llm": llm_eval,
            "risk": risk_eval,
        }
    return evaluated


def summarize_results(results: Sequence[dict], horizons: Sequence[int]) -> dict:
    summary = {}
    variants = sorted({str(item["variant"]) for item in results})
    for variant in variants:
        variant_rows = [item for item in results if item["variant"] == variant and item.get("status") == "OK"]
        variant_summary = {
            "samples": len(variant_rows),
            "errors": sum(1 for item in results if item["variant"] == variant and item.get("status") != "OK"),
            "technical_failures": sum(bool(item.get("llm_technical_failure")) for item in variant_rows),
            "llm_actions": dict(Counter(item["llm_action"] for item in variant_rows)),
            "risk_actions": dict(Counter(item["risk_action"] for item in variant_rows)),
            "llm_to_risk": dict(Counter(f"{item['llm_action']}->{item['risk_action']}" for item in variant_rows)),
            "regime_alignment": {},
            "horizons": {},
        }
        for regime in sorted({item["regime"] for item in variant_rows}):
            rows = [item for item in variant_rows if item["regime"] == regime]
            expected = rows[0].get("expected_action")
            variant_summary["regime_alignment"][regime] = {
                "expected_action": expected,
                "samples": len(rows),
                "llm_matches": sum(item["llm_action"] == expected for item in rows) if expected else None,
                "risk_matches": sum(item["risk_action"] == expected for item in rows) if expected else None,
            }

        for horizon in horizons:
            key = str(horizon)
            bucket = Counter()
            directional_edges = []
            weighted_edges = []
            directional_good = 0
            directional_bad = 0
            directional_neutral = 0
            calibration: dict[str, Counter] = {}
            for item in variant_rows:
                evaluation = item.get("horizons", {}).get(key, {})
                maturity = evaluation.get("maturity", "not_matured")
                bucket[maturity] += 1
                if maturity != "matured":
                    continue
                risk_eval = evaluation["risk"]
                bucket[risk_eval["status"]] += 1
                confidence_key = conviction_bucket(float(item.get("llm_conviction") or 0.0))
                calibration.setdefault(confidence_key, Counter())[evaluation["llm"]["status"]] += 1
                if item["risk_action"] in {"BUY", "SELL"}:
                    directional_edges.append(float(risk_eval["directional_edge_after_cost_pct"]))
                    weighted_edges.append(float(risk_eval["size_weighted_edge_pct"]))
                    directional_good += risk_eval["status"] == "good"
                    directional_bad += risk_eval["status"] == "bad"
                    directional_neutral += risk_eval["status"] == "neutral"
            precision_denominator = directional_good + directional_bad
            calibration_report = {}
            for confidence_key, counts in sorted(calibration.items()):
                decisive = counts["good"] + counts["bad"]
                calibration_report[confidence_key] = {
                    "samples": sum(counts.values()),
                    **dict(counts),
                    "good_rate_among_good_bad": round(counts["good"] / decisive, 4) if decisive else None,
                }
            variant_summary["horizons"][key] = {
                **dict(bucket),
                "directional_samples": len(directional_edges),
                "directional_good": directional_good,
                "directional_bad": directional_bad,
                "directional_neutral": directional_neutral,
                "directional_coverage": (
                    round(len(directional_edges) / bucket["matured"], 4) if bucket["matured"] else None
                ),
                "directional_precision": (
                    round(directional_good / precision_denominator, 4) if precision_denominator else None
                ),
                "llm_conviction_calibration": calibration_report,
                "average_directional_edge_after_cost_pct": (
                    round(sum(directional_edges) / len(directional_edges), 6) if directional_edges else None
                ),
                "average_size_weighted_edge_pct": (
                    round(sum(weighted_edges) / len(weighted_edges), 6) if weighted_edges else None
                ),
            }
        summary[variant] = variant_summary
    return summary
