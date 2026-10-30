"""Reação da abertura ao movimento noturno de um mercado externo (ex.: S&P 500).

O micro S&P da B3 (WSP) abre às 9:00 junto com o WIN; o gap dele (preço das 9:00 vs
fechamento do dia anterior) mede quanto o S&P andou durante a noite.

Na primeira barra do dia (sinal executado na abertura da segunda, 9:05):
- direction=-1 ("fade"): vende o WIN se o S&P subiu forte, compra se caiu forte;
- direction=+1: segue o S&P.
"Forte" = |gap| acima do quantil `threshold_q` dos |gaps| dos `lookback` pregões
anteriores (sem olhar o futuro). Sai no horário `exit_at` (ou stop/alvo/zeragem).

Dados do mercado externo, de duas formas:
- `external="WSP$N"`: a estratégia recebe as barras via `update_external()` (robô ao vivo,
  replay e backtest pela linha de comando);
- `gaps=`: série pronta, indexada por data (pesquisa).
Só entra em dias em que a primeira barra externa é a das 9:00.
"""

from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd

from .base import Strategy, _hhmm


def overnight_gaps(ext: pd.DataFrame, first_bar: time = time(9, 0)) -> pd.Series:
    """Gap de cada pregão do mercado externo: fechamento da 1ª barra / último fechamento anterior - 1."""
    g = ext.groupby(ext.index.date)
    first_close = g["close"].first()
    first_time = pd.Series(ext.index, index=ext.index).groupby(ext.index.date).first().dt.time
    gaps = first_close / g["close"].last().shift() - 1
    gaps[first_time != first_bar] = np.nan
    gaps.index = pd.Index(gaps.index, name="date")
    return gaps.dropna()


class ExternalGapFade(Strategy):
    name = "external_gap"

    def __init__(self, gaps: pd.Series | None = None, external: str | None = None,
                 exit_at: str = "13:00", threshold_q: float = 0.5, lookback: int = 120,
                 direction: int = -1, **window) -> None:
        super().__init__(exit_at=exit_at, threshold_q=threshold_q, lookback=lookback,
                         direction=direction, **window)
        if gaps is None and not external:
            raise ValueError("external_gap precisa de `external` (símbolo) ou da série `gaps`")
        if external:
            self.params["external"] = external
        self.external_symbols = [external] if external else []
        self.gaps = gaps if gaps is not None else pd.Series(dtype=float)
        self.exit_time = _hhmm(exit_at)
        self.threshold_q = threshold_q
        self.lookback = lookback
        self.direction = direction

    @property
    def external_bars(self) -> int:
        # barras M5 suficientes para `lookback` pregões + folga (~115 barras por pregão)
        return (self.lookback + 10) * 120

    def update_external(self, data: dict[str, pd.DataFrame]) -> None:
        if not self.external_symbols:
            return
        ext = data.get(self.external_symbols[0])
        if ext is None or not len(ext):
            self.gaps = pd.Series(dtype=float)
            return
        key = ext.index[-1].date()
        # os gaps só mudam quando chega um pregão novo: evita recalcular a cada barra
        if getattr(self, "_gaps_key", None) == key and key in self.gaps.index:
            return
        self.gaps = overnight_gaps(ext)
        self._gaps_key = key

    def ready(self, bars: pd.DataFrame) -> bool:
        """Na 1ª barra do dia (9:00), só decide quando o gap externo do dia já existe."""
        if bars.empty or not self.external_symbols:
            return True
        last = bars.index[-1]
        is_first = last.time() == time(9, 0) and (len(bars) < 2 or bars.index[-2].date() != last.date())
        return (not is_first) or (last.date() in self.gaps.index)

    def _day_signal(self) -> pd.Series:
        g = self.gaps
        if g.empty:
            return pd.Series(dtype=int)
        key = (len(g), g.index[0], g.index[-1], float(g.iloc[-1]))
        cached = getattr(self, "_sig_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        sig = self._compute_day_signal(g.sort_index())
        self._sig_cache = (key, sig)
        return sig

    def _compute_day_signal(self, g: pd.Series) -> pd.Series:
        thr = g.abs().shift(1).rolling(self.lookback, min_periods=max(20, self.lookback // 2)) \
            .quantile(self.threshold_q) if self.threshold_q > 0 else pd.Series(0.0, index=g.index)
        sig = np.sign(g) * self.direction
        return sig.where(g.abs() > thr, 0).fillna(0).astype(int)

    def signals(self, bars: pd.DataFrame) -> pd.Series:
        out = pd.Series(0, index=bars.index, dtype=int)
        if bars.empty:
            return out
        dates = pd.Index(bars.index.date)
        first = ~pd.Series(dates, index=bars.index).duplicated().to_numpy()
        first &= bars.index.time == time(9, 0)
        day_sig = self._day_signal()
        vals = day_sig.reindex(dates[first]).fillna(0).astype(int).to_numpy()
        out.iloc[np.flatnonzero(first)] = vals
        return out

    def exits(self, bars):
        late = pd.Series(bars.index.time >= self.exit_time, index=bars.index)
        return late, late
