from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Mapping


REQUIRED_WORKERS = {
    "price_worker": 300,
    "news_worker": 3600,
}
MAX_FUTURE_HEARTBEAT_SECONDS = 30
MAX_FUTURE_MARKET_DATA_SECONDS = 30


@dataclass(frozen=True)
class WorkerHealthAssessment:
    healthy: bool
    ages_seconds: dict[str, int]
    failures: tuple[str, ...]


def assess_worker_heartbeats(
    rows: Iterable[Mapping[str, object]],
    *,
    now: int | None = None,
) -> WorkerHealthAssessment:
    """Validate every required worker heartbeat against one shared contract."""
    current_time = int(time.time() if now is None else now)
    heartbeats: dict[str, int] = {}
    malformed: set[str] = set()
    for row in rows:
        worker_name = str(row.get("worker_name", ""))
        if worker_name not in REQUIRED_WORKERS:
            continue
        try:
            heartbeats[worker_name] = int(row["last_heartbeat"])
        except (KeyError, TypeError, ValueError, OverflowError):
            malformed.add(worker_name)

    failures = []
    ages = {}
    for worker_name, max_age in REQUIRED_WORKERS.items():
        if worker_name in malformed:
            failures.append(f"{worker_name} possui heartbeat malformado.")
            continue
        heartbeat = heartbeats.get(worker_name)
        if heartbeat is None:
            failures.append(f"{worker_name} sem heartbeat em system_health.")
            continue
        age = current_time - heartbeat
        ages[worker_name] = age
        if age < -MAX_FUTURE_HEARTBEAT_SECONDS:
            failures.append(
                f"{worker_name} heartbeat no futuro: {-age}s > "
                f"{MAX_FUTURE_HEARTBEAT_SECONDS}s de tolerancia."
            )
        elif age > max_age:
            failures.append(f"{worker_name} stale: heartbeat_age={age}s > {max_age}s.")

    return WorkerHealthAssessment(not failures, ages, tuple(failures))
