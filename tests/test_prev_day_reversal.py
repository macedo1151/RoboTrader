from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from robotrader.__main__ import main
from robotrader.backtest import run_backtest
from robotrader.broker.paper import ReplayFeed, build_paper_broker
from robotrader.config import load_config
from robotrader.contracts import get_spec
from robotrader.data import synthetic_bars
from robotrader.live import LiveTrader
from robotrader.strategies import PrevDayReversal
from robotrader.strategies.prev_day_reversal import daily_moves


def session(day: str, open_: float, close: float, n: int = 110) -> pd.DataFrame:
    """Pregão M5 de 9:00 a 18:05 que vai linearmente de open_ a close."""
    idx = pd.date_range(f"{day} 09:00", periods=n, freq="5min", name="datetime")
    px = np.linspace(open_, close, n)
    return pd.DataFrame({"open": px, "high": px + 5, "low": px - 5, "close": px,
                         "volume": 100.0}, index=idx)


def test_daily_moves_uses_0900_open_and_1740_close():
    bars = session("2026-03-02", 100_000, 101_100)
    m = daily_moves(bars)
    close_1740 = bars.loc["2026-03-02 17:40", "close"]
    assert m.iloc[0] == pytest.approx(close_1740 - 100_000)


def test_signal_fades_previous_big_day_on_next_open():
    days = pd.bdate_range("2026-01-05", periods=30)
    parts = [session(str(d.date()), 100_000, 100_000 + (100 if i < 29 else 0))
             for i, d in enumerate(days)]
    # dia 28: movimento enorme de alta -> dia 29 deve vender na barra das 9:00
    parts[28] = session(str(days[28].date()), 100_000, 103_000)
    bars = pd.concat(parts)
    sig = PrevDayReversal(threshold_q=0.5, lookback=20).signals(bars)
    nonzero = sig[sig != 0]
    assert nonzero.index[-1] == pd.Timestamp(f"{days[29].date()} 09:00")
    assert nonzero.iloc[-1] == -1
    assert (sig[sig.index.time != pd.Timestamp("09:00").time()] == 0).all()


def test_no_lookahead():
    bars = synthetic_bars(get_spec("WIN"), 130_000, days=70, seed=8)
    strat = PrevDayReversal(lookback=30)
    full = strat.generate(bars)
    for cut in (3000, 6000):
        part = PrevDayReversal(lookback=30).generate(bars.iloc[:cut])
        pd.testing.assert_series_equal(part, full.iloc[:cut])


def test_config_has_two_win_entries():
    cfg = load_config()
    assert cfg.instruments["WIN"].root == "WIN" and cfg.instruments["WIN"].name == "WIN"
    rev = cfg.instruments["WIN_REV"]
    assert rev.root == "WIN" and rev.name == "WIN_REV" and rev.enabled
    assert rev.strategy_name == "prev_day_reversal"


def test_two_strategies_same_contract_keep_separate_positions(cfg):
    a = replace(cfg.instruments["WIN"], name="A")
    b = replace(cfg.instruments["WIN"], name="B")
    broker = build_paper_broker([a, b])
    ts = pd.Timestamp("2026-03-02 09:00").to_pydatetime()
    for name in ("A", "B"):
        broker.on_bar(name, ts, 100_000, 100_010, 99_990, 100_000)
    broker.open("A", 1, 1, 0, 0)
    broker.open("B", -1, 1, 0, 0)
    assert broker.position("A").side == 1 and broker.position("B").side == -1
    broker.close("A")
    assert broker.position("A") is None and broker.position("B").side == -1


def test_replay_of_both_strategies_matches_backtests(cfg):
    win = synthetic_bars(get_spec("WIN"), 130_000, days=80, seed=21)
    wsp = synthetic_bars(get_spec("WIN"), 5_000, days=80, seed=121)
    common = dict(stop_points=0, target_points=0, stop_atr=10.0)
    gap = replace(cfg.instruments["WIN"], name="WIN", strategy_name="external_gap",
                  strategy_params={"external": "WSP$N", "lookback": 40}, **common)
    rev = replace(cfg.instruments["WIN"], name="WIN_REV", strategy_name="prev_day_reversal",
                  strategy_params={"lookback": 40}, **common)
    c2 = replace(cfg, instruments={"WIN": gap, "WIN_REV": rev})

    bt_gap = run_backtest(win, gap, c2, external={"WSP$N": wsp}).trades
    bt_rev = run_backtest(win, rev, c2).trades
    assert len(bt_gap) > 3 and len(bt_rev) > 3

    feed = ReplayFeed(win, extra={"WSP$N": wsp})
    broker = build_paper_broker([gap, rev])
    trader = LiveTrader.build(c2, feed, broker, ["WIN", "WIN_REV"])
    trader.kill_switch = trader.kill_switch.with_name("STOP_inexistente_teste")
    while not feed.finished:
        feed.advance()
        trader.step()
    live = pd.DataFrame([t.__dict__ for t in broker.trades])
    for name, bt in [("WIN", bt_gap), ("WIN_REV", bt_rev)]:
        lv = live[live.symbol == name]
        assert list(zip(pd.to_datetime(lv.entry_time).dt.date, lv.side)) == \
            list(zip(bt.entry_time.dt.date, bt.side)), name


def test_mt5_mode_refuses_two_strategies_on_same_contract(tmp_path):
    src = open(load_config.__globals__["DEFAULT_CONFIG"], encoding="utf-8").read()
    cfg_file = tmp_path / "mt5.toml"
    cfg_file.write_text(src.replace('mode = "paper"', 'mode = "mt5"'), encoding="utf-8")
    assert main(["--config", str(cfg_file), "live", "--confirm"]) == 2
