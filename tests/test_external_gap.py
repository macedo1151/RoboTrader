from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from robotrader.backtest import run_backtest
from robotrader.strategies import ExternalGapFade, overnight_gaps
from conftest import make_bars


def day_bars(day: str, closes, start="09:00"):
    return make_bars([(c, c + 1, c - 1, c) for c in closes], start=f"{day} {start}")


def test_overnight_gaps_and_late_open_skipped():
    ext = pd.concat([
        day_bars("2026-03-02", [100, 100, 100]),
        day_bars("2026-03-03", [110, 111, 112]),           # +10% vs 100
        day_bars("2026-03-04", [99, 99], start="09:30"),   # 1ª barra não é 9:00 -> ignora
    ])
    g = overnight_gaps(ext)
    assert list(g.index) == [date(2026, 3, 3)]
    assert g.iloc[0] == pytest.approx(0.10)


def make_gaps(values, start="2026-01-01"):
    idx = pd.Index(pd.bdate_range(start, periods=len(values)).date)
    return pd.Series(values, index=idx)


def test_signal_only_on_first_bar_and_fades():
    gaps = make_gaps([0.01, -0.02])
    bars = pd.concat([day_bars(str(d), [100] * 4) for d in gaps.index])
    sig = ExternalGapFade(gaps=gaps, threshold_q=0.0).signals(bars)
    assert sig.tolist() == [-1, 0, 0, 0, 1, 0, 0, 0]  # S&P subiu -> vende; caiu -> compra
    follow = ExternalGapFade(gaps=gaps, threshold_q=0.0, direction=1).signals(bars)
    assert (follow == -sig).all()


def test_threshold_uses_only_past_days():
    rng = np.random.default_rng(1)
    gaps = make_gaps(rng.normal(0, 0.01, 120))
    strat = ExternalGapFade(gaps=gaps, threshold_q=0.5, lookback=40)
    full = strat._day_signal()
    # mudar um gap futuro não pode mudar sinais anteriores
    altered = gaps.copy()
    altered.iloc[100] = 0.5
    part = ExternalGapFade(gaps=altered, threshold_q=0.5, lookback=40)._day_signal()
    pd.testing.assert_series_equal(full.iloc[:100], part.iloc[:100])
    assert (full.iloc[:20] == 0).all()  # sem histórico suficiente, não opera


def test_requires_gaps():
    with pytest.raises(ValueError):
        ExternalGapFade()


def test_engine_enters_0905_and_exits_at_time(cfg):
    gaps = make_gaps([0.02], start="2026-03-02")
    closes = [100_000 + 50 * i for i in range(30)]  # 9:00 .. 11:25
    bars = day_bars("2026-03-02", closes)
    inst = replace(cfg.instruments["WIN"], stop_points=0, target_points=0)
    res = run_backtest(bars, inst, cfg, ExternalGapFade(gaps=gaps, exit_at="10:00",
                                                        threshold_q=0.0))
    t = res.trades.iloc[0]
    assert t.side == -1
    assert t.entry_time == pd.Timestamp("2026-03-02 09:05")
    assert t.exit_time == pd.Timestamp("2026-03-02 10:05")  # sinal de saída às 10:00, executa na barra seguinte
    assert t.reason == "saída"
