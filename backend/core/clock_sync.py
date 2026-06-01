import email.utils
import statistics
import time

import requests


DEFAULT_CLOCK_URLS = (
    "https://www.google.com/generate_204",
    "https://www.cloudflare.com/cdn-cgi/trace",
)


def fetch_http_clock_samples(urls: tuple[str, ...] = DEFAULT_CLOCK_URLS, timeout: float = 5.0) -> list[dict]:
    """Read independent HTTP Date headers without trusting the local wall clock."""
    samples = []
    for url in urls:
        try:
            response = requests.get(url, timeout=timeout, headers={"Cache-Control": "no-cache"})
            date_header = response.headers.get("Date")
            if not date_header:
                raise ValueError("HTTP Date header ausente")
            remote_dt = email.utils.parsedate_to_datetime(date_header)
            remote_timestamp = int(remote_dt.timestamp())
            local_timestamp = int(time.time())
            samples.append(
                {
                    "url": url,
                    "remote_timestamp": remote_timestamp,
                    "local_timestamp": local_timestamp,
                    "skew_seconds": local_timestamp - remote_timestamp,
                }
            )
        except Exception as exc:
            samples.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
    return samples


def check_clock_skew(
    *,
    max_skew_seconds: int = 300,
    urls: tuple[str, ...] = DEFAULT_CLOCK_URLS,
    timeout: float = 5.0,
) -> dict:
    samples = fetch_http_clock_samples(urls=urls, timeout=timeout)
    valid_samples = [sample for sample in samples if "skew_seconds" in sample]
    if not valid_samples:
        return {
            "status": "UNAVAILABLE",
            "is_within_tolerance": False,
            "max_skew_seconds": max_skew_seconds,
            "skew_seconds": None,
            "samples": samples,
        }

    skew_seconds = int(round(statistics.median(sample["skew_seconds"] for sample in valid_samples)))
    return {
        "status": "OK" if abs(skew_seconds) <= max_skew_seconds else "CLOCK_SKEW",
        "is_within_tolerance": abs(skew_seconds) <= max_skew_seconds,
        "max_skew_seconds": max_skew_seconds,
        "skew_seconds": skew_seconds,
        "samples": samples,
    }
