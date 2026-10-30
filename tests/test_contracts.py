from datetime import date

import pytest

from robotrader.contracts import active_contract, get_spec, wdo_expiry, win_expiry


def test_tick_values():
    assert get_spec("WIN").tick_value == pytest.approx(1.0)
    assert get_spec("WDO").tick_value == pytest.approx(5.0)


def test_round_price():
    win = get_spec("WIN")
    assert win.round_price(130_002) == 130_000
    assert win.round_price(130_003) == 130_005
    assert get_spec("WDO").round_price(5501.3) == 5501.5


@pytest.mark.parametrize("y,m,expected", [
    (2026, 2, date(2026, 2, 18)),   # dia 15 é domingo -> quarta 18
    (2026, 10, date(2026, 10, 14)),  # dia 15 é quinta -> quarta 14
    (2026, 12, date(2026, 12, 16)),  # dia 15 é terça -> quarta 16
])
def test_win_expiry(y, m, expected):
    assert win_expiry(y, m) == expected
    assert expected.weekday() == 2


def test_wdo_expiry_skips_weekend():
    assert wdo_expiry(2026, 11, ) == date(2026, 11, 2)  # 1/11/2026 é domingo


@pytest.mark.parametrize("today,root,expected", [
    (date(2026, 10, 29), "WIN", "WINZ26"),
    (date(2026, 10, 5), "WIN", "WINV26"),
    (date(2026, 10, 13), "WIN", "WINV26"),  # véspera do vencimento: ainda o antigo
    (date(2026, 10, 14), "WIN", "WINZ26"),  # dia do vencimento: liquidez já no novo
    (date(2026, 12, 16), "WIN", "WING27"),
    (date(2026, 10, 29), "WDO", "WDOX26"),
    (date(2026, 10, 31), "WDO", "WDOZ26"),
    (date(2026, 12, 20), "WDO", "WDOF27"),
])
def test_active_contract(today, root, expected):
    assert active_contract(root, today) == expected
