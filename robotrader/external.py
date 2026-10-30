"""Dados de mercados externos salvos em data/external/ (ver research/download_external.py)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .data import load_csv

EXTERNAL_DIR = Path(__file__).resolve().parent.parent / "data" / "external"


def external_path(symbol: str, timeframe: str = "M5") -> Path:
    return EXTERNAL_DIR / f"{symbol.replace('$', '_')}_{timeframe}.csv"


def load_external(symbols: list[str], timeframe: str = "M5") -> dict[str, pd.DataFrame]:
    out = {}
    for sym in symbols:
        path = external_path(sym, timeframe)
        if not path.exists():
            raise RuntimeError(
                f"Faltam os dados de {sym} ({path}). Rode research/download_external.py {sym}"
            )
        out[sym] = load_csv(path)
    return out
