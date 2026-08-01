"""Compatibility wrapper for a 20-cycle PostgreSQL paper-trading smoke run."""

import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.tests.run_paper_trading import start_paper_trading


def parse_args():
    parser = argparse.ArgumentParser(description="Run paper-trading smoke cycles on configured PostgreSQL data.")
    parser.add_argument("--cycles", type=int, default=20)
    parser.add_argument("--sleep", type=int, default=0)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    start_paper_trading(cycles=args.cycles, sleep_seconds=args.sleep, backup=False)
