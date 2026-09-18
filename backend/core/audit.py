import decimal
import json
import math
import numbers


def _mapping(value) -> dict:
    """Return `value` when it is a plain dict, else an empty dict.

    `payload.get(key, {})` only defaults on a MISSING key. A key present with an
    explicit `None` (or a string/list) used to raise `AttributeError` on the next
    `.get(...)`, which would abort the audit write for exactly the malformed
    payload an operator most needs recorded.

    `type(value) is dict` rather than `isinstance`: a `dict` subclass can override
    `get`, and an exception from it would propagate out of the snapshot and lose
    the row. A subclass is treated as malformed, which is the safe reading.
    """
    return value if type(value) is dict else {}


def _list_of(value, *, limit: int) -> list:
    """Return at most `limit` items of `value` when it is a plain list, else [].

    Slicing a non-list (`news_risk.get("matched_headlines", [])[:5]`) raises
    `TypeError` for an int/dict/None, which would abort the audit write. A
    string IS sliceable and would silently iterate characters, so require a list.
    `type(..) is list` because a `list` subclass can override `__getitem__` and
    raise from the slice.
    """
    if type(value) is not list:
        return []
    try:
        return value[:limit]
    except Exception:
        return []


def _status_of(block) -> object:
    return _mapping(block).get("status")


# Recursion budget for `json_safe`. The live payload nests about four levels
# (technical_context.rsi.value). This leaves ample headroom while stopping a
# circular reference or a deep bomb from exhausting the interpreter stack and
# aborting the audit write.
MAX_SNAPSHOT_DEPTH = 32


def json_safe(value, _depth: int = 0, _seen: frozenset | None = None):
    """Recursively make `value` serializable by a strict JSON writer.

    The per-field sanitizers above handle the fields that exist today. This is
    the backstop for everything else: any field copied verbatim, and any field
    added later, cannot abort the audit write. It exists because
    `serialize_payload_snapshot` runs `allow_nan=False`, so a single non-finite
    float or a `bytes` object anywhere in the snapshot raises and loses the
    record -- including on the pre-LLM abort path, which is exactly where a
    damaged payload appears.

    Rules: dict keys are stringified, non-finite floats and bytes-like values
    become None (unknown), other non-JSON scalars become their string form, and
    containers are rebuilt element by element. A genuine finite number, string,
    bool or None passes through unchanged.

    Containers deeper than `MAX_SNAPSHOT_DEPTH` or already on the current path
    (a cycle) collapse to None. Both would otherwise raise `RecursionError`,
    which no caller catches, and losing one nested field beats losing the row.

    Every coercion is guarded, including `str()` and `dict.items()`. An object
    whose `__str__`, `__float__` or `items()` raises would otherwise propagate
    out of here and abort the audit write -- the exact outcome this function
    exists to prevent. A value that cannot be coerced becomes None.
    """
    if _depth > MAX_SNAPSHOT_DEPTH:
        return None
    try:
        if value is None or isinstance(value, (str, bool)):
            return value
        if isinstance(value, (int, float)):
            return value if (isinstance(value, int) or math.isfinite(value)) else None
        if isinstance(value, (bytes, bytearray, memoryview)):
            return None

        if isinstance(value, dict) or isinstance(value, (list, tuple, set, frozenset)):
            if _seen is None:
                _seen = frozenset()
            marker = id(value)
            if marker in _seen:
                return None
            _seen = _seen | {marker}
            if isinstance(value, dict):
                return {
                    _safe_key(key): json_safe(item, _depth + 1, _seen)
                    for key, item in value.items()
                }
            return [json_safe(item, _depth + 1, _seen) for item in value]

        return str(value)
    except Exception:
        # A hostile dunder, or any other unexpected failure while coercing one
        # value. Losing this leaf is always better than losing the audit row.
        return None


def _safe_key(key) -> str:
    """Stringify a dict key without letting a hostile `__str__` escape."""
    if isinstance(key, str):
        return key
    try:
        return str(key)
    except Exception:
        return "<unprintable-key>"


def _truncated_text(value, *, limit: int = 240) -> str:
    """Stringify `value` defensively and bound its length.

    `str()` runs arbitrary code, so a hostile `__str__` would propagate out of
    `build_payload_snapshot` and abort the audit write before `json_safe` ever
    runs. None becomes an empty string to keep the stored shape stable.
    """
    if value is None:
        return ""
    try:
        return str(value)[:limit]
    except Exception:
        return "<unprintable>"


def _atr_for_snapshot(atr):
    """Keep the ATR shape the payload used, with any non-finite value stripped.

    The snapshot records the shape deliberately (a scalar stays a scalar, a dict
    stays a dict) because operators compare stored snapshots against the live
    payload. Only the numeric value is sanitized.

    A non-finite scalar must become None, not fall back to the original value:
    `serialize_payload_snapshot` runs with `allow_nan=False`, so returning
    `inf`/`nan` here would make the serializer raise and abort the very audit
    write this function exists to protect. A non-numeric scalar (a string that
    is not a number) is kept verbatim, which is valid JSON and preserves the
    evidence of what the payload contained.
    """
    # Only a plain dict: `"value" not in atr`, `dict(atr)` and `{**atr, ...}` all
    # run subclass hooks (`__contains__`, `__iter__`, `keys`), and a hostile
    # subclass would raise out of `build_payload_snapshot`. A subclass is
    # recorded as malformed rather than trusted.
    if type(atr) is dict:
        if "value" not in atr:
            return dict(atr)
        raw = atr.get("value")
        # `True` is not a number. The gate's `_atr_value` rejects it, so the
        # snapshot must not record it as a calm `1.0` while the gate held for
        # invalid evidence.
        if isinstance(raw, bool):
            return {**atr, "value": None}
        return {**atr, "value": _finite_or_none(raw)}
    if atr is not None and not isinstance(atr, (str, bool, int, float)):
        return None
    if atr is None:
        return None
    if isinstance(atr, bool):
        return None
    if isinstance(atr, (int, float)):
        return _finite_or_none(atr)
    return atr


def _finite_or_none(value):
    """Coerce `value` to a finite float, or None when it is not usable.

    `json.dumps` writes a bare `NaN`/`Infinity` token for a non-finite float.
    That is invalid JSON for strict consumers, and the Electron console parses
    dashboard JSON with `JSON.parse`, so a non-finite price must not reach the
    snapshot. None is the honest "no reading" value; 0.0 would look like a real
    price of zero.

    Only a real numeric type is coerced. `float()` on an arbitrary object can
    run arbitrary code, and a `__float__` that raises anything other than the
    three common exceptions would escape and abort the audit write.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        # `str` is checked before `numbers.Real` so a `str` subclass goes through
        # the guarded `strip`, and the type test is exact so a `float`/`int`
        # subclass with a hostile `__float__` is still coerced inside the guard.
        try:
            if value.strip() == "":
                return None
        except Exception:
            return None
    elif type(value) not in (int, float) and not isinstance(value, decimal.Decimal):
        if not isinstance(value, numbers.Real):
            return None
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def execution_price_from_payload(payload) -> float:
    """Read `technical_context.current_price` without raising on a bad shape.

    Used on the pre-LLM abort path, where a malformed payload is the expected
    reason for the abort. Returns 0.0 when the value is absent or unusable; the
    audit row records the abort either way.
    """
    price = _mapping(_mapping(payload).get("technical_context")).get("current_price", 0.0)
    number = _finite_or_none(price)
    return number if number is not None else 0.0


def build_payload_snapshot(payload: dict) -> dict:
    """Keep the context needed to explain one trade log without storing the full prompt."""
    # `type(..) is dict`: a `dict` subclass may override `get` and raise, which
    # would abort the audit write. The values inside are handled by `_mapping`.
    if type(payload) is not dict:
        payload = {}
    technical = _mapping(payload.get("technical_context"))
    data_health = _mapping(payload.get("data_health"))
    news_risk = _mapping(payload.get("news_risk"))
    portfolio = _mapping(payload.get("portfolio_context"))

    matched_headlines = []
    for item in _list_of(news_risk.get("matched_headlines"), limit=5):
        item = _mapping(item)
        matched_terms = item.get("matched_terms", [])
        if not isinstance(matched_terms, list):
            matched_terms = []
        matched_headlines.append(
            {
                "headline": _truncated_text(item.get("headline")),
                "source": item.get("source"),
                "matched_terms": matched_terms,
            }
        )

    recent_news = []
    for item in _list_of(payload.get("news_context"), limit=5):
        item = _mapping(item)
        recent_news.append(
            {
                "timestamp": _finite_or_none(item.get("timestamp")),
                "source": item.get("source"),
                "headline": _truncated_text(item.get("headline")),
            }
        )

    return {
        "schema_version": 1,
        "technical": {
            # Non-finite numbers are stripped: a bare NaN/Infinity token is
            # invalid JSON for the Electron console's JSON.parse.
            "current_price": _finite_or_none(technical.get("current_price")),
            "rsi_value": _finite_or_none(_mapping(technical.get("rsi")).get("value")),
            "rsi_status": _status_of(technical.get("rsi")),
            "macd_histogram": _finite_or_none(_mapping(technical.get("macd")).get("histogram")),
            "macd_status": _status_of(technical.get("macd")),
            "ema_status": _status_of(technical.get("ema_crossover")),
            "volatility_atr": _atr_for_snapshot(technical.get("volatility_atr")),
        },
        "data_health": {
            "kline_age_seconds": _finite_or_none(data_health.get("kline_age_seconds")),
            "news_age_seconds": _finite_or_none(data_health.get("news_age_seconds")),
            "is_market_data_stale": data_health.get("is_market_data_stale"),
            "is_news_stale": data_health.get("is_news_stale"),
        },
        "news_risk": {
            # No `False` default: an absent flag would be stored as "no red flag",
            # which is unknown rendered as benign. `None` reads as "not reported".
            "has_negative_red_flag": news_risk.get("has_negative_red_flag"),
            "has_untrusted_instruction": news_risk.get("has_untrusted_instruction"),
            "risk_level": news_risk.get("risk_level", "UNKNOWN"),
            "matched_terms": _list_of(news_risk.get("matched_terms"), limit=20),
            "matched_headlines": matched_headlines,
        },
        "portfolio": {
            "current_exposure_percentage": _finite_or_none(portfolio.get("current_exposure_percentage")),
            "is_in_drawdown": portfolio.get("is_in_drawdown"),
            "max_allowed_risk_per_trade": _finite_or_none(portfolio.get("max_allowed_risk_per_trade")),
            "equity_snapshot_id": portfolio.get("equity_snapshot_id"),
            "equity_brl": _finite_or_none(portfolio.get("equity_brl")),
            "daily_reference_equity_brl": _finite_or_none(portfolio.get("daily_reference_equity_brl")),
            "daily_reference_timestamp": _finite_or_none(portfolio.get("daily_reference_timestamp")),
            "daily_drawdown_percentage": _finite_or_none(portfolio.get("daily_drawdown_percentage")),
            "daily_drawdown_limit_percentage": _finite_or_none(portfolio.get("daily_drawdown_limit_percentage")),
        },
        "recent_news": recent_news,
    }


def serialize_payload_snapshot(payload: dict) -> str:
    """Serialize the audit snapshot, never raising on a damaged payload.

    `json_safe` runs first so a shape no field-specific sanitizer anticipated
    (a non-finite float in a verbatim field, a `bytes` value, a set) still
    produces a valid snapshot instead of aborting the audit write. `allow_nan=False`
    then stays as the strict guard against emitting an invalid JSON token.

    The whole build is wrapped as a last resort. Every reader above is already
    defensive, but this function is the only thing standing between a damaged
    payload and an unaudited abort, so a shape nobody anticipated must still
    produce a row. The fallback records the failure instead of the payload.
    """
    try:
        snapshot = json_safe(build_payload_snapshot(payload))
    except Exception:
        snapshot = {"schema_version": 1, "snapshot_error": "unreadable payload"}
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
