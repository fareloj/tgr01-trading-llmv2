"""Compara a definicao antiga (SMA) e a atual (Wilder) do RSI sobre os candles reais.

Motivacao
---------
O docstring de `backend/features/indicators.py:wilder_rsi` registrava "489 falsos
OVERBOUGHT e 432 falsos OVERSOLD" sem citar dataset, intervalo ou consulta. Uma
auditoria independente nao reproduziu esses numeros em nenhuma serie disponivel.
Este script existe para que a afirmacao deixe de ser um numero solto e passe a ser
uma medicao que qualquer pessoa roda de novo.

O que ele faz
-------------
1. Le os candles BTC/BRL 1m do PostgreSQL via `backend.core.repository`.
2. Recalcula o RSI pelas DUAS definicoes sobre exatamente a mesma serie:
   - `sma`: delta.where(delta>0,0).rolling(14, min_periods=1).mean() (a versao antiga);
   - `wilder`: `backend.features.indicators.wilder_rsi`.
3. Classifica cada posicao em OVERBOUGHT (>=70) / OVERSOLD (<=30) / NEUTRAL, usando
   os MESMOS limiares de `calculate_technical_status`.
4. Conta as divergencias posicao a posicao e imprime os totais.

Uso
---
    py -3.11 backend\\tests\\audit_rsi_definition.py
    py -3.11 backend\\tests\\audit_rsi_definition.py --asset BTC/BRL --timeframe 1m

Se o banco nao estiver acessivel, o script falha com mensagem explicita; ele nunca
cai para um dataset sintetico, porque um numero sintetico recriaria o problema que
este script veio resolver.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.core import repository
from backend.features.indicators import wilder_rsi

OVERBOUGHT = 70.0
OVERSOLD = 30.0


def sma_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Definicao antiga, preservada aqui apenas para comparacao.

    Reproduz literalmente o trecho removido de `calculate_technical_status`:
    media movel simples dos ganhos/perdas com `min_periods=1` (sem warmup NaN).
    Nao usar em codigo de producao.
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period, min_periods=1).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def classify(values: pd.Series) -> pd.Series:
    """Mesmos limiares de `calculate_technical_status` (OVERBOUGHT>=70, OVERSOLD<=30)."""
    result = pd.Series("NEUTRAL", index=values.index, dtype="object")
    result = result.mask(values >= OVERBOUGHT, "OVERBOUGHT")
    result = result.mask(values <= OVERSOLD, "OVERSOLD")
    return result.mask(values.isna(), "NEUTRAL")


def compare_status(old: pd.Series, new: pd.Series) -> dict:
    """Conta divergencias posicao a posicao entre as duas classificacoes."""
    old_cls, new_cls = classify(old), classify(new)
    return {
        "old_overbought_new_not": int(((old_cls == "OVERBOUGHT") & (new_cls != "OVERBOUGHT")).sum()),
        "old_oversold_new_not": int(((old_cls == "OVERSOLD") & (new_cls != "OVERSOLD")).sum()),
        "new_overbought_old_not": int(((new_cls == "OVERBOUGHT") & (old_cls != "OVERBOUGHT")).sum()),
        "new_oversold_old_not": int(((new_cls == "OVERSOLD") & (old_cls != "OVERSOLD")).sum()),
        "old_totals": {k: int(v) for k, v in old_cls.value_counts().items()},
        "new_totals": {k: int(v) for k, v in new_cls.value_counts().items()},
    }


def load_closes(asset: str, timeframe: str, limit: int) -> pd.DataFrame:
    rows = repository.get_klines(asset, timeframe, limit)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError(
            f"nenhum candle para {asset} {timeframe}; suba o banco e os workers antes de auditar"
        )
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    if frame["close"].isna().any():
        raise RuntimeError("a serie contem close com NaN; a comparacao posicao a posicao nao seria valida")
    return frame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--asset", default="BTC/BRL")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--limit", type=int, default=100000)
    parser.add_argument("--period", type=int, default=14)
    args = parser.parse_args()

    frame = load_closes(args.asset, args.timeframe, args.limit)
    close = frame["close"].astype(float)
    first_ts, last_ts = int(frame["timestamp"].iloc[0]), int(frame["timestamp"].iloc[-1])

    old = sma_rsi(close, args.period)
    new = wilder_rsi(close, args.period)
    stats = compare_status(old, new)

    print("=" * 68)
    print(f"RSI {args.asset} {args.timeframe}: {len(close)} candles")
    print(f"timestamps {first_ts}..{last_ts}")
    print(f"period={args.period}  limiares OVERBOUGHT>={OVERBOUGHT:.0f} OVERSOLD<={OVERSOLD:.0f}")
    print("=" * 68)
    print("Antiga (SMA) = OVERBOUGHT, Wilder != OVERBOUGHT:", stats["old_overbought_new_not"])
    print("Antiga (SMA) = OVERSOLD,   Wilder != OVERSOLD  :", stats["old_oversold_new_not"])
    print("Wilder = OVERBOUGHT, antiga != OVERBOUGHT     :", stats["new_overbought_old_not"])
    print("Wilder = OVERSOLD,   antiga != OVERSOLD       :", stats["new_oversold_old_not"])
    print("-" * 68)
    print("totais SMA   :", stats["old_totals"])
    print("totais Wilder:", stats["new_totals"])
    print("=" * 68)
    print(
        "Registre estes numeros no docstring de wilder_rsi junto do range de\n"
        "timestamps acima. Nao reaproveite numeros de uma execucao antiga."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
