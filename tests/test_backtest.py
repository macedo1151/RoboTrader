from dataclasses import replace

import pandas as pd
import pytest

from robotrader.backtest import run_backtest
from robotrader.broker.paper import ReplayFeed, build_paper_broker
from robotrader.contracts import get_spec
from robotrader.data import synthetic_bars
from robotrader.live import LiveTrader
from robotrader.strategies.base import Strategy
from conftest import make_bars


class Fixed(Strategy):
    """Estratégia de teste: sinais em posições pré-definidas."""
    name = "fixed"

    def __init__(self, at: dict[int, int]):
        super().__init__(at=at)
        self.at = at

    def signals(self, bars):
        s = pd.Series(0, index=bars.index, dtype=int)
        for i, v in self.at.items():
            if i < len(bars):
                s.iloc[i] = v
        return s


def flat_bars(n, start="2026-03-02 10:00", price=130_000):
    return make_bars([(price, price + 10, price - 10, price)] * n, start=start)


def test_entry_at_next_open_and_target(cfg):
    inst = replace(cfg.instruments["WIN"], stop_points=100, target_points=100,
                   slippage_ticks=0, cost_per_contract=0)
    bars = flat_bars(5)
    bars.iloc[3] = [130_000, 130_150, 129_990, 130_120, 100]
    res = run_backtest(bars, inst, cfg, Fixed({0: 1}))
    t = res.trades.iloc[0]
    assert t.entry_time == bars.index[1]  # executa na barra seguinte ao sinal
    assert t.reason == "alvo" and t.points == 100
    assert t.net == pytest.approx(20.0)


def test_flatten_at_end_of_session(cfg):
    inst = replace(cfg.instruments["WIN"], stop_points=0, target_points=0)
    bars = flat_bars(14, start="2026-03-02 16:50")  # 16:50 .. 17:55
    res = run_backtest(bars, inst, cfg, Fixed({0: 1}))
    assert len(res.trades) == 1
    assert res.trades.iloc[0].reason == "zeragem"
    assert res.trades.iloc[0].exit_time == pd.Timestamp("2026-03-02 17:50")


def test_reversal(cfg):
    inst = replace(cfg.instruments["WIN"], stop_points=0, target_points=0)
    res = run_backtest(flat_bars(10), inst, cfg, Fixed({0: 1, 4: -1}))
    assert res.trades.iloc[0].reason == "inversão"
    assert res.trades.iloc[1].side == -1


def test_no_entry_after_last_entry_time(cfg):
    res = run_backtest(flat_bars(5, start="2026-03-02 17:10"), cfg.instruments["WIN"], cfg,
                       Fixed({0: 1}))
    assert res.trades.empty
    assert res.blocked


def test_daily_loss_limit_respected(cfg):
    """Nenhum dia perde muito mais que o limite (+ uma operação de folga)."""
    bars = synthetic_bars(get_spec("WIN"), 130_000, days=40, seed=7)
    res = run_backtest(bars, cfg.instruments["WIN"], cfg)
    daily = res.trades.groupby(res.trades.exit_time.dt.date)["net"].sum()
    worst_single = res.trades["net"].min()
    assert daily.min() >= -cfg.risk.max_daily_loss + worst_single


@pytest.mark.parametrize("root", ["WIN", "WDO"])
def test_replay_matches_backtest(cfg, root):
    """O loop ao vivo (paper) deve reproduzir o backtest de perto."""
    bars = synthetic_bars(get_spec(root), 130_000 if root == "WIN" else 5_500, days=15, seed=3)
    bt = run_backtest(bars, cfg.instruments[root], cfg)

    feed = ReplayFeed(bars)
    broker = build_paper_broker([cfg.instruments[root]])
    trader = LiveTrader.build(cfg, feed, broker, [root])
    trader.kill_switch = trader.kill_switch.with_name("STOP_inexistente_teste")
    while not feed.finished:
        feed.advance()
        trader.step()

    n_bt, n_live = len(bt.trades), len(broker.trades)
    assert n_bt > 0
    assert abs(n_bt - n_live) <= max(2, 0.1 * n_bt)
