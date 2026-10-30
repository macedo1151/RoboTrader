"""Gerenciamento de risco compartilhado pelo backtest e pela execução ao vivo.

Regras:
- só abre posição entre `session.start` e `session.last_entry`;
- zera tudo a partir de `session.flatten`;
- para de operar no dia ao atingir `max_daily_loss`, `max_trades_per_day`
  ou `max_consecutive_losses`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .config import RiskConfig, SessionConfig


@dataclass
class DayState:
    day: date | None = None
    pnl: float = 0.0
    trades: int = 0
    consecutive_losses: int = 0
    halted_reason: str = ""


@dataclass
class RiskManager:
    risk: RiskConfig
    session: SessionConfig
    state: DayState = field(default_factory=DayState)

    def roll_day(self, ts: datetime) -> None:
        if self.state.day != ts.date():
            self.state = DayState(day=ts.date())

    def must_flatten(self, ts: datetime) -> bool:
        return ts.time() >= self.session.flatten

    def can_enter(self, ts: datetime) -> tuple[bool, str]:
        self.roll_day(ts)
        t = ts.time()
        if t < self.session.start:
            return False, "antes do horário de início"
        if t > self.session.last_entry:
            return False, "após horário limite de entrada"
        if self.state.halted_reason:
            return False, self.state.halted_reason
        return True, ""

    def register_trade(self, ts: datetime, pnl: float) -> None:
        """Registra uma operação encerrada (pnl líquido em R$)."""
        self.roll_day(ts)
        s = self.state
        s.pnl += pnl
        s.trades += 1
        s.consecutive_losses = s.consecutive_losses + 1 if pnl < 0 else 0

        if s.pnl <= -abs(self.risk.max_daily_loss):
            s.halted_reason = f"perda diária atingida (R$ {s.pnl:.2f})"
        elif s.trades >= self.risk.max_trades_per_day:
            s.halted_reason = "limite de operações do dia"
        elif s.consecutive_losses >= self.risk.max_consecutive_losses:
            s.halted_reason = f"{s.consecutive_losses} perdas seguidas"
