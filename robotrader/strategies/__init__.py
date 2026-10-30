from __future__ import annotations

from typing import Any

from .base import Strategy
from .donchian import DonchianBreakout
from .external_gap import ExternalGapFade, overnight_gaps
from .mean_reversion import MeanReversion
from .opening_range import OpeningRangeBreakout
from .prev_day_reversal import PrevDayReversal
from .sma_cross import SmaCross

STRATEGIES: dict[str, type[Strategy]] = {
    cls.name: cls
    for cls in (SmaCross, OpeningRangeBreakout, DonchianBreakout, MeanReversion,
                ExternalGapFade, PrevDayReversal)
}


def get_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    try:
        cls = STRATEGIES[name]
    except KeyError as exc:
        raise ValueError(
            f"Estratégia '{name}' não existe. Disponíveis: {sorted(STRATEGIES)}"
        ) from exc
    return cls(**(params or {}))


__all__ = [
    "Strategy", "STRATEGIES", "get_strategy",
    "SmaCross", "OpeningRangeBreakout", "DonchianBreakout", "MeanReversion",
    "ExternalGapFade", "overnight_gaps", "PrevDayReversal",
]
