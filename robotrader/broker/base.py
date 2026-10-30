"""Interfaces de fonte de dados e de corretora usadas pelo robô ao vivo."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass
class OpenPosition:
    side: int
    qty: int
    entry_price: float


@dataclass
class ClosedTrade:
    exit_time: datetime
    net: float


class DataFeed(ABC):
    @abstractmethod
    def get_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """Últimas `count` barras FECHADAS (sem a barra em formação)."""


class Broker(ABC):
    @abstractmethod
    def position(self, symbol: str) -> OpenPosition | None: ...

    @abstractmethod
    def open(
        self, symbol: str, side: int, qty: int,
        stop_points: float, target_points: float, comment: str = "",
        trail_points: float = 0.0,
    ) -> bool: ...

    @abstractmethod
    def close(self, symbol: str, reason: str = "") -> bool: ...

    @abstractmethod
    def closed_trades_today(self, symbol: str, today: datetime) -> list[ClosedTrade]:
        """Operações encerradas no dia (para reconstruir os limites de risco)."""
