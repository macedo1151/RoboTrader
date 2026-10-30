from datetime import datetime

import pytest

from robotrader.contracts import get_spec
from robotrader.execution import ExecutionModel

TS = datetime(2026, 3, 2, 10, 0)


@pytest.fixture
def win():
    return ExecutionModel(get_spec("WIN"), stop_points=100, target_points=200,
                          slippage_ticks=1, cost_per_contract=0.5)


def test_open_applies_slippage_and_levels(win):
    pos = win.open(+1, 1, 130_000, TS)
    assert pos.entry_price == 130_005
    assert pos.stop == 129_905
    assert pos.target == 130_205
    short = win.open(-1, 1, 130_000, TS)
    assert (short.entry_price, short.stop, short.target) == (129_995, 130_095, 129_795)


def test_stop_wins_when_both_hit(win):
    pos = win.open(+1, 1, 130_000, TS)
    price, reason = win.check_bar(pos, 130_000, 130_300, 129_800)
    assert reason == "stop"
    assert price == 129_900  # stop menos 1 tick de slippage


def test_target_hit(win):
    pos = win.open(+1, 1, 130_000, TS)
    assert win.check_bar(pos, 130_050, 130_210, 130_000) == (130_205, "alvo")


def test_gap_through_stop_fills_at_open(win):
    pos = win.open(+1, 1, 130_000, TS)
    price, reason = win.check_bar(pos, 129_700, 129_750, 129_600)
    assert reason == "stop (gap)"
    assert price == 129_695


def test_no_exit_inside_range(win):
    pos = win.open(-1, 1, 130_000, TS)
    assert win.check_bar(pos, 130_000, 130_050, 129_900) is None


def test_trade_pnl_and_costs(win):
    pos = win.open(+1, 2, 130_000, TS)  # entra 130_005
    trade = win.close("WIN", pos, 130_105, TS, "teste")
    assert trade.points == 100
    assert trade.gross == pytest.approx(100 * 0.20 * 2)  # R$ 40
    assert trade.costs == pytest.approx(2 * 0.5 * 2)      # R$ 2
    assert trade.net == pytest.approx(38)


def test_wdo_pnl():
    wdo = ExecutionModel(get_spec("WDO"), 5, 10, slippage_ticks=0, cost_per_contract=0)
    pos = wdo.open(-1, 1, 5500.0, TS)
    trade = wdo.close("WDO", pos, 5490.0, TS, "teste")
    assert trade.net == pytest.approx(100.0)  # 10 pontos x R$ 10
