"""Busca de estratégias complementares à do S&P noturno (external_gap).

Hipóteses definidas ANTES de olhar os resultados, cada uma com uma razão de existir:

H1  Momentum intradiário (Gao, Han, Li & Zhou, 2018, "Market intraday momentum"):
    o retorno do começo do dia prevê o da última meia hora, na mesma direção.
    H1a: fechamento anterior -> 9:30 prevê 17:00 -> 17:45     (WIN e WDO)
    H1b: abertura 9:00 -> 17:00 prevê 17:00 -> 17:45           (WIN e WDO)
H3  Gap noturno do S&P no WDO (aversão a risco à noite -> real fraco na abertura).
    Sinal: gap do WSP; opera 9:05 -> 13:00. Sem direção definida a priori: o sinal
    escolhido na metade de descoberta é o testado na confirmação.
H4  O S&P na sessão americana (WSP 9:00 -> 13:00, inclui a abertura de Nova York)
    prevê a tarde: WIN segue o S&P; WDO vai na direção oposta.  Opera 13:05 -> 17:45.
H5  Virada do mês (fluxo de aplicações/salários; efeito "turn of the month"):
    compra WIN 9:05 -> 17:45 no último pregão do mês e nos 3 primeiros.

Avaliação (por operação, já descontado o custo: WIN 15 pts, WDO 1,3 pt):
- metade de DESCOBERTA: 03/2023 a 02/2025; metade de CONFIRMAÇÃO: 03/2025 a 10/2026;
- APROVA se: líquido > 0 nas duas metades, t >= 2 no total, t >= 1 na confirmação e
  correlação diária com a estratégia external_gap < 0,3.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from robotrader.data import load_csv  # noqa: E402
from robotrader.strategies import overnight_gaps  # noqa: E402

SPLIT = pd.Timestamp("2025-03-01").date()
COST = {"WIN": 15.0, "WDO": 1.3}
VALUE = {"WIN": 0.20, "WDO": 10.0}  # R$ por ponto
EXCLUDE = {pd.Timestamp(x).date() for x in ["2026-10-05", "2026-10-26"]}


def at(df: pd.DataFrame, hhmm: str, field: str) -> pd.Series:
    """Preço `field` da barra que COMEÇA em hhmm, por dia."""
    t = pd.Timestamp(hhmm).time()
    s = df[df.index.time == t][field]
    s.index = pd.Index(s.index.date)
    return s


def close_at(df: pd.DataFrame, hhmm: str) -> pd.Series:
    """Preço de fechamento no instante hhmm (= fechamento da barra que termina em hhmm)."""
    return at(df, (pd.Timestamp(hhmm) - pd.Timedelta(minutes=5)).strftime("%H:%M"), "close")


def tstat(x: pd.Series) -> float:
    x = x.dropna()
    return float(x.mean() / x.std() * math.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else float("nan")


def evaluate(name: str, root: str, pnl_pts: pd.Series, gap_daily: pd.Series) -> dict:
    """pnl_pts: resultado bruto em pontos por dia operado (sem custo)."""
    pnl = (pnl_pts.dropna() - COST[root]) * VALUE[root]  # R$ por contrato
    pnl = pnl[~pnl.index.isin(EXCLUDE)]
    disc, hold = pnl[pnl.index < SPLIT], pnl[pnl.index >= SPLIT]
    joined = pd.concat([pnl.rename("a"), gap_daily.rename("b")], axis=1).fillna(0)
    corr = joined["a"].corr(joined["b"])
    row = {
        "hipotese": name, "ativo": root, "ops": len(pnl),
        "R$/op": round(pnl.mean(), 1), "total_R$": round(pnl.sum()),
        "t": round(tstat(pnl), 2),
        "descoberta_R$": round(disc.sum()), "t_desc": round(tstat(disc), 2),
        "confirmacao_R$": round(hold.sum()), "t_conf": round(tstat(hold), 2),
        "corr_gap": round(corr, 2),
    }
    row["APROVA"] = bool(row["descoberta_R$"] > 0 and row["confirmacao_R$"] > 0 and
                         row["t"] >= 2 and row["t_conf"] >= 1 and abs(corr) < 0.3)
    return row


def main() -> None:
    win = load_csv(ROOT / "data" / "WIN_M5.csv")
    wdo = load_csv(ROOT / "data" / "WDO_M5.csv")
    wsp = load_csv(ROOT / "data" / "external" / "WSP_N_M5.csv")
    mk = {"WIN": win, "WDO": wdo}

    gt = pd.read_csv(ROOT / "reports" / "gap_research" / "base_trades.csv")
    gap_daily = gt.groupby(pd.to_datetime(gt.exit_time).dt.date)["net"].sum()

    rows = []
    for root, df in mk.items():
        prev_close = df.groupby(df.index.date)["close"].last().shift()
        prev_close.index = pd.Index(prev_close.index)
        entry, exit_ = at(df, "17:00", "open"), close_at(df, "17:45")
        trade = exit_ - entry
        # H1a: fechamento anterior -> 9:30
        sig = np.sign(close_at(df, "09:30") - prev_close)
        rows.append(evaluate("H1a momentum (fech.ant->9:30) -> 17:00-17:45", root,
                             (sig * trade).dropna()[sig != 0], gap_daily))
        # H1b: 9:00 -> 17:00
        sig = np.sign(close_at(df, "17:00") - at(df, "09:00", "open"))
        rows.append(evaluate("H1b momentum (9:00->17:00) -> 17:00-17:45", root,
                             (sig * trade).dropna()[sig != 0], gap_daily))

    # H3: gap do S&P no WDO; direção escolhida na descoberta
    g = overnight_gaps(wsp)
    strong = g.abs() > g.abs().shift(1).rolling(120, min_periods=60).median()
    trade = close_at(wdo, "13:00") - at(wdo, "09:05", "open")
    raw = (np.sign(g[strong]) * trade).dropna()
    direction = 1 if raw[raw.index < SPLIT].mean() > 0 else -1
    rows.append(evaluate(f"H3 gap do S&P no WDO (direção {'segue' if direction > 0 else 'contra'}, "
                         "escolhida na descoberta) 9:05-13:00", "WDO", direction * raw, gap_daily))

    # H4: S&P 9:00->13:00 prevê a tarde
    wsp_am = np.sign(close_at(wsp, "13:00") - at(wsp, "09:00", "open"))
    for root, sign in [("WIN", 1), ("WDO", -1)]:
        df = mk[root]
        trade = close_at(df, "17:45") - at(df, "13:05", "open")
        rows.append(evaluate(f"H4 S&P manhã -> tarde ({'segue' if sign > 0 else 'oposto'}) 13:05-17:45",
                             root, (sign * wsp_am * trade).dropna()[wsp_am != 0], gap_daily))

    # H5: virada do mês, compra WIN o dia todo
    days = pd.Index(sorted(set(win.index.date)))
    month = pd.Series([d.strftime("%Y-%m") for d in days], index=days)
    pos_in_month = month.groupby(month).cumcount()
    from_end = month.groupby(month).cumcount(ascending=False)
    tom = (pos_in_month < 3) | (from_end == 0)
    trade = close_at(win, "17:45") - at(win, "09:05", "open")
    rows.append(evaluate("H5 virada do mês: compra WIN 9:05-17:45", "WIN",
                         trade[trade.index.isin(days[tom.to_numpy()])], gap_daily))
    # referência: comprar WIN todo dia (só para comparar com H5)
    ref = evaluate("(referência) compra WIN todo dia 9:05-17:45", "WIN", trade, gap_daily)
    ref["APROVA"] = "-"
    rows.append(ref)

    out = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 70)
    print(out.to_string(index=False))
    out.to_csv(ROOT / "reports" / "diversify_screen.csv", index=False)


if __name__ == "__main__":
    main()
