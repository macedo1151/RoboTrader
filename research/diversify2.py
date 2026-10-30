"""Segunda leva de hipóteses complementares à external_gap (definidas ANTES dos resultados).

H6  Véspera de feriado (Ariel 1990): compra WIN 9:05 -> 17:45 no pregão anterior a um
    feriado (dia útil sem pregão, detectado nos dados).
H7  Reversão diária: se o movimento de ontem (9:00 -> 17:45) do WIN foi maior que a
    mediana dos 120 pregões anteriores, opera contra ele hoje, 9:05 -> 17:45.
H8  Drift noturno (Lou, Polk & Skouras 2019): compra WIN no fechamento e vende na
    abertura seguinte (série WIN$D, ajustada nas rolagens). POSIÇÃO DE UM DIA PARA O OUTRO.
H10 Tendência de 60 pregões (Moskowitz, Ooi & Pedersen 2012): fica comprado se o retorno
    dos últimos 60 pregões é positivo, vendido se negativo; revisa no fechamento.
    H10a: WIN$D;  H10b: DOL$D (dólar, operado via WDO). POSIÇÃO POR VÁRIOS DIAS.
    Custo: meia ida-e-volta por contrato a cada mudança de lado + rolagens
    (WIN a cada 2 meses, WDO todo mês).
H11 Dia de vencimento do WIN (exploratória): 9:05 -> 17:45, direção escolhida na descoberta.

Mesmo critério da primeira leva: descoberta até 02/2025, confirmação depois; APROVA se
líquido > 0 nas duas metades, t >= 2 no total, t >= 1 na confirmação e |correlação diária|
com a external_gap < 0,3.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))
sys.stdout.reconfigure(encoding="utf-8")

from diversify import COST, VALUE, at, close_at, evaluate  # noqa: E402
from robotrader.contracts import win_expiry  # noqa: E402
from robotrader.data import load_csv  # noqa: E402


def load_daily(name: str) -> pd.DataFrame:
    df = pd.read_csv(ROOT / "data" / "daily" / f"{name}_D1.csv", parse_dates=["date"],
                     index_col="date")
    df.index = pd.Index(df.index.date)
    return df


def position_pnl(pos: pd.Series, close: pd.Series, root: str, roll_days: set) -> pd.Series:
    """P&L diário em PONTOS (já com custos) de uma posição mantida de fechamento a fechamento.
    `evaluate` desconta COST por dia operado; aqui devolvemos o bruto + ajuste para que
    o custo efetivo seja só o das mudanças de lado e rolagens."""
    pos = pos.reindex(close.index).fillna(0)
    held = pos.shift(1).fillna(0)
    gross = held * close.diff()
    side_cost = (pos - held).abs() * COST[root] / 2           # meia ida-e-volta por contrato movido
    roll_cost = pd.Series([COST[root] if d in roll_days else 0.0 for d in close.index],
                          index=close.index) * held.abs()
    net_pts = gross - side_cost - roll_cost
    active = (held != 0) | (pos != held)
    # evaluate() subtrai COST[root]; somamos de volta para não cobrar duas vezes
    return (net_pts + COST[root])[active]


def main() -> None:
    win = load_csv(ROOT / "data" / "WIN_M5.csv")
    gt = pd.read_csv(ROOT / "reports" / "gap_research" / "base_trades.csv")
    gap_daily = gt.groupby(pd.to_datetime(gt.exit_time).dt.date)["net"].sum()
    rows = []

    day_trade = close_at(win, "17:45") - at(win, "09:05", "open")
    days = pd.Index(sorted(set(win.index.date)))

    # H6 véspera de feriado
    bdays = pd.bdate_range(days[0], days[-1]).date
    holidays = sorted(set(bdays) - set(days))
    nxt = pd.Series(days[1:].tolist() + [None], index=days)
    pre_hol = [d for d in days if nxt[d] is not None and
               any(d < h < nxt[d] for h in holidays)]
    rows.append(evaluate(f"H6 véspera de feriado ({len(holidays)} feriados): compra 9:05-17:45",
                         "WIN", day_trade[day_trade.index.isin(pre_hol)], gap_daily))

    # H7 reversão diária
    move = close_at(win, "17:45") - at(win, "09:00", "open")
    big = move.abs() > move.abs().shift(1).rolling(120, min_periods=60).median()
    sig = -np.sign(move).where(big, 0).shift(1)
    pnl = (sig * day_trade).dropna()
    rows.append(evaluate("H7 reversão do dia anterior (movimento > mediana) 9:05-17:45", "WIN",
                         pnl[sig.reindex(pnl.index) != 0], gap_daily))

    # rolagens do WIN (quarta mais próxima do dia 15 dos meses pares)
    win_rolls = {win_expiry(y, m) for y in range(2021, 2028) for m in (2, 4, 6, 8, 10, 12)}

    # H8 drift noturno (WIN$D ajustado)
    wd = load_daily("WIN_D")
    overnight = (wd["open"] - wd["close"].shift(1)).dropna()
    rows.append(evaluate("H8 drift noturno: compra no fechamento, vende na abertura (WIN$D)",
                         "WIN", overnight, gap_daily))

    # H10 tendência de 60 pregões
    dd = load_daily("DOL_D")
    dol_rolls = {d for d in dd.index if d.day <= 3 and
                 (dd.index[dd.index.get_loc(d) - 1].month != d.month if dd.index.get_loc(d) else False)}
    for label, df, root, rolls in [("H10a tendência 60 pregões WIN$D", wd, "WIN", win_rolls),
                                   ("H10b tendência 60 pregões dólar (DOL$D, via WDO)", dd, "WDO", dol_rolls)]:
        pos = np.sign(df["close"] - df["close"].shift(60))
        rows.append(evaluate(label + " (posição por dias)", root,
                             position_pnl(pos, df["close"], root, rolls), gap_daily))

    # H11 vencimento do WIN (exploratória)
    exp_days = [d for d in days if d in win_rolls]
    raw = day_trade[day_trade.index.isin(exp_days)]
    split = pd.Timestamp("2025-03-01").date()
    direction = 1 if raw[raw.index < split].mean() > 0 else -1
    rows.append(evaluate(f"H11 dia de vencimento WIN ({'compra' if direction > 0 else 'venda'}, "
                         "direção da descoberta) 9:05-17:45", "WIN", direction * raw, gap_daily))

    out = pd.DataFrame(rows)
    pd.set_option("display.width", 260)
    pd.set_option("display.max_colwidth", 75)
    print(out.to_string(index=False))
    out.to_csv(ROOT / "reports" / "diversify_screen2.csv", index=False)


if __name__ == "__main__":
    main()
