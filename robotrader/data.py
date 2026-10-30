"""Carregamento de dados históricos (CSV) e geração de dados sintéticos para testes.

Formato padrão do DataFrame: índice `datetime` (horário de Brasília, sem fuso)
e colunas `open, high, low, close, volume`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .contracts import ContractSpec

COLUMNS = ["open", "high", "low", "close", "volume"]

TIMEFRAME_MINUTES = {
    "M1": 1, "M2": 2, "M3": 3, "M5": 5, "M10": 10,
    "M15": 15, "M30": 30, "H1": 60,
}


def load_csv(path: str | Path) -> pd.DataFrame:
    """Lê CSV no formato padrão ou exportado pelo MetaTrader 5.

    Formato padrão: datetime,open,high,low,close,volume
    Formato MT5 (Centro de Histórico / Exportar barras), separado por TAB:
        <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> <TICKVOL> <VOL> <SPREAD>
    """
    path = Path(path)
    with open(path, encoding="utf-8-sig") as fh:
        header = fh.readline()

    if "<DATE>" in header.upper():
        sep = "\t" if "\t" in header else ","
        raw = pd.read_csv(path, sep=sep, encoding="utf-8-sig")
        raw.columns = [c.strip("<>").lower() for c in raw.columns]
        if "time" in raw.columns:
            stamp = raw["date"].astype(str) + " " + raw["time"].astype(str)
        else:
            stamp = raw["date"].astype(str)
        raw.index = pd.to_datetime(stamp, format="mixed")
        if "vol" in raw.columns and raw["vol"].sum() > 0:
            raw["volume"] = raw["vol"]
        else:
            raw["volume"] = raw.get("tickvol", 0)
        df = raw[COLUMNS]
    else:
        df = pd.read_csv(path, parse_dates=["datetime"], index_col="datetime")
        df.columns = [c.lower() for c in df.columns]
        if "volume" not in df.columns:
            df["volume"] = 0
        df = df[COLUMNS]

    df.index.name = "datetime"
    return validate(df.sort_index())


def validate(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Colunas ausentes: {sorted(missing)}")
    if not df.index.is_monotonic_increasing:
        raise ValueError("Índice de datas fora de ordem")
    df = df[~df.index.duplicated(keep="last")]
    bad = (df["high"] < df[["open", "close"]].max(axis=1)) | (
        df["low"] > df[["open", "close"]].min(axis=1)
    )
    if bad.any():
        raise ValueError(f"{int(bad.sum())} barras com high/low inconsistentes")
    return df.astype({c: float for c in COLUMNS})


def synthetic_bars(
    spec: ContractSpec,
    start_price: float,
    days: int = 60,
    minutes: int = 5,
    daily_vol: float = 0.012,
    seed: int = 42,
    start_date: date = date(2026, 1, 5),
    session: tuple[time, time] = (time(9, 0), time(18, 0)),
) -> pd.DataFrame:
    """Passeio aleatório arredondado ao tick. Serve para testar o código, NÃO a estratégia."""
    rng = np.random.default_rng(seed)
    rows = []
    price = start_price
    d = start_date
    produced = 0
    bars_per_day = int(
        (datetime.combine(d, session[1]) - datetime.combine(d, session[0])).seconds
        / 60 / minutes
    )
    bar_vol = daily_vol / np.sqrt(bars_per_day)
    while produced < days:
        if d.weekday() < 5:
            ts = datetime.combine(d, session[0])
            # pequeno gap de abertura
            price *= 1 + rng.normal(0, daily_vol / 3)
            for _ in range(bars_per_day):
                o = spec.round_price(price)
                steps = rng.normal(0, bar_vol, 4) * price
                path = o + np.cumsum(steps)
                c = spec.round_price(path[-1])
                h = spec.ceil_price(max(o, c, path.max()))
                lo = spec.floor_price(min(o, c, path.min()))
                rows.append((ts, o, h, lo, c, int(rng.integers(100, 5000))))
                price = c
                ts += timedelta(minutes=minutes)
            produced += 1
        d += timedelta(days=1)

    df = pd.DataFrame(rows, columns=["datetime", *COLUMNS]).set_index("datetime")
    return validate(df)


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Agrega barras para um tempo gráfico maior, sem misturar dias."""
    minutes = TIMEFRAME_MINUTES[timeframe]
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = (
        df.groupby(df.index.date, group_keys=False)
        .apply(lambda g: g.resample(f"{minutes}min", label="left", closed="left").agg(agg))
        .dropna(subset=["open"])
    )
    out.index.name = "datetime"
    return out
