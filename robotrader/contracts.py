"""Especificações dos contratos e cálculo do vencimento vigente.

WIN (mini índice): tick de 5 pontos, R$ 0,20 por ponto -> R$ 1,00 por tick.
    Vence na quarta-feira mais próxima do dia 15 dos meses pares (G J M Q V Z).
WDO (mini dólar): tick de 0,5 ponto, R$ 10,00 por ponto -> R$ 5,00 por tick.
    Vence no primeiro dia útil de cada mês; negocia-se o contrato do mês seguinte.

O cálculo de vencimento ignora feriados. Em datas de rolagem, confira o
contrato no MetaTrader ou defina `contract` manualmente na configuração.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

MONTH_CODES = "FGHJKMNQUVXZ"  # jan..dez


@dataclass(frozen=True)
class ContractSpec:
    root: str
    tick_size: float
    point_value: float  # R$ por ponto por contrato

    @property
    def tick_value(self) -> float:
        return self.tick_size * self.point_value

    def round_price(self, price: float) -> float:
        """Arredonda para o tick mais próximo."""
        return round(round(price / self.tick_size) * self.tick_size, 6)

    def floor_price(self, price: float) -> float:
        return round(math.floor(price / self.tick_size + 1e-9) * self.tick_size, 6)

    def ceil_price(self, price: float) -> float:
        return round(math.ceil(price / self.tick_size - 1e-9) * self.tick_size, 6)


SPECS: dict[str, ContractSpec] = {
    "WIN": ContractSpec("WIN", tick_size=5.0, point_value=0.20),
    "WDO": ContractSpec("WDO", tick_size=0.5, point_value=10.0),
}


def get_spec(root: str) -> ContractSpec:
    try:
        return SPECS[root.upper()]
    except KeyError as exc:
        raise ValueError(f"Contrato desconhecido: {root}") from exc


def win_expiry(year: int, month: int) -> date:
    """Quarta-feira mais próxima do dia 15."""
    d15 = date(year, month, 15)
    # weekday(): segunda=0 ... quarta=2
    diff = 2 - d15.weekday()
    if diff > 3:
        diff -= 7
    elif diff < -3:
        diff += 7
    return d15 + timedelta(days=diff)


def wdo_expiry(year: int, month: int) -> date:
    """Primeiro dia útil (seg-sex) do mês."""
    d = date(year, month, 1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + n
    return idx // 12, idx % 12 + 1


def contract_code(root: str, year: int, month: int) -> str:
    return f"{root}{MONTH_CODES[month - 1]}{year % 100:02d}"


# Dias corridos antes do vencimento em que a liquidez passa para o próximo contrato.
# WIN: medido nos ticks da Genial (ago/out 2026) -> a liquidez migra NO dia do vencimento.
# WDO: estimativa; confira no MT5 perto do fim do mês.
ROLL_DAYS = {"WIN": 0, "WDO": 2}


def active_contract(root: str, today: date, roll_days: int | None = None) -> str:
    """Contrato vigente na data: o próximo vencimento, trocando `roll_days` dias corridos
    antes dele (0 = troca no próprio dia do vencimento)."""
    root = root.upper()
    roll_days = ROLL_DAYS.get(root, 2) if roll_days is None else roll_days
    for n in range(0, 14):
        year, month = _add_months(today.year, today.month, n)
        if root == "WIN":
            if month % 2:
                continue
            expiry = win_expiry(year, month)
        elif root == "WDO":
            if n == 0:
                continue  # o contrato do mês corrente já venceu no 1º dia útil
            expiry = wdo_expiry(year, month)
        else:
            raise ValueError(f"Contrato desconhecido: {root}")
        if today < expiry - timedelta(days=roll_days):
            return contract_code(root, year, month)
    raise RuntimeError("Não foi possível determinar o contrato vigente")
