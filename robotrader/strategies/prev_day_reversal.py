"""Reversão do dia anterior (hipótese H7 de research/diversify2.py).

Movimento do dia = fechamento às 17:45 (fim da barra das 17:40) - abertura das 9:00.
Se o movimento de ONTEM foi maior, em módulo, que o quantil `threshold_q` dos movimentos
dos `lookback` pregões anteriores, hoje opera CONTRA ele: ontem subiu forte -> vende.
Sinal na barra das 9:00 (executa às 9:05); sai na barra seguinte a `exit_at`.

Status: candidata (não passou no critério t >= 2). Rodar só no modo simulado,
para acumular evidência fora da amostra.
"""

from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd

from .base import Strategy, _hhmm


def daily_moves(bars: pd.DataFrame, open_at: time = time(9, 0),
                close_bar: time = time(17, 40)) -> pd.Series:
    """Movimento (pontos) de cada pregão: fechamento da barra `close_bar` - abertura de `open_at`."""
    t = bars.index.time
    o = bars["open"][t == open_at]
    c = bars["close"][t == close_bar]
    o.index, c.index = pd.Index(o.index.date), pd.Index(c.index.date)
    days = pd.Index(sorted(set(bars.index.date)))
    return (c.reindex(days) - o.reindex(days)).rename("move")


class PrevDayReversal(Strategy):
    name = "prev_day_reversal"

    def __init__(self, threshold_q: float = 0.5, lookback: int = 120,
                 exit_at: str = "17:40", **window) -> None:
        super().__init__(threshold_q=threshold_q, lookback=lookback, exit_at=exit_at, **window)
        self.threshold_q = threshold_q
        self.lookback = lookback
        self.exit_time = _hhmm(exit_at)

    @property
    def history_bars(self) -> int:
        # ~115 barras M5 por pregão: cobre `lookback` pregões + folga
        return (self.lookback + 10) * 120

    def _day_signal(self, bars: pd.DataFrame) -> pd.Series:
        """Sinal de cada pregão (índice = data), calculado só com pregões anteriores."""
        # o sinal de cada dia só depende de pregões anteriores: muda quando entra um
        # pregão novo (ou quando a janela de histórico avança)
        key = (bars.index[-1].date(), bars.index[0].date())
        cached = getattr(self, "_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        moves = daily_moves(bars)
        size = moves.abs()
        thr = size.shift(1).rolling(self.lookback, min_periods=max(20, self.lookback // 2)) \
            .quantile(self.threshold_q) if self.threshold_q > 0 else pd.Series(0.0, index=moves.index)
        today_sig = (-np.sign(moves)).where(size > thr, 0)   # sinal gerado PELO dia
        sig = today_sig.shift(1).fillna(0).astype(int)        # aplicado no pregão seguinte
        self._cache = (key, sig)
        return sig

    def signals(self, bars: pd.DataFrame) -> pd.Series:
        out = pd.Series(0, index=bars.index, dtype=int)
        if bars.empty:
            return out
        dates = pd.Index(bars.index.date)
        first = ~pd.Series(dates, index=bars.index).duplicated().to_numpy()
        first &= bars.index.time == time(9, 0)
        sig = self._day_signal(bars)
        out.iloc[np.flatnonzero(first)] = sig.reindex(dates[first]).fillna(0).astype(int).to_numpy()
        return out

    def exits(self, bars):
        late = pd.Series(bars.index.time >= self.exit_time, index=bars.index)
        return late, late
