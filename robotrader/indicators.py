"""Indicadores técnicos usados pelas estratégias e pelo cálculo de stop por ATR."""

from __future__ import annotations

import numpy as np
import pandas as pd


def day_key(bars: pd.DataFrame) -> np.ndarray:
    return bars.index.normalize().values


def true_range(bars: pd.DataFrame) -> pd.Series:
    prev_close = bars["close"].shift(1)
    # na primeira barra do dia o gap não entra no TR
    new_day = pd.Series(day_key(bars), index=bars.index).diff().ne(pd.Timedelta(0))
    prev_close[new_day] = np.nan
    return pd.concat(
        [bars["high"] - bars["low"],
         (bars["high"] - prev_close).abs(),
         (bars["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(bars).rolling(period, min_periods=period).mean()


def session_vwap(bars: pd.DataFrame) -> pd.Series:
    """VWAP acumulada do pregão (reinicia a cada dia)."""
    key = day_key(bars)
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    vol = bars["volume"].where(bars["volume"] > 0, 1.0)
    pv = (typical * vol).groupby(key).cumsum()
    return pv / vol.groupby(key).cumsum()


def bar_of_day(bars: pd.DataFrame) -> pd.Series:
    """Índice da barra dentro do dia (0 = primeira barra do pregão)."""
    return pd.Series(1, index=bars.index).groupby(day_key(bars)).cumsum() - 1
