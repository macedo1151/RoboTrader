import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from robotrader.config import load_config  # noqa: E402


@pytest.fixture
def cfg():
    """Config com estratégias de exemplo fixas: os testes não dependem da estratégia
    escolhida para produção em settings.toml nem de arquivos em data/."""
    from dataclasses import replace

    c = load_config()
    win = replace(c.instruments["WIN"], strategy_name="sma_cross",
                  strategy_params={"fast": 9, "slow": 21},
                  stop_points=250, target_points=500, stop_atr=0.0, target_atr=0.0)
    wdo = replace(c.instruments["WDO"], enabled=True,
                  strategy_name="opening_range_breakout", strategy_params={"range_minutes": 30})
    return replace(c, instruments={"WIN": win, "WDO": wdo}, portfolios=[])


def make_bars(rows, start="2026-03-02 09:00", minutes=5) -> pd.DataFrame:
    """rows = [(open, high, low, close), ...]"""
    idx = pd.date_range(start, periods=len(rows), freq=f"{minutes}min", name="datetime")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 100.0
    return df.astype(float)
