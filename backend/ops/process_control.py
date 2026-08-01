from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Callable


RunCommand = Callable[..., subprocess.CompletedProcess]


def terminate_process_tree(
    pid: int,
    *,
    platform: str = sys.platform,
    run_command: RunCommand = subprocess.run,
) -> None:
    """Terminate an operational command and every child it launched."""
    if pid <= 0:
        return
    if platform.startswith("win"):
        run_command(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
            text=True,
        )
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

