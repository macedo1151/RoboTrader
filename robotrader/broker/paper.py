"""Corretora simulada e fonte de dados de replay (para rodar o loop ao vivo offline)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..contracts import get_spec
from ..execution import ExecutionModel, Position, Trade
from .base import Broker, ClosedTrade, DataFeed, OpenPosition


class ReplayFeed(DataFeed):
    """Entrega barras históricas uma a uma, como se fossem chegando ao vivo."""

    def __init__(self, bars: pd.DataFrame, warmup: int = 50,
                 extra: dict[str, pd.DataFrame] | None = None) -> None:
        """`extra`: outros símbolos (ex.: {"WSP$N": barras}), entregues em sincronia:
        só as barras que já fecharam até a barra atual do ativo principal."""
        self.bars = bars
        self.extra = extra or {}
        self.cursor = min(warmup, len(bars))

    @property
    def finished(self) -> bool:
        return self.cursor >= len(self.bars)

    def advance(self) -> None:
        self.cursor += 1

    def get_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        end = min(self.cursor, len(self.bars))
        if symbol in self.extra:
            df = self.extra[symbol]
            if end == 0:
                return df.iloc[:0]
            j = df.index.searchsorted(self.bars.index[end - 1], side="right")
            return df.iloc[max(0, j - count):j]
        return self.bars.iloc[max(0, end - count):end]


class PaperBroker(Broker):
    """Executa ordens em memória usando o mesmo modelo de execução do backtest."""

    def __init__(self, models: dict[str, ExecutionModel],
                 journal_dir: str | Path | None = None) -> None:
        """`journal_dir`: grava operações (paper_trades.csv) e posições abertas
        (paper_state.json) para sobreviver a reinícios do robô."""
        self.models = models
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.last_bar: dict[str, tuple[datetime, float]] = {}
        self.journal_dir = Path(journal_dir) if journal_dir else None
        if self.journal_dir:
            self.journal_dir.mkdir(parents=True, exist_ok=True)
            self._load_journal()

    # ---------- diário ----------
    @property
    def _trades_path(self) -> Path:
        return self.journal_dir / "paper_trades.csv"

    @property
    def _state_path(self) -> Path:
        return self.journal_dir / "paper_state.json"

    def _load_journal(self) -> None:
        if self._trades_path.exists():
            df = pd.read_csv(self._trades_path, parse_dates=["entry_time", "exit_time"])
            for row in df.to_dict("records"):
                row["entry_time"] = row["entry_time"].to_pydatetime()
                row["exit_time"] = row["exit_time"].to_pydatetime()
                self.trades.append(Trade(**row))
        if self._state_path.exists():
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            for sym, pos in raw.items():
                pos["entry_time"] = datetime.fromisoformat(pos["entry_time"])
                self.positions[sym] = Position(**pos)

    def _save_state(self) -> None:
        if not self.journal_dir:
            return
        data = {s: {**asdict(pos), "entry_time": pos.entry_time.isoformat()}
                for s, pos in self.positions.items()}
        self._state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _append_trade(self, trade: Trade) -> None:
        if not self.journal_dir:
            return
        pd.DataFrame([asdict(trade)]).to_csv(
            self._trades_path, mode="a", header=not self._trades_path.exists(), index=False)

    def on_bar(self, symbol: str, ts: datetime, o: float, h: float, lo: float, c: float) -> None:
        """Atualiza preço e verifica stop/alvo da posição aberta."""
        pos = self.positions.get(symbol)
        if pos is not None and ts > pos.entry_time:
            hit = self.models[symbol].check_bar(pos, o, h, lo)
            if hit:
                self._close(symbol, hit[0], ts, hit[1])
        self.last_bar[symbol] = (ts, c)

    def position(self, symbol: str) -> OpenPosition | None:
        pos = self.positions.get(symbol)
        return OpenPosition(pos.side, pos.qty, pos.entry_price) if pos else None

    def open(self, symbol, side, qty, stop_points, target_points, comment="",
             trail_points=0.0) -> bool:
        ts, price = self.last_bar[symbol]
        model = self.models[symbol]
        self.positions[symbol] = model.open(side, qty, price, ts, stop_points, target_points,
                                            trail_points)
        self._save_state()
        return True

    def close(self, symbol, reason="") -> bool:
        pos = self.positions.get(symbol)
        if pos is None:
            return False
        ts, price = self.last_bar[symbol]
        self._close(symbol, self.models[symbol].market_exit_price(pos, price), ts, reason)
        return True

    def _close(self, symbol: str, price: float, ts: datetime, reason: str) -> None:
        pos = self.positions.pop(symbol)
        trade = self.models[symbol].close(symbol, pos, price, ts, reason)
        self.trades.append(trade)
        self._append_trade(trade)
        self._save_state()

    def closed_trades_today(self, symbol, today) -> list[ClosedTrade]:
        return [
            ClosedTrade(t.exit_time, t.net)
            for t in self.trades
            if t.symbol == symbol and t.exit_time.date() == today.date()
        ]


def build_paper_broker(instruments, journal_dir: str | Path | None = None) -> PaperBroker:
    models = {
        inst.name: ExecutionModel(
            spec=get_spec(inst.root),
            stop_points=inst.stop_points,
            target_points=inst.target_points,
            slippage_ticks=inst.slippage_ticks,
            cost_per_contract=inst.cost_per_contract,
        )
        for inst in instruments
    }
    return PaperBroker(models, journal_dir)
