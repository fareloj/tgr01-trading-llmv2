from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import torch

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.ml.dataset import FEATURE_COLUMNS
from backend.ml.inference import TCNAdvisor


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the read-only TCN research advisory.")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument(
        "--dataset",
        default=str(PROJECT_DIR / "backend" / "reports" / "mb_tcn_dataset.csv"),
    )
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args()
    if args.device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA is unavailable")

    advisor = TCNAdvisor(Path(args.checkpoint), device=args.device)
    context = pd.read_csv(args.dataset, usecols=list(FEATURE_COLUMNS)).tail(
        advisor.sequence_length
    )
    result = advisor.predict(context.to_numpy(dtype="float32"))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] != "UNAVAILABLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
