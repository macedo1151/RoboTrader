"""Reversão à média.

Mede quão longe o preço está de uma referência (z-score):
- anchor="sma":  z = (fechamento - média de n) / desvio-padrão de n  (Bandas de Bollinger)
- anchor="vwap": z = (fechamento - VWAP do dia) / ATR de n
Compra quando z cruza abaixo de -k, vende quando cruza acima de +k, e sai quando o
preço volta à referência.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, session_vwap
from .base import Strategy


class MeanReversion(Strategy):
    name = "mean_reversion"

    def __init__(self, n: int = 20, k: float = 2.0, anchor: str = "sma", **window) -> None:
        if anchor not in ("sma", "vwap"):
            raise ValueError("anchor deve ser 'sma' ou 'vwap'")
        super().__init__(n=n, k=k, anchor=anchor, **window)
        self.n, self.k, self.anchor = n, k, anchor

    @property
    def min_bars(self) -> int:
        return self.n + 2

    def _anchor_and_z(self, bars):
        close = bars["close"]
        if self.anchor == "sma":
            ref = close.rolling(self.n).mean()
            scale = close.rolling(self.n).std()
        else:
            ref = session_vwap(bars)
            scale = atr(bars, self.n)
        return ref, (close - ref) / scale.replace(0, np.nan)

    def signals(self, bars: pd.DataFrame) -> pd.Series:
        _, z = self._anchor_and_z(bars)
        prev = z.shift(1)
        out = np.zeros(len(bars), dtype=int)
        out[((z < -self.k) & (prev >= -self.k)).to_numpy()] = 1
        out[((z > self.k) & (prev <= self.k)).to_numpy()] = -1
        return pd.Series(out, index=bars.index)

    def exits(self, bars):
        ref, _ = self._anchor_and_z(bars)
        close = bars["close"]
        return close >= ref, close <= ref
