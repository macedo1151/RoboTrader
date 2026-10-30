"""Cruzamento de médias móveis simples (exemplo de seguidor de tendência)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


def sma(values: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) >= n:
        c = np.cumsum(np.insert(values, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


class SmaCross(Strategy):
    name = "sma_cross"

    def __init__(self, fast: int = 9, slow: int = 21, **window) -> None:
        if fast >= slow:
            raise ValueError("fast deve ser menor que slow")
        super().__init__(fast=fast, slow=slow, **window)
        self.fast = fast
        self.slow = slow

    @property
    def min_bars(self) -> int:
        return self.slow + 1

    def signals(self, bars: pd.DataFrame) -> pd.Series:
        close = bars["close"].to_numpy(dtype=float)
        fast, slow = sma(close, self.fast), sma(close, self.slow)
        valid = ~np.isnan(slow)
        above = np.where(valid, np.sign(fast - slow), 0).astype(int)
        prev = np.concatenate([[0], above[:-1]])
        prev_valid = np.concatenate([[False], valid[:-1]])
        out = np.zeros(len(close), dtype=int)
        ok = valid & prev_valid
        out[ok & (above == 1) & (prev == -1)] = 1
        out[ok & (above == -1) & (prev == 1)] = -1
        return pd.Series(out, index=bars.index)
