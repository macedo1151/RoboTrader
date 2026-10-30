import numpy as np
import pandas as pd
import pytest

from robotrader.flow import FLAG_BUY, FLAG_SELL, aggregate, resample_flow, ticks_to_frame


def mt5_ticks(rows):
    """rows = [(ms_desde_09h, preço, qtd, flags)] no formato do copy_ticks_range."""
    base = int(pd.Timestamp("2026-10-28 09:00").value // 1_000_000)
    dtype = [("time", "i8"), ("bid", "f8"), ("ask", "f8"), ("last", "f8"), ("volume", "u8"),
             ("time_msc", "i8"), ("flags", "u4"), ("volume_real", "f8")]
    return np.array([(0, 0, 0, p, int(q), base + ms, f, q) for ms, p, q, f in rows], dtype=dtype)


def test_aggressor_side_and_bar_values():
    LAST = 8 | 16
    ticks = mt5_ticks([
        (0, 100.0, 5, LAST | FLAG_BUY),
        (10_000, 102.0, 3, LAST | FLAG_SELL),
        (20_000, 101.0, 2, LAST),                   # neutro (leilão / direto)
        (65_000, 103.0, 4, LAST | FLAG_BUY),        # minuto seguinte
    ])
    frame = ticks_to_frame(ticks)
    assert frame["side"].tolist() == [1, -1, 0, 1]
    bars = aggregate(frame, 1)
    first = bars.iloc[0]
    assert (first.open, first.high, first.low, first.close) == (100, 102, 100, 101)
    assert (first.buy_vol, first.sell_vol, first.neutral_vol, first.delta) == (5, 3, 2, 2)
    assert first.trades == 3 and first.volume == 10
    assert bars.iloc[1].delta == 4


def test_ignores_zero_price_ticks():
    ticks = mt5_ticks([(0, 0.0, 1, FLAG_BUY), (1, 100.0, 1, FLAG_BUY)])
    assert len(ticks_to_frame(ticks)) == 1


def test_resample_flow_sums_volumes():
    idx = pd.date_range("2026-10-28 09:00", periods=10, freq="1min", name="datetime")
    bars = pd.DataFrame({
        "open": range(10), "high": range(1, 11), "low": range(10), "close": range(10),
        "volume": [10.0] * 10, "buy_vol": [6.0] * 10, "sell_vol": [4.0] * 10,
        "neutral_vol": [0.0] * 10, "trades": [3.0] * 10, "delta": [2.0] * 10,
    }, index=idx)
    m5 = resample_flow(bars, 5)
    assert len(m5) == 2
    assert m5.iloc[0].delta == 10 and m5.iloc[0].open == 0 and m5.iloc[0].close == 4
    assert m5.iloc[0].high == 5


def test_server_time_keeps_wall_clock():
    """O horário pedido ao MT5 deve chegar como o mesmo relógio do servidor (sem +3h)."""
    from datetime import datetime

    from robotrader.broker.mt5 import from_server_ts, server_time

    dt = datetime(2026, 10, 28, 9, 0)
    ts = server_time(dt).timestamp()
    assert from_server_ts(ts) == dt
    assert ts % 86400 == 9 * 3600  # 09:00 "UTC" = convenção do MT5
