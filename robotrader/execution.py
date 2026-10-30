"""Simulação de execução: posição, stop/alvo dentro da barra, custos e slippage.

Usado tanto pelo backtest quanto pelo PaperBroker, para que o simulado ao vivo
se comporte igual ao backtest.

Premissas conservadoras:
- entradas e saídas a mercado pagam `slippage_ticks` contra você;
- alvo (ordem limitada) é executado no preço do alvo, sem slippage;
- se stop e alvo cabem na mesma barra, assume-se que o STOP foi atingido;
- se a barra abre além do stop (gap), a saída é no preço de abertura;
- trailing: o stop só é ajustado com a máxima/mínima de barras JÁ concluídas
  (a barra atual primeiro é checada contra o stop antigo).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contracts import ContractSpec


@dataclass
class Position:
    side: int            # +1 comprado, -1 vendido
    qty: int
    entry_price: float
    entry_time: datetime
    stop: float | None
    target: float | None
    trail_dist: float | None = None  # trailing: distância em pontos (ativa ao andar isso a favor)
    best: float | None = None        # melhor preço atingido desde a entrada


@dataclass
class Trade:
    symbol: str
    side: int
    qty: int
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    points: float
    gross: float
    costs: float
    net: float
    reason: str


def trail_points(inst, atr_value: float | None) -> float:
    """Distância do trailing em pontos (0 = sem trailing)."""
    k = getattr(inst, "trail_atr", 0.0) or 0.0
    if k > 0 and atr_value is not None and atr_value == atr_value and atr_value > 0:
        return k * atr_value
    return 0.0


def stop_target_points(inst, atr_value: float | None) -> tuple[float, float]:
    """Distâncias de stop e alvo em pontos: fixas ou proporcionais ao ATR."""
    stop, target = inst.stop_points, inst.target_points
    if atr_value is not None and atr_value == atr_value and atr_value > 0:  # não-NaN
        if inst.stop_atr > 0:
            stop = inst.stop_atr * atr_value
        if inst.target_atr > 0:
            target = inst.target_atr * atr_value
    return stop, target


@dataclass
class ExecutionModel:
    spec: ContractSpec
    stop_points: float
    target_points: float
    slippage_ticks: int = 1
    cost_per_contract: float = 0.0  # por lado

    @property
    def slippage(self) -> float:
        return self.slippage_ticks * self.spec.tick_size

    def open(self, side: int, qty: int, price: float, ts: datetime,
             stop_points: float | None = None, target_points: float | None = None,
             trail_points: float = 0.0) -> Position:
        stop_points = self.stop_points if stop_points is None else stop_points
        target_points = self.target_points if target_points is None else target_points
        entry = self.spec.round_price(price + side * self.slippage)
        stop = (
            self.spec.round_price(entry - side * stop_points)
            if stop_points > 0 else None
        )
        target = (
            self.spec.round_price(entry + side * target_points)
            if target_points > 0 else None
        )
        trail = trail_points if trail_points and trail_points > 0 else None
        return Position(side, qty, entry, ts, stop, target, trail, entry if trail else None)

    def market_exit_price(self, pos: Position, price: float) -> float:
        return self.spec.round_price(price - pos.side * self.slippage)

    def check_bar(
        self, pos: Position, o: float, h: float, lo: float
    ) -> tuple[float, str] | None:
        """Verifica se stop ou alvo foram atingidos na barra. Retorna (preço, motivo)."""
        s = pos.side
        if pos.stop is not None:
            gapped = o <= pos.stop if s > 0 else o >= pos.stop
            if gapped:
                return self.market_exit_price(pos, o), "stop (gap)"
            hit = lo <= pos.stop if s > 0 else h >= pos.stop
            if hit:
                return self.market_exit_price(pos, pos.stop), "stop"
        if pos.target is not None:
            gapped = o >= pos.target if s > 0 else o <= pos.target
            if gapped:
                return o, "alvo (gap)"
            hit = h >= pos.target if s > 0 else lo <= pos.target
            if hit:
                return pos.target, "alvo"
        self._update_trail(pos, h, lo)
        return None

    def _update_trail(self, pos: Position, h: float, lo: float) -> None:
        """Depois de andar `trail_dist` a favor, o stop segue o melhor preço a essa distância."""
        if not pos.trail_dist:
            return
        s = pos.side
        pos.best = max(pos.best, h) if s > 0 else min(pos.best, lo)
        if (pos.best - pos.entry_price) * s >= pos.trail_dist:
            level = self.spec.round_price(pos.best - s * pos.trail_dist)
            if pos.stop is None:
                pos.stop = level
            else:
                pos.stop = max(pos.stop, level) if s > 0 else min(pos.stop, level)

    def close(
        self, symbol: str, pos: Position, price: float, ts: datetime, reason: str
    ) -> Trade:
        points = (price - pos.entry_price) * pos.side
        gross = points * self.spec.point_value * pos.qty
        costs = 2 * self.cost_per_contract * pos.qty
        return Trade(
            symbol=symbol, side=pos.side, qty=pos.qty,
            entry_time=pos.entry_time, entry_price=pos.entry_price,
            exit_time=ts, exit_price=price, points=round(points, 6),
            gross=round(gross, 2), costs=round(costs, 2),
            net=round(gross - costs, 2), reason=reason,
        )
