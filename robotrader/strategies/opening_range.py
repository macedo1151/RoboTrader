"""Faixa de abertura (Opening Range): rompimento ou reversão.

Marca a máxima e a mínima dos primeiros `range_minutes` do pregão. Depois disso,
no primeiro fechamento fora da faixa:
- mode="breakout": compra no rompimento para cima, vende no rompimento para baixo;
- mode="fade": faz o contrário (aposta que o rompimento falha).
No máximo um sinal por direção por dia.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


def _first_per_day(mask: np.ndarray, day_code: np.ndarray) -> np.ndarray:
    """Posições da primeira ocorrência de `mask` em cada dia."""
    pos = np.flatnonzero(mask)
    _, first = np.unique(day_code[pos], return_index=True)
    return pos[first]


class OpeningRangeBreakout(Strategy):
    name = "opening_range_breakout"

    def __init__(self, range_minutes: int = 30, mode: str = "breakout", **window) -> None:
        if mode not in ("breakout", "fade"):
            raise ValueError("mode deve ser 'breakout' ou 'fade'")
        super().__init__(range_minutes=range_minutes, mode=mode, **window)
        self.range_minutes = range_minutes
        self.direction = 1 if mode == "breakout" else -1

    def signals(self, bars: pd.DataFrame) -> pd.Series:
        out = np.zeros(len(bars), dtype=int)
        if len(bars) < 2:
            return pd.Series(out, index=bars.index)

        t = bars.index.values
        bar_len = np.median(np.diff(t))
        _, day_code = np.unique(bars.index.normalize().values, return_inverse=True)
        n_days = day_code.max() + 1
        day_start = np.empty(n_days, dtype=t.dtype)
        first_idx = np.unique(day_code, return_index=True)[1]
        day_start[day_code[first_idx]] = t[first_idx]

        # a barra pertence à faixa se FECHA até o fim da faixa
        limit = day_start[day_code] + np.timedelta64(self.range_minutes, "m")
        in_range = t + bar_len <= limit

        high = bars["high"].to_numpy()
        low = bars["low"].to_numpy()
        close = bars["close"].to_numpy()
        hi = np.full(n_days, -np.inf)
        lo = np.full(n_days, np.inf)
        np.maximum.at(hi, day_code[in_range], high[in_range])
        np.minimum.at(lo, day_code[in_range], low[in_range])

        after = ~in_range & np.isfinite(hi[day_code])
        out[_first_per_day(after & (close > hi[day_code]), day_code)] = self.direction
        out[_first_per_day(after & (close < lo[day_code]), day_code)] = -self.direction
        return pd.Series(out, index=bars.index)
