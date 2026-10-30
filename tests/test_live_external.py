from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from robotrader.backtest import run_backtest
from robotrader.broker.paper import ReplayFeed, build_paper_broker
from robotrader.contracts import get_spec
from robotrader.data import synthetic_bars
from robotrader.live import LiveTrader
from robotrader.strategies import ExternalGapFade


@pytest.fixture
def gap_cfg(cfg, tmp_path):
    inst = replace(cfg.instruments["WIN"], strategy_name="external_gap",
                   strategy_params={"external": "WSP$N", "exit_at": "13:00",
                                    "threshold_q": 0.5, "lookback": 40},
                   stop_points=0, target_points=0, stop_atr=10.0)
    return replace(cfg, instruments={"WIN": inst})


def make_markets(days=90, seed=3):
    win = synthetic_bars(get_spec("WIN"), 130_000, days=days, seed=seed)
    wsp = synthetic_bars(get_spec("WIN"), 5_000, days=days, seed=seed + 100)
    return win, wsp


def run_replay(cfg, win, wsp, journal=None):
    feed = ReplayFeed(win, extra={"WSP$N": wsp})
    broker = build_paper_broker([cfg.instruments["WIN"]], journal)
    trader = LiveTrader.build(cfg, feed, broker, ["WIN"])
    trader.kill_switch = trader.kill_switch.with_name("STOP_inexistente_teste")
    while not feed.finished:
        feed.advance()
        trader.step()
    return broker


def test_replay_feed_extra_is_time_synced():
    win, wsp = make_markets(days=2)
    feed = ReplayFeed(win, warmup=10, extra={"WSP$N": wsp})
    ext = feed.get_bars("WSP$N", "M5", 1000)
    assert ext.index[-1] <= win.index[9]
    assert ext.index[-1] == win.index[9]  # mesma barra já fechada nos dois mercados


def test_live_replay_matches_backtest_with_external(gap_cfg):
    win, wsp = make_markets()
    bt = run_backtest(win, gap_cfg.instruments["WIN"], gap_cfg, external={"WSP$N": wsp})
    broker = run_replay(gap_cfg, win, wsp)
    assert len(bt.trades) > 5
    live = pd.DataFrame([t.__dict__ for t in broker.trades])
    # mesmos dias e mesmos lados
    bt_days = list(zip(bt.trades.entry_time.dt.date, bt.trades.side))
    live_days = list(zip(pd.to_datetime(live.entry_time).dt.date, live.side))
    assert bt_days == live_days
    # resultado próximo (simulado entra no fechamento das 9:00, backtest na abertura das 9:05)
    assert live.net.sum() == pytest.approx(bt.trades.net.sum(), abs=0.15 * bt.trades.net.abs().sum())


def test_waits_for_external_bar_at_open(gap_cfg):
    win, wsp = make_markets(days=60)
    last_day = win.index[-1].date()
    wsp_missing_today = wsp[pd.Index(wsp.index.date) != last_day]
    # para o replay exatamente na barra das 9:00 do último dia
    nine = win.index.get_loc(win[(pd.Index(win.index.date) == last_day)].index[0])
    feed = ReplayFeed(win, warmup=nine + 1, extra={"WSP$N": wsp_missing_today})
    broker = build_paper_broker([gap_cfg.instruments["WIN"]])
    trader = LiveTrader.build(gap_cfg, feed, broker, ["WIN"])
    trader.max_waits = 2
    trader.step()
    assert trader.runners[0].last_bar is None and trader.runners[0].waits == 1  # aguardando
    trader.step()
    trader.step()  # desiste após max_waits e segue sem o dado
    assert trader.runners[0].last_bar == win.index[nine].to_pydatetime()
    assert broker.position("WIN") is None  # sem gap do dia, não entra


def test_paper_journal_survives_restart(gap_cfg, tmp_path):
    win, wsp = make_markets(days=60)
    broker = run_replay(gap_cfg, win, wsp, journal=tmp_path)
    assert (tmp_path / "paper_trades.csv").exists()
    reloaded = build_paper_broker([gap_cfg.instruments["WIN"]], tmp_path)
    assert len(reloaded.trades) == len(broker.trades) > 0
    assert reloaded.trades[0] == broker.trades[0]
    # posição aberta também é restaurada
    reloaded.last_bar["WIN"] = (win.index[-1].to_pydatetime(), 130_000.0)
    reloaded.open("WIN", 1, 1, 0, 0)
    again = build_paper_broker([gap_cfg.instruments["WIN"]], tmp_path)
    assert again.position("WIN").side == 1


def test_config_strategy_builds_from_toml():
    from robotrader.config import load_config

    cfg = load_config()
    inst = cfg.instruments["WIN"]
    assert inst.strategy_name == "external_gap"
    from robotrader.strategies import get_strategy
    s = get_strategy(inst.strategy_name, inst.strategy_params)
    assert isinstance(s, ExternalGapFade) and s.external_symbols == ["WSP$N"]
    assert not cfg.instruments["WDO"].enabled
