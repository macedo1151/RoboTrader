"""Métricas de desempenho a partir da lista de operações."""

from __future__ import annotations

import numpy as np
import pandas as pd


def trades_to_frame(trades: list) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame(
            columns=["symbol", "side", "qty", "entry_time", "entry_price", "exit_time",
                     "exit_price", "points", "gross", "costs", "net", "reason"]
        )
    return pd.DataFrame([t.__dict__ for t in trades])


def equity_curve(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    return trades.set_index("exit_time")["net"].cumsum()


def summarize(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"operacoes": 0}

    net = trades["net"]
    wins = net[net > 0]
    losses = net[net <= 0]
    equity = net.cumsum()
    drawdown = equity - equity.cummax().clip(lower=0)
    daily = trades.groupby(pd.to_datetime(trades["exit_time"]).dt.date)["net"].sum()

    gross_win = wins.sum()
    gross_loss = -losses.sum()
    return {
        "operacoes": int(len(trades)),
        "resultado_liquido": round(float(net.sum()), 2),
        "custos_totais": round(float(trades["costs"].sum()), 2),
        "taxa_acerto_%": round(100 * len(wins) / len(trades), 1),
        "fator_lucro": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "payoff": (
            round(wins.mean() / -losses.mean(), 2)
            if len(wins) and len(losses) and losses.mean() < 0 else float("nan")
        ),
        "media_por_operacao": round(float(net.mean()), 2),
        "drawdown_maximo": round(float(drawdown.min()), 2),
        "dias_operados": int(len(daily)),
        "dias_positivos_%": round(100 * float((daily > 0).mean()), 1),
        "pior_dia": round(float(daily.min()), 2),
        "melhor_dia": round(float(daily.max()), 2),
        "sharpe_diario_anualizado": (
            round(float(daily.mean() / daily.std() * np.sqrt(252)), 2)
            if len(daily) > 1 and daily.std() > 0 else float("nan")
        ),
    }
