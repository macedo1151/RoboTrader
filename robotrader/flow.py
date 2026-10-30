"""Fluxo de ordens: agrega negócios (ticks) em barras com volume agredido.

Cada negócio na B3 vem marcado pelo lado agressor:
- TICK_FLAG_BUY  (32): o comprador "pagou" a oferta  -> agressão de compra
- TICK_FLAG_SELL (64): o vendedor "bateu" na compra -> agressão de venda
Negócios sem nenhuma das duas marcas (leilão, negócios diretos) entram como neutros.

Barra de fluxo: open/high/low/close/volume + buy_vol, sell_vol, neutral_vol,
trades, delta (= buy_vol - sell_vol).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

FLAG_BUY = 32
FLAG_SELL = 64

FLOW_COLUMNS = ["open", "high", "low", "close", "volume",
                "buy_vol", "sell_vol", "neutral_vol", "trades", "delta"]


def ticks_to_frame(ticks: np.ndarray | pd.DataFrame) -> pd.DataFrame:
    """Converte o retorno de copy_ticks_range em DataFrame com lado agressor."""
    df = pd.DataFrame(ticks)
    if df.empty:
        return pd.DataFrame(columns=["price", "qty", "side"])
    flags = df["flags"].to_numpy()
    buy = (flags & FLAG_BUY) > 0
    sell = (flags & FLAG_SELL) > 0
    side = np.where(buy & ~sell, 1, np.where(sell & ~buy, -1, 0))
    out = pd.DataFrame({
        "price": df["last"].to_numpy(dtype=float),
        "qty": df["volume_real"].to_numpy(dtype=float),
        "side": side,
    }, index=pd.to_datetime(df["time_msc"].to_numpy(), unit="ms"))
    out.index.name = "datetime"
    return out[out["price"] > 0]


def aggregate(ticks: pd.DataFrame, minutes: int = 1) -> pd.DataFrame:
    """Agrega negócios em barras de `minutes` minutos."""
    if ticks.empty:
        return pd.DataFrame(columns=FLOW_COLUMNS)
    key = ticks.index.floor(f"{minutes}min")
    qty, side = ticks["qty"], ticks["side"]
    g = ticks.groupby(key)
    bars = pd.DataFrame({
        "open": g["price"].first(),
        "high": g["price"].max(),
        "low": g["price"].min(),
        "close": g["price"].last(),
        "volume": g["qty"].sum(),
        "buy_vol": qty.where(side == 1, 0.0).groupby(key).sum(),
        "sell_vol": qty.where(side == -1, 0.0).groupby(key).sum(),
        "neutral_vol": qty.where(side == 0, 0.0).groupby(key).sum(),
        "trades": g["qty"].size(),
    })
    bars["delta"] = bars["buy_vol"] - bars["sell_vol"]
    bars.index.name = "datetime"
    return bars[FLOW_COLUMNS]


def resample_flow(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Agrega barras de fluxo para um tempo gráfico maior, sem misturar dias."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last",
           "volume": "sum", "buy_vol": "sum", "sell_vol": "sum",
           "neutral_vol": "sum", "trades": "sum", "delta": "sum"}
    key = bars.index.floor(f"{minutes}min")
    out = bars.groupby(key).agg(agg)
    out.index.name = "datetime"
    return out


def load_flow(path: str | Path, minutes: int | None = None) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["datetime"], index_col="datetime").sort_index()
    df = df[~df.index.duplicated(keep="last")].astype(float)
    return resample_flow(df, minutes) if minutes and minutes > 1 else df
