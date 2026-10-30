"""Rompimento de canal (Donchian): seguidor de tendência.

Compra quando o fechamento rompe a máxima das últimas `n` barras e vende quando
rompe a mínima. Com `exit_n > 0`, sai quando o preço rompe o canal contrário
mais curto de `exit_n` barras (trailing stop por canal).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class DonchianBreakout(Strategy):
    name = "donchian_breakout"

    def __init__(self, n: int = 20, exit_n: int = 0, **window) -> None:
        super().__init__(n=n, exit_n=exit_n, **window)
        self.n = n
        self.exit_n = exit_n

    @property
    def min_bars(self) -> int:
        return self.n + 2

    def signals(self, bars: pd.DataFrame) -> pd.Series:
        close = bars["close"]
        upper = bars["high"].rolling(self.n).max().shift(1)
        lower = bars["low"].rolling(self.n).min().shift(1)
        above = close > upper
        below = close < lower
        out = np.zeros(len(bars), dtype=int)
        out[(above & ~above.shift(1, fill_value=False)).to_numpy()] = 1
        out[(below & ~below.shift(1, fill_value=False)).to_numpy()] = -1
        return pd.Series(out, index=bars.index)

    def exits(self, bars):
        if self.exit_n <= 0:
            return None
        close = bars["close"]
        exit_long = close < bars["low"].rolling(self.exit_n).min().shift(1)
        exit_short = close > bars["high"].rolling(self.exit_n).max().shift(1)
        return exit_long, exit_short
