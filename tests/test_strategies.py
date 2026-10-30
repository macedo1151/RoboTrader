import numpy as np
import pandas as pd

from robotrader.strategies import get_strategy
from conftest import make_bars


def series_bars(closes, **kw):
    return make_bars([(c, c + 5, c - 5, c) for c in closes], **kw)


def test_sma_cross_signals_on_cross_only():
    closes = list(range(130, 100, -1)) + list(range(101, 131)) + list(range(129, 80, -1))
    bars = series_bars(closes)
    sig = get_strategy("sma_cross", {"fast": 3, "slow": 10}).signals(bars)
    assert set(sig.unique()) <= {-1, 0, 1}
    assert (sig == 1).sum() == 1
    assert (sig == -1).sum() == 1
    assert sig[sig == 1].index[0] < sig[sig == -1].index[0]


def test_sma_no_lookahead():
    """Sinais de um prefixo não mudam quando chegam barras novas."""
    rng = np.random.default_rng(1)
    bars = series_bars(list(130_000 + np.cumsum(rng.normal(0, 50, 300)).round()))
    strat = get_strategy("sma_cross", {"fast": 5, "slow": 20})
    full = strat.signals(bars)
    for cut in (50, 120, 250):
        pd.testing.assert_series_equal(strat.signals(bars.iloc[:cut]), full.iloc[:cut])


def test_orb_breakout_up_once_per_day():
    # 6 barras de 5 min = faixa de 30 min entre 9:00 e 9:30
    rows = [(100, 110, 90, 100)] * 6 + [
        (100, 108, 99, 105),   # dentro da faixa
        (105, 115, 104, 112),  # fecha acima de 110 -> compra
        (112, 120, 111, 118),  # já rompeu: sem novo sinal
        (118, 118, 85, 88),    # fecha abaixo de 90 -> venda
        (88, 89, 80, 82),
    ]
    bars = make_bars(rows)
    sig = get_strategy("opening_range_breakout", {"range_minutes": 30}).signals(bars)
    assert sig.tolist() == [0] * 7 + [1, 0, -1, 0]


def test_orb_resets_each_day():
    day = [(100, 110, 90, 100)] * 6 + [(100, 115, 99, 112)]
    d1 = make_bars(day, start="2026-03-02 09:00")
    d2 = make_bars(day, start="2026-03-03 09:00")
    sig = get_strategy("opening_range_breakout").signals(pd.concat([d1, d2]))
    assert (sig == 1).sum() == 2
