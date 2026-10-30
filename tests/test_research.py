from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from robotrader.backtest import run_backtest
from robotrader.contracts import get_spec
from robotrader.data import synthetic_bars
from robotrader.execution import stop_target_points
from robotrader.indicators import atr, session_vwap
from robotrader.research import Candidate, split_params, walk_forward
from robotrader.strategies import get_strategy
from conftest import make_bars


def series_bars(closes, **kw):
    return make_bars([(c, c + 5, c - 5, c) for c in closes], **kw)


# ---------------- indicadores ----------------
def test_vwap_resets_each_day():
    d1 = make_bars([(100, 100, 100, 100)] * 3, start="2026-03-02 09:00")
    d2 = make_bars([(200, 200, 200, 200)] * 3, start="2026-03-03 09:00")
    v = session_vwap(pd.concat([d1, d2]))
    assert v.iloc[2] == 100 and v.iloc[3] == 200


def test_atr_ignores_overnight_gap():
    d1 = make_bars([(100, 110, 90, 100)] * 3, start="2026-03-02 09:00")
    d2 = make_bars([(500, 510, 490, 500)] * 3, start="2026-03-03 09:00")
    a = atr(pd.concat([d1, d2]), period=2)
    assert a.iloc[3] == 20 and a.iloc[4] == 20


# ---------------- estratégias ----------------
def test_entry_window_filters_signals():
    closes = list(range(130, 100, -1)) + list(range(101, 131)) + list(range(129, 80, -1))
    bars = series_bars(closes)
    free = get_strategy("sma_cross", {"fast": 3, "slow": 10}).generate(bars)
    limited = get_strategy("sma_cross", {"fast": 3, "slow": 10,
                                         "entry_until": "10:00"}).generate(bars)
    assert (free != 0).sum() > (limited != 0).sum()
    assert (limited[limited.index.time > pd.Timestamp("10:00").time()] == 0).all()


def test_orb_fade_inverts_breakout():
    rows = [(100, 110, 90, 100)] * 6 + [(105, 115, 104, 112), (112, 113, 80, 85)]
    bars = make_bars(rows)
    br = get_strategy("opening_range_breakout", {"mode": "breakout"}).signals(bars)
    fd = get_strategy("opening_range_breakout", {"mode": "fade"}).signals(bars)
    assert (fd == -br).all() and br.abs().sum() == 2


def test_donchian_signals_on_new_high_only():
    closes = [100] * 10 + [101, 102, 103] + [102] * 5
    bars = series_bars(closes)
    sig = get_strategy("donchian_breakout", {"n": 5}).signals(bars)
    # rompe a máxima (com +5 de pavio) só quando close > 105: nunca aqui
    assert (sig == 0).all()
    bars2 = series_bars([100] * 10 + [120, 130, 140, 150])
    sig2 = get_strategy("donchian_breakout", {"n": 5}).signals(bars2)
    assert sig2.iloc[10] == 1 and (sig2.iloc[11:] == 0).all()  # só no cruzamento


def test_mean_reversion_entry_and_exit():
    closes = [99.0, 101.0] * 15 + [70.0, 75.0, 90.0, 101.0, 100.0]
    bars = series_bars(closes)
    strat = get_strategy("mean_reversion", {"n": 20, "k": 2.0, "anchor": "sma"})
    sig = strat.signals(bars)
    assert sig.iloc[30] == 1  # queda brusca -> compra
    exit_long, _ = strat.exits(bars)
    assert exit_long.iloc[33]  # voltou acima da média -> sai


@pytest.mark.parametrize("name,params", [
    ("donchian_breakout", {"n": 12, "exit_n": 6}),
    ("mean_reversion", {"n": 20, "k": 2.0, "anchor": "sma"}),
    ("mean_reversion", {"n": 14, "k": 2.0, "anchor": "vwap"}),
    ("opening_range_breakout", {"mode": "fade"}),
])
def test_no_lookahead(name, params):
    bars = synthetic_bars(get_spec("WIN"), 130_000, days=6, seed=5)
    strat = get_strategy(name, params)
    full = strat.generate(bars)
    ex_full = strat.exits(bars)
    for cut in (150, 400):
        part = bars.iloc[:cut]
        pd.testing.assert_series_equal(strat.generate(part), full.iloc[:cut])
        if ex_full is not None:
            ex = strat.exits(part)
            pd.testing.assert_series_equal(ex[0], ex_full[0].iloc[:cut])


# ---------------- motor ----------------
def test_atr_stop_distance(cfg):
    inst = replace(cfg.instruments["WIN"], stop_atr=2.0, target_atr=3.0)
    assert stop_target_points(inst, 100.0) == (200.0, 300.0)
    assert stop_target_points(inst, float("nan")) == (inst.stop_points, inst.target_points)


def test_exit_signal_closes_position(cfg):
    inst = replace(cfg.instruments["WIN"], stop_points=0, target_points=0)
    closes = [99.0, 101.0] * 15 + [70.0, 75.0, 90.0, 101.0, 100.0, 100.0]
    bars = series_bars(closes, start="2026-03-02 10:00")
    res = run_backtest(bars, inst, cfg,
                       get_strategy("mean_reversion", {"n": 20, "k": 2.0}))
    assert res.trades.iloc[0].reason == "saída"
    assert res.trades.iloc[0].side == 1


def test_trade_from_skips_earlier_entries(cfg):
    bars = synthetic_bars(get_spec("WIN"), 130_000, days=10, seed=2)
    full = run_backtest(bars, cfg.instruments["WIN"], cfg)
    cut = pd.Timestamp(bars.index.normalize().unique()[5])
    part = run_backtest(bars, cfg.instruments["WIN"], cfg, trade_from=cut)
    assert (part.trades.entry_time >= cut).all()
    later = full.trades[full.trades.entry_time >= cut].reset_index(drop=True)
    pd.testing.assert_frame_equal(part.trades.reset_index(drop=True), later)


# ---------------- walk-forward ----------------
def test_walk_forward_matches_independent_runs(cfg):
    """A seleção sobre o resultado diário deve dar o mesmo que rodar cada janela."""
    bars = synthetic_bars(get_spec("WIN"), 130_000, days=40, seed=11)
    inst = cfg.instruments["WIN"]
    cand = Candidate("teste", "sma_cross", {
        "fast": [5, 9], "slow": [21, 40], "inst.stop_atr": [1.0, 2.0],
    })
    res = walk_forward(bars, inst, cfg, cand, train_days=20, test_days=10,
                       min_trades=5, workers=1)
    assert len(res.folds) == 2
    days = pd.Index(bars.index.normalize().unique())
    for i, fold in res.folds.iterrows():
        test_days = days[20 + 10 * i: 30 + 10 * i]
        warm_start = days[20 + 10 * i - 3]
        sl = bars[(bars.index >= warm_start) & (bars.index < test_days[-1] + pd.Timedelta(days=1))]
        inst2, sp = split_params(inst, fold["params"])
        alone = run_backtest(sl, inst2, cfg, get_strategy("sma_cross", sp),
                             trade_from=test_days[0])
        assert alone.trades["net"].sum() == pytest.approx(fold["teste_R$"])


def test_walk_forward_never_uses_test_data_to_choose(cfg):
    """O parâmetro escolhido depende só do treino: mudar o teste não muda a escolha."""
    bars = synthetic_bars(get_spec("WIN"), 130_000, days=30, seed=4)
    cand = Candidate("t", "sma_cross", {"fast": [5, 9], "slow": [21, 40]})
    a = walk_forward(bars, cfg.instruments["WIN"], cfg, cand, 20, 10, 1, workers=1)
    noisy = bars.copy()
    last_days = noisy.index >= noisy.index.normalize().unique()[20]
    rng = np.random.default_rng(0)
    shift = get_spec("WIN").round_price(1) * 0 + rng.choice([-50, 50], last_days.sum())
    for c in ("open", "high", "low", "close"):
        noisy.loc[last_days, c] += shift
    b = walk_forward(noisy, cfg.instruments["WIN"], cfg, cand, 20, 10, 1, workers=1)
    assert a.folds["params"].tolist() == b.folds["params"].tolist()
