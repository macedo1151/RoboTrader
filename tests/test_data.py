import pandas as pd
import pytest

from robotrader.contracts import get_spec
from robotrader.data import load_csv, resample, synthetic_bars


def test_synthetic_is_on_tick_grid():
    bars = synthetic_bars(get_spec("WDO"), 5500, days=3)
    for col in ("open", "high", "low", "close"):
        assert ((bars[col] / 0.5) % 1 == 0).all()
    assert bars.index.normalize().nunique() == 3


def test_load_standard_csv(tmp_path):
    p = tmp_path / "bars.csv"
    p.write_text("datetime,open,high,low,close,volume\n"
                 "2026-03-02 09:00,100,110,95,105,10\n"
                 "2026-03-02 09:05,105,108,100,101,12\n")
    df = load_csv(p)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2


def test_load_mt5_export(tmp_path):
    p = tmp_path / "mt5.csv"
    p.write_text(
        "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
        "2026.03.02\t09:00:00\t130000\t130100\t129900\t130050\t500\t1200\t0\n"
        "2026.03.02\t09:05:00\t130050\t130200\t130000\t130150\t400\t900\t0\n"
    )
    df = load_csv(p)
    assert df.index[0] == pd.Timestamp("2026-03-02 09:00")
    assert df["volume"].tolist() == [1200, 900]


def test_rejects_bad_bars(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("datetime,open,high,low,close,volume\n2026-03-02 09:00,100,99,95,105,1\n")
    with pytest.raises(ValueError):
        load_csv(p)


def test_resample_m5_to_m15():
    bars = synthetic_bars(get_spec("WIN"), 130_000, days=1, minutes=5)
    m15 = resample(bars, "M15")
    assert len(m15) == len(bars) // 3
    assert m15["high"].iloc[0] == bars["high"].iloc[:3].max()
