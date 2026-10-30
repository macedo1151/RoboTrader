"""Motor de backtest barra a barra.

Fluxo em cada barra t:
1. novo dia -> reinicia limites diários (e encerra posição esquecida do dia anterior);
2. horário de zeragem -> sai a mercado na abertura;
3. sinal de saída da barra t-1 -> sai a mercado na abertura de t;
4. sinal de entrada da barra t-1 -> entra/inverte na abertura de t (se o risco permitir),
   com stop/alvo fixos em pontos ou proporcionais ao ATR da barra t-1;
5. confere stop/alvo com a máxima/mínima de t.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config, InstrumentConfig
from .contracts import get_spec
from .execution import ExecutionModel, Position, Trade, stop_target_points, trail_points
from .indicators import atr
from .metrics import equity_curve, summarize, trades_to_frame
from .risk import RiskManager
from .strategies import Strategy, get_strategy


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    trades: pd.DataFrame
    equity: pd.Series
    metrics: dict
    blocked: dict[str, int]


def run_backtest(
    bars: pd.DataFrame,
    inst: InstrumentConfig,
    cfg: Config,
    strategy: Strategy | None = None,
    trade_from: pd.Timestamp | None = None,
    external: dict[str, pd.DataFrame] | None = None,
    raw_signals: pd.Series | None = None,
    qty_mult: pd.Series | None = None,
) -> BacktestResult:
    """`trade_from`: barras anteriores servem só de aquecimento dos indicadores.
    `external`: barras de outros símbolos exigidos pela estratégia; se faltar, são
    lidas de data/external/.
    `raw_signals` / `qty_mult`: sinais e multiplicador de contratos já decididos por um
    grupo de carteira (ver portfolio.py), na barra do sinal."""
    spec = get_spec(inst.root)
    strategy = strategy or get_strategy(inst.strategy_name, inst.strategy_params)
    model = ExecutionModel(
        spec=spec,
        stop_points=inst.stop_points,
        target_points=inst.target_points,
        slippage_ticks=inst.slippage_ticks,
        cost_per_contract=inst.cost_per_contract,
    )
    risk = RiskManager(cfg.risk, cfg.session)
    qty = cfg.risk.contracts

    if strategy.external_symbols:
        if external is None:
            from .external import load_external
            external = load_external(strategy.external_symbols, cfg.timeframe)
        strategy.update_external(external)
    raw = strategy.generate(bars) if raw_signals is None else raw_signals.reindex(bars.index).fillna(0)
    mult = (qty_mult.reindex(bars.index).fillna(1.0) if qty_mult is not None
            else pd.Series(1.0, index=bars.index)).shift(1).fillna(1.0).to_numpy()
    if trade_from is not None:
        raw = raw.where(bars.index >= trade_from, 0)
    signals = raw.shift(1).fillna(0).astype(int)
    exits = strategy.exits(bars)
    if exits is None:
        exit_long = exit_short = np.zeros(len(bars), dtype=bool)
    else:
        exit_long = exits[0].shift(1, fill_value=False).fillna(False).to_numpy(dtype=bool)
        exit_short = exits[1].shift(1, fill_value=False).fillna(False).to_numpy(dtype=bool)
    atr_prev = atr(bars, inst.atr_period).shift(1).to_numpy()
    trades: list[Trade] = []
    blocked: dict[str, int] = {}
    pos: Position | None = None
    last_ts = last_close = None

    def close(price: float, ts, reason: str) -> None:
        nonlocal pos
        trade = model.close(inst.name, pos, price, ts, reason)
        trades.append(trade)
        risk.register_trade(ts, trade.net)
        pos = None

    for ts, o, h, lo, c, sig, xl, xs, a, m in zip(
        bars.index, bars["open"], bars["high"], bars["low"], bars["close"], signals,
        exit_long, exit_short, atr_prev, mult,
    ):
        ts = ts.to_pydatetime()

        # 1. virada de dia com posição aberta (não deveria ocorrer com zeragem)
        if pos is not None and last_ts is not None and ts.date() != last_ts.date():
            close(model.market_exit_price(pos, last_close), last_ts, "fim do dia")
        risk.roll_day(ts)

        # 2. zeragem obrigatória
        if risk.must_flatten(ts):
            if pos is not None:
                close(model.market_exit_price(pos, o), ts, "zeragem")
            last_ts, last_close = ts, c
            continue

        # 3. saída por sinal da estratégia (barra anterior)
        if pos is not None and ((pos.side > 0 and xl) or (pos.side < 0 and xs)):
            close(model.market_exit_price(pos, o), ts, "saída")

        # 4. entrada/inversão por sinal da barra anterior
        if sig != 0 and (pos is None or pos.side != sig):
            if pos is not None:
                close(model.market_exit_price(pos, o), ts, "inversão")
            ok, reason = risk.can_enter(ts)
            if ok:
                stop_pts, target_pts = stop_target_points(inst, a)
                n = max(1, int(round(qty * m)))
                pos = model.open(sig, n, o, ts, stop_pts, target_pts, trail_points(inst, a))
            else:
                blocked[reason.split(" (")[0]] = blocked.get(reason.split(" (")[0], 0) + 1

        # 5. stop / alvo
        if pos is not None:
            hit = model.check_bar(pos, o, h, lo)
            if hit:
                close(hit[0], ts, hit[1])

        last_ts, last_close = ts, c

    if pos is not None:
        close(model.market_exit_price(pos, last_close), last_ts, "fim dos dados")

    frame = trades_to_frame(trades)
    return BacktestResult(
        symbol=inst.name,
        strategy=repr(strategy),
        trades=frame,
        equity=equity_curve(frame),
        metrics=summarize(frame),
        blocked=blocked,
    )
