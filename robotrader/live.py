"""Loop do robô ao vivo (simulado ou MT5).

A cada nova barra fechada:
1. (paper) atualiza stop/alvo da posição simulada;
2. reconstrói os limites diários a partir das operações do dia na corretora
   (sobrevive a reinícios do robô);
3. zera no horário de zeragem ou se existir o arquivo STOP (botão de pânico);
4. calcula o sinal de entrada de cada estratégia;
5. estratégias de um mesmo grupo de carteira decidem JUNTAS (posição líquida, ver
   portfolio.py): a entrada só é executada quando todos os membros já processaram a
   mesma barra; depois cada uma entra se o risco permitir.
"""

from __future__ import annotations

import logging
import time as systime
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .broker.base import Broker, DataFeed
from .broker.paper import PaperBroker, ReplayFeed
from .config import Config, InstrumentConfig
from .contracts import active_contract
from .data import TIMEFRAME_MINUTES
from .execution import stop_target_points, trail_points
from .portfolio import net_rule
from .indicators import atr
from .risk import DayState, RiskManager
from .strategies import Strategy, get_strategy

log = logging.getLogger("robotrader")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KILL_SWITCH = PROJECT_ROOT / "STOP"


def setup_logging(log_dir: str) -> None:
    path = PROJECT_ROOT / log_dir
    path.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(path / f"robo_{date.today():%Y%m%d}.log", encoding="utf-8"),
        ],
    )


@dataclass
class InstrumentRunner:
    inst: InstrumentConfig
    strategy: Strategy
    risk: RiskManager
    data_symbol: str
    order_symbol: str
    last_bar: datetime | None = None
    lookback: int = 300
    waits: int = 0  # tentativas aguardando dado externo da barra atual


@dataclass
class PendingEntry:
    runner: InstrumentRunner
    ts: datetime          # abertura da barra que gerou o sinal
    now: datetime         # fechamento dessa barra
    sig: int
    stop_pts: float
    target_pts: float
    trail_pts: float


@dataclass
class LiveTrader:
    cfg: Config
    feed: DataFeed
    broker: Broker
    runners: list[InstrumentRunner] = field(default_factory=list)
    kill_switch: Path = KILL_SWITCH
    max_waits: int = 12  # ciclos (~1 min com poll de 5 s) esperando o dado externo
    pending: dict[str, PendingEntry] = field(default_factory=dict)

    @classmethod
    def build(cls, cfg: Config, feed: DataFeed, broker: Broker,
              roots: list[str], today: date | None = None) -> "LiveTrader":
        today = today or date.today()
        paper = isinstance(broker, PaperBroker)
        runners = []
        for root in roots:
            inst = cfg.instruments[root]
            if paper:
                order_symbol = inst.name  # posição simulada própria de cada entrada
                data_symbol = (inst.root if isinstance(feed, ReplayFeed)
                               else (inst.data_symbol or inst.root))
            else:
                data_symbol = inst.data_symbol or inst.root
                order_symbol = inst.contract or active_contract(inst.root, today)
            strategy = get_strategy(inst.strategy_name, inst.strategy_params)
            runners.append(InstrumentRunner(
                inst=inst,
                strategy=strategy,
                risk=RiskManager(cfg.risk, cfg.session),
                data_symbol=data_symbol,
                order_symbol=order_symbol,
                lookback=max(300, strategy.history_bars),
            ))
            log.info("%s: dados=%s ordens=%s estratégia=%s",
                     root, data_symbol, order_symbol, runners[-1].strategy)
        return cls(cfg, feed, broker, runners)

    # ------------------------------------------------------------------
    def _sync_risk(self, r: InstrumentRunner, now: datetime) -> None:
        r.risk.state = DayState(day=now.date())
        for t in sorted(self.broker.closed_trades_today(r.order_symbol, now),
                        key=lambda t: t.exit_time):
            r.risk.register_trade(t.exit_time, t.net)

    def step(self) -> None:
        bar_minutes = TIMEFRAME_MINUTES[self.cfg.timeframe]
        for r in self.runners:
            entry = self._process(r, bar_minutes)
            if entry is not None:
                self.pending[r.inst.name] = entry
        self._execute_pending()

    def _runner(self, name: str) -> InstrumentRunner | None:
        return next((r for r in self.runners if r.inst.name == name), None)

    def _execute_pending(self) -> None:
        for name in list(self.pending):
            e = self.pending.get(name)
            if e is None:
                continue
            group = self.cfg.group_of(name)
            members = [m for m in (group.members if group else [name]) if self._runner(m)]
            # espera todos os membros do grupo processarem a mesma barra
            if any((self._runner(m).last_bar or datetime.min) < e.ts for m in members):
                continue
            sigs = {m: (self.pending[m].sig if m in self.pending and self.pending[m].ts == e.ts
                        else 0) for m in members}
            if group and len(members) > 1:
                decided = net_rule(sigs, group.agree_multiplier)
                active = {m: s for m, s in sigs.items() if s}
                if len(active) > 1:
                    log.info("carteira %s: sinais %s -> %s", group.name, active,
                             "conflito, nenhuma entra" if all(v[0] == 0 for v in decided.values())
                             else f"concordam, {group.agree_multiplier:g}x contratos")
            else:
                decided = {name: (e.sig, 1.0)}
            for m in members:
                pe = self.pending.pop(m, None)
                if pe is None or pe.ts != e.ts:
                    continue
                side, mult = decided[m]
                if side == 0:
                    log.info("%s: sinal %+d cancelado pela regra da carteira", m, pe.sig)
                    continue
                self._enter(pe, side, mult)

    def _enter(self, e: PendingEntry, side: int, mult: float) -> None:
        r = e.runner
        ok, reason = r.risk.can_enter(e.now)
        if not ok:
            log.info("%s: sinal %+d ignorado (%s)", r.order_symbol, side, reason)
            return
        qty = max(1, int(round(self.cfg.risk.contracts * mult)))
        log.info("%s: entrando %s %d contrato(s) (stop %.1f, alvo %.1f, trailing %.1f pts)",
                 r.order_symbol, "COMPRA" if side > 0 else "VENDA", qty,
                 e.stop_pts, e.target_pts, e.trail_pts)
        self.broker.open(r.order_symbol, side, qty, e.stop_pts, e.target_pts,
                         comment=f"robo {r.strategy.name}", trail_points=e.trail_pts)

    def _process(self, r: InstrumentRunner, bar_minutes: int) -> PendingEntry | None:
        """Processa uma barra nova da estratégia; devolve a entrada desejada, se houver."""
        bars = self.feed.get_bars(r.data_symbol, self.cfg.timeframe, r.lookback)
        if bars.empty:
            return None
        last_ts = bars.index[-1].to_pydatetime()
        if r.last_bar is not None and last_ts <= r.last_bar:
            return None  # nenhuma barra nova

        if r.strategy.external_symbols:
            ext = {s: self.feed.get_bars(s, self.cfg.timeframe, r.strategy.external_bars)
                   for s in r.strategy.external_symbols}
            r.strategy.update_external(ext)
            if not r.strategy.ready(bars):
                r.waits += 1
                if r.waits <= self.max_waits:
                    if r.waits == 1:
                        log.info("%s: aguardando dado de %s para a barra %s",
                                 r.order_symbol, r.strategy.external_symbols, last_ts)
                    return None
                log.warning("%s: dado de %s não chegou; seguindo sem ele",
                            r.order_symbol, r.strategy.external_symbols)
        r.waits = 0
        r.last_bar = last_ts
        now = last_ts + timedelta(minutes=bar_minutes)  # horário de fechamento da barra

        if isinstance(self.broker, PaperBroker):
            b = bars.iloc[-1]
            self.broker.on_bar(r.order_symbol, last_ts, b.open, b.high, b.low, b.close)

        self._sync_risk(r, now)
        pos = self.broker.position(r.order_symbol)
        log.info("%s: barra %s fechou %.1f | posição: %s | dia: R$ %.2f em %d ops%s",
                 r.order_symbol, f"{last_ts:%H:%M}", bars["close"].iloc[-1],
                 "nenhuma" if pos is None else
                 f"{'COMPRADO' if pos.side > 0 else 'VENDIDO'} {pos.qty} @ {pos.entry_price:.1f}",
                 r.risk.state.pnl, r.risk.state.trades,
                 f" | bloqueado: {r.risk.state.halted_reason}" if r.risk.state.halted_reason else "")

        if self.kill_switch.exists():
            if pos:
                log.warning("%s: arquivo STOP encontrado — zerando", r.order_symbol)
                self.broker.close(r.order_symbol, "kill switch")
            return None

        if r.risk.must_flatten(now):
            if pos:
                log.info("%s: horário de zeragem", r.order_symbol)
                self.broker.close(r.order_symbol, "zeragem")
            return None

        if len(bars) < r.strategy.min_bars:
            return None

        exits = r.strategy.exits(bars)
        if pos and exits is not None:
            should_exit = bool(exits[0].iloc[-1] if pos.side > 0 else exits[1].iloc[-1])
            if should_exit:
                log.info("%s: sinal de saída", r.order_symbol)
                self.broker.close(r.order_symbol, "saída")
                self._sync_risk(r, now)
                pos = None

        sig = int(r.strategy.generate(bars).iloc[-1])
        if r.strategy.name == "external_gap" and last_ts.time() == time(9, 0):
            gap = r.strategy.gaps.get(last_ts.date())
            log.info("%s: gap noturno do S&P hoje = %s -> sinal %+d", r.order_symbol,
                     "indisponível" if gap is None else f"{100 * gap:+.2f}%", sig)
        if sig == 0 or (pos and pos.side == sig):
            return None

        if pos:
            log.info("%s: inversão de %+d para %+d", r.order_symbol, pos.side, sig)
            self.broker.close(r.order_symbol, "inversão")
            self._sync_risk(r, now)

        atr_now = float(atr(bars, r.inst.atr_period).iloc[-1])
        stop_pts, target_pts = stop_target_points(r.inst, atr_now)
        return PendingEntry(r, last_ts, now, sig, stop_pts, target_pts,
                            trail_points(r.inst, atr_now))

    def run_forever(self, poll_seconds: float = 5.0) -> None:
        log.info("Robô iniciado (modo %s). Crie o arquivo %s para zerar e parar.",
                 self.cfg.mode, self.kill_switch)
        failures = 0
        while True:
            try:
                self.step()
                failures = 0
            except KeyboardInterrupt:
                raise
            except Exception:
                failures += 1
                log.exception("Erro no ciclo do robô (%d seguido(s))", failures)
                self._try_reconnect(failures)
            systime.sleep(poll_seconds if failures == 0 else min(60, 5 * failures))

    def _try_reconnect(self, failures: int) -> None:
        """Se a fonte de dados é o MT5, tenta reconectar (terminal fechado/reiniciado)."""
        connector = self.feed
        if not hasattr(connector, "connect") or failures % 3:
            return
        try:
            connector.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            connector.connect(trading=not getattr(connector, "read_only", True))
            log.info("Reconectado ao MT5")
        except Exception as exc:  # noqa: BLE001
            log.error("Falha ao reconectar ao MT5: %s", exc)
