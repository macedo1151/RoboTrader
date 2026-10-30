from dataclasses import replace
from datetime import datetime

import pandas as pd
import pytest

from robotrader.broker.paper import ReplayFeed, build_paper_broker
from robotrader.config import PortfolioGroup, load_config
from robotrader.contracts import get_spec
from robotrader.data import synthetic_bars
from robotrader.execution import ExecutionModel
from robotrader.live import LiveTrader
from robotrader.portfolio import apply_net_rule, net_rule, run_portfolio

TS = datetime(2026, 3, 2, 9, 5)


# ---------------- trailing
@pytest.fixture
def model():
    return ExecutionModel(get_spec("WIN"), stop_points=1000, target_points=0, slippage_ticks=0)


def test_trailing_activates_after_distance_and_follows(model):
    pos = model.open(+1, 1, 100_000, TS, stop_points=1000, trail_points=300)
    assert pos.stop == 99_000 and pos.best == 100_000
    assert model.check_bar(pos, 100_000, 100_200, 99_950) is None
    assert pos.stop == 99_000                      # andou só 200 < 300: não ativa
    model.check_bar(pos, 100_200, 100_400, 100_150)
    assert pos.stop == 100_100                     # melhor 100.400 - 300
    model.check_bar(pos, 100_400, 100_350, 100_200)
    assert pos.stop == 100_100                     # nunca recua
    price, reason = model.check_bar(pos, 100_200, 100_250, 100_000)
    assert reason == "stop" and price == 100_100


def test_trailing_checks_old_stop_before_updating(model):
    """Na mesma barra, primeiro vale o stop antigo; o novo nível só vale nas próximas."""
    pos = model.open(-1, 1, 100_000, TS, stop_points=1000, trail_points=300)
    assert model.check_bar(pos, 100_000, 100_050, 99_600) is None
    assert pos.stop == 99_900                      # vendido: melhor 99.600 + 300


def test_no_trailing_keeps_old_behavior(model):
    pos = model.open(+1, 1, 100_000, TS, stop_points=1000)
    model.check_bar(pos, 100_000, 102_000, 99_990)
    assert pos.stop == 99_000 and pos.trail_dist is None


# ---------------- regra da posição líquida
def test_net_rule_cases():
    assert net_rule({"a": 1, "b": -1}, 2) == {"a": (0, 0.0), "b": (0, 0.0)}
    assert net_rule({"a": 1, "b": 1}, 2) == {"a": (1, 2), "b": (1, 2)}
    assert net_rule({"a": -1, "b": 0}, 2) == {"a": (-1, 1.0), "b": (0, 1.0)}
    assert net_rule({"a": 0, "b": 0}, 2) == {"a": (0, 1.0), "b": (0, 1.0)}


def test_vectorized_rule_matches_scalar():
    raw = pd.DataFrame({"a": [1, 1, -1, 0, 0], "b": [-1, 1, 0, 1, 0]})
    sig, mult = apply_net_rule(raw, 2.0)
    for i in range(len(raw)):
        exp = net_rule(raw.iloc[i].to_dict(), 2.0)
        for k in raw.columns:
            assert sig.iloc[i][k] == exp[k][0]
            if exp[k][0]:
                assert mult.iloc[i][k] == exp[k][1]


def test_config_groups():
    cfg = load_config()
    assert [g.members for g in cfg.portfolios] == [["WIN", "WIN_REV"], ["WIN_TR", "WIN_REV_TR"]]
    assert cfg.group_of("WIN_REV").name == "principal"
    assert cfg.instruments["WIN_TR"].trail_atr == 4.0


def test_config_rejects_unknown_or_mixed_members(tmp_path):
    src = open(load_config.__globals__["DEFAULT_CONFIG"], encoding="utf-8").read()
    bad = tmp_path / "bad.toml"
    bad.write_text(src + '\n[[portfolio]]\nname = "x"\nmembers = ["NAO_EXISTE"]\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(bad)
    bad.write_text(src.replace('members = ["WIN", "WIN_REV"]', 'members = ["WIN", "WDO"]'),
                   encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(bad)


# ---------------- grupo: backtest x loop ao vivo
@pytest.fixture
def group_cfg(cfg):
    common = dict(stop_points=0, target_points=0, stop_atr=10.0, trail_atr=3.0)
    gap = replace(cfg.instruments["WIN"], name="G", strategy_name="external_gap",
                  strategy_params={"external": "WSP$N", "lookback": 40}, **common)
    rev = replace(cfg.instruments["WIN"], name="R", strategy_name="prev_day_reversal",
                  strategy_params={"lookback": 40}, **common)
    return replace(cfg, instruments={"G": gap, "R": rev},
                   portfolios=[PortfolioGroup("teste", ["G", "R"], 2.0)])


def test_group_replay_matches_portfolio_backtest(group_cfg):
    win = synthetic_bars(get_spec("WIN"), 130_000, days=90, seed=31)
    wsp = synthetic_bars(get_spec("WIN"), 5_000, days=90, seed=131)
    bt = run_portfolio(win, group_cfg, group_cfg.portfolios[0], external={"WSP$N": wsp})
    feed = ReplayFeed(win, extra={"WSP$N": wsp})
    broker = build_paper_broker(list(group_cfg.instruments.values()))
    trader = LiveTrader.build(group_cfg, feed, broker, ["G", "R"])
    trader.kill_switch = trader.kill_switch.with_name("STOP_inexistente_teste")
    while not feed.finished:
        feed.advance()
        trader.step()
    live = pd.DataFrame([t.__dict__ for t in broker.trades])
    total_conflict_or_agree = 0
    for name, res in bt.items():
        b = res.trades
        lv = live[live.symbol == name]
        assert list(zip(pd.to_datetime(lv.entry_time).dt.date, lv.side, lv.qty)) == \
            list(zip(b.entry_time.dt.date, b.side, b.qty)), name
        total_conflict_or_agree += (b.qty == 2).sum()
    assert total_conflict_or_agree > 0  # houve dias em que as duas concordaram


def test_group_waits_for_member_that_needs_external_data(group_cfg):
    """Se a estratégia do S&P ainda espera o WSP, a outra não entra antes da decisão."""
    win = synthetic_bars(get_spec("WIN"), 130_000, days=60, seed=5)
    wsp = synthetic_bars(get_spec("WIN"), 5_000, days=60, seed=105)
    last = win.index[-1].date()
    nine = win.index.get_loc(win[pd.Index(win.index.date) == last].index[0])
    feed = ReplayFeed(win, warmup=nine + 1,
                      extra={"WSP$N": wsp[pd.Index(wsp.index.date) != last]})
    broker = build_paper_broker(list(group_cfg.instruments.values()))
    trader = LiveTrader.build(group_cfg, feed, broker, ["G", "R"])
    trader.max_waits = 3
    trader.step()
    r = next(x for x in trader.runners if x.inst.name == "R")
    g = next(x for x in trader.runners if x.inst.name == "G")
    assert r.last_bar is not None and g.last_bar is None   # R processou, G aguarda o WSP
    assert broker.position("R") is None                     # R não entrou sozinho
    for _ in range(4):
        trader.step()
    assert g.last_bar is not None and not trader.pending    # G desistiu; decisão tomada
