from backend.core.runtime_safety import assess_worker_heartbeats


def _rows(*, price=990, news=950):
    rows = []
    if price is not None:
        rows.append({"worker_name": "price_worker", "last_heartbeat": price})
    if news is not None:
        rows.append({"worker_name": "news_worker", "last_heartbeat": news})
    return rows


def test_required_workers_are_healthy_only_when_both_are_present_and_fresh():
    result = assess_worker_heartbeats(_rows(), now=1000)

    assert result.healthy is True
    assert result.ages_seconds == {"price_worker": 10, "news_worker": 50}
    assert result.failures == ()


def test_missing_worker_fails_closed():
    result = assess_worker_heartbeats(_rows(news=None), now=1000)

    assert result.healthy is False
    assert "news_worker sem heartbeat" in result.failures[0]


def test_stale_future_and_malformed_heartbeats_fail_closed():
    stale = assess_worker_heartbeats(_rows(price=600), now=1000)
    future = assess_worker_heartbeats(_rows(price=1100), now=1000)
    malformed = assess_worker_heartbeats(
        [
            {"worker_name": "price_worker", "last_heartbeat": "invalid"},
            {"worker_name": "news_worker", "last_heartbeat": 990},
        ],
        now=1000,
    )

    assert stale.healthy is False
    assert "stale" in stale.failures[0]
    assert future.healthy is False
    assert "no futuro" in future.failures[0]
    assert malformed.healthy is False
    assert "malformado" in malformed.failures[0]
