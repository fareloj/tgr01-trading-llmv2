from backend.tests.find_market_windows import find_windows


def test_window_finder_scales_over_sparse_timestamp_ranges():
    rows = [
        {
            "timestamp": 1_800_000_000 + index * 60,
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100 + index,
            "volume": 1,
        }
        for index in range(180)
    ]

    windows = find_windows(
        rows,
        window_minutes=60,
        stride_minutes=10,
        trend_threshold_pct=0.5,
        sideways_threshold_pct=0.1,
        min_coverage_pct=80,
    )

    assert len(windows) == 12
    assert all(window.candles == 61 for window in windows)
    assert all(window.label == "UPTREND" for window in windows)
