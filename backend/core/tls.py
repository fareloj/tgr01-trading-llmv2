"""TLS bootstrap using the operating system's maintained trust store."""

from __future__ import annotations

from threading import Lock

import truststore

_LOCK = Lock()
_CONFIGURED = False


def configure_native_ca_store() -> None:
    """Make stdlib and Requests TLS validation use native certificates."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    with _LOCK:
        if _CONFIGURED:
            return
        truststore.inject_into_ssl()
        _CONFIGURED = True
