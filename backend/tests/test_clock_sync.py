import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from core import clock_sync


def test_clock_skew_uses_median_sample(monkeypatch):
    monkeypatch.setattr(
        clock_sync,
        "fetch_http_clock_samples",
        lambda **kwargs: [
            {"url": "a", "skew_seconds": 10},
            {"url": "b", "skew_seconds": 14},
        ],
    )

    result = clock_sync.check_clock_skew(max_skew_seconds=300)

    assert result["status"] == "OK"
    assert result["skew_seconds"] == 12
    assert result["is_within_tolerance"] is True


def test_clock_skew_blocks_large_drift(monkeypatch):
    monkeypatch.setattr(
        clock_sync,
        "fetch_http_clock_samples",
        lambda **kwargs: [{"url": "a", "skew_seconds": -3600}],
    )

    result = clock_sync.check_clock_skew(max_skew_seconds=300)

    assert result["status"] == "CLOCK_SKEW"
    assert result["is_within_tolerance"] is False


def test_clock_skew_reports_unavailable(monkeypatch):
    monkeypatch.setattr(
        clock_sync,
        "fetch_http_clock_samples",
        lambda **kwargs: [{"url": "a", "error": "offline"}],
    )

    result = clock_sync.check_clock_skew(max_skew_seconds=300)

    assert result["status"] == "UNAVAILABLE"
    assert result["is_within_tolerance"] is False
