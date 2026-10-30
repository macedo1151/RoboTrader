from datetime import datetime, time

from robotrader.config import RiskConfig, SessionConfig
from robotrader.risk import RiskManager


def rm(**kw):
    return RiskManager(
        RiskConfig(**{"contracts": 1, "max_daily_loss": 100, "max_trades_per_day": 5,
                      "max_consecutive_losses": 3, **kw}),
        SessionConfig(time(9, 5), time(17, 0), time(17, 50)),
    )


def at(h, m, day=2):
    return datetime(2026, 3, day, h, m)


def test_session_window():
    r = rm()
    assert not r.can_enter(at(9, 0))[0]
    assert r.can_enter(at(9, 5))[0]
    assert not r.can_enter(at(17, 5))[0]
    assert r.must_flatten(at(17, 50)) and not r.must_flatten(at(17, 45))


def test_daily_loss_halts_and_resets_next_day():
    r = rm()
    r.register_trade(at(10, 0), -60)
    assert r.can_enter(at(10, 5))[0]
    r.register_trade(at(10, 30), -45)
    ok, reason = r.can_enter(at(11, 0))
    assert not ok and "perda diária" in reason
    assert r.can_enter(at(10, 0, day=3))[0]


def test_consecutive_losses():
    r = rm(max_daily_loss=10_000)
    for m in (0, 10, 20):
        r.register_trade(at(10, m), -1)
    assert not r.can_enter(at(11, 0))[0]


def test_win_resets_loss_streak():
    r = rm(max_daily_loss=10_000)
    r.register_trade(at(10, 0), -1)
    r.register_trade(at(10, 10), -1)
    r.register_trade(at(10, 20), +5)
    r.register_trade(at(10, 30), -1)
    assert r.can_enter(at(11, 0))[0]


def test_max_trades():
    r = rm(max_daily_loss=10_000, max_consecutive_losses=99)
    for m in range(5):
        r.register_trade(at(10, m), 1)
    assert not r.can_enter(at(11, 0))[0]
