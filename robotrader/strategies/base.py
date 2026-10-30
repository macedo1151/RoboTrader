"""Interface comum das estratégias.

Uma estratégia recebe as barras FECHADAS e devolve:
- `signals(bars)`: +1 = compra, -1 = venda, 0 = nada (entrada ou inversão);
- `exits(bars)` (opcional): par (sair_da_compra, sair_da_venda) de séries booleanas.

O sinal da barra t é executado na abertura da barra t+1 (no backtest) ou
logo após o fechamento da barra (ao vivo). Assim não há olhar para o futuro.

Toda estratégia aceita `entry_from` e `entry_until` ("HH:MM") para limitar o
horário dos sinais de entrada. Use `generate()` para obter os sinais já filtrados.

Stop, alvo, zeragem e limites de perda ficam com o gerenciador de risco.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import time
from typing import Any

import numpy as np
import pandas as pd


def _hhmm(value: str | None) -> time | None:
    if not value:
        return None
    h, m = str(value).split(":")
    return time(int(h), int(m))


class Strategy(ABC):
    name: str = "base"
    # símbolos de outros mercados que a estratégia precisa (ex.: ["WSP$N"])
    external_symbols: list[str] = []

    def __init__(self, entry_from: str | None = None, entry_until: str | None = None,
                 **params: Any) -> None:
        self.entry_from = _hhmm(entry_from)
        self.entry_until = _hhmm(entry_until)
        self.params = dict(params)
        if entry_from:
            self.params["entry_from"] = entry_from
        if entry_until:
            self.params["entry_until"] = entry_until

    @property
    def min_bars(self) -> int:
        """Quantidade mínima de barras para gerar o primeiro sinal."""
        return 1

    @abstractmethod
    def signals(self, bars: pd.DataFrame) -> pd.Series:
        """Série de int (-1, 0, +1) alinhada ao índice de `bars`."""

    def exits(self, bars: pd.DataFrame) -> tuple[pd.Series, pd.Series] | None:
        """(sair_da_compra, sair_da_venda). None = só stop/alvo/zeragem/inversão."""
        return None

    @property
    def history_bars(self) -> int:
        """Quantas barras do próprio ativo buscar ao vivo (mínimo usado: 300)."""
        return 300

    @property
    def external_bars(self) -> int:
        """Quantas barras de cada símbolo externo buscar ao vivo."""
        return 0

    def update_external(self, data: dict[str, pd.DataFrame]) -> None:
        """Recebe as barras fechadas dos símbolos externos (ao vivo e no backtest)."""

    def ready(self, bars: pd.DataFrame) -> bool:
        """False = ainda falta algum dado para decidir sobre a última barra (tentar de novo)."""
        return True

    def generate(self, bars: pd.DataFrame) -> pd.Series:
        sig = self.signals(bars)
        if self.entry_from or self.entry_until:
            t = bars.index.time
            ok = np.ones(len(bars), dtype=bool)
            if self.entry_from:
                ok &= t >= self.entry_from
            if self.entry_until:
                ok &= t <= self.entry_until
            sig = sig.where(ok, 0)
        return sig.astype(int)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.params})"
