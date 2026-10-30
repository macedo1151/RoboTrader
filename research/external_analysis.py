"""Mercados relacionados (S&P na B3, juros DI, dólar cheio) antecipam WIN e WDO?

Teste 1 — abertura: o WSP (micro S&P na B3) abre às 9:00 junto com o WIN, então
  gap do WSP = movimento do S&P durante a noite. Hipótese: quando o WIN abre "atrasado"
  em relação ao S&P, ele corrige depois.  Sinal = beta x gap_WSP - gap_WIN.
Teste 2 — intradiário: retorno do mercado externo na barra t prevê o retorno do
  WIN/WDO entre a abertura da barra t+1 e o fechamento de t+h?

Entrada sempre na abertura da barra seguinte; custos: WIN ~15 pts, WDO ~1,3 pt.
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

EXCLUDE = {pd.Timestamp(x).date() for x in ["2026-10-05", "2026-10-26"]}
COST = {"WIN": 15.0, "WDO": 1.3}


def load(name: str) -> pd.DataFrame:
    p = ROOT / "data" / ("external" if "_" in name or name.endswith("11") else "") / f"{name}_M5.csv"
    df = load_csv(p)
    return df[~pd.Index(df.index.date).isin(EXCLUDE)]


def tstat(x: pd.Series) -> float:
    x = x.dropna()
    return float(x.mean() / x.std() * math.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else float("nan")


def daily_ic(sig: pd.Series, fwd: pd.Series) -> pd.Series:
    df = pd.DataFrame({"s": sig, "f": fwd}).dropna()
    return df.groupby(df.index.date).apply(
        lambda g: g["s"].rank().corr(g["f"].rank()) if len(g) > 10 else np.nan).dropna()


def opening_test(win: pd.DataFrame, wsp: pd.DataFrame) -> None:
    print("\n================ TESTE 1: S&P durante a noite x abertura do WIN ================")

    def daily(df):
        g = df.groupby(df.index.date)
        return pd.DataFrame({"open": g["open"].first(), "close": g["close"].last(),
                             "first_close": g["close"].first()})

    w, s = daily(win), daily(wsp)
    days = w.index.intersection(s.index)
    w, s = w.loc[days], s.loc[days]
    # gaps em %: preço da 1ª barra (fechamento das 9:00) vs fechamento do dia anterior
    gap_w = w["first_close"] / w["close"].shift() - 1
    gap_s = s["first_close"] / s["close"].shift() - 1
    ok = gap_w.notna() & gap_s.notna() & (gap_w.abs() < 0.05)
    beta = np.polyfit(gap_s[ok], gap_w[ok], 1)[0]
    print(f"{ok.sum()} pregões | correlação gap WIN x gap S&P: {gap_w[ok].corr(gap_s[ok]):.2f} "
          f"| beta {beta:.2f}")

    # entrada às 9:05 (abertura da 2ª barra), saída em vários horários
    entry = win.groupby(win.index.date)["open"].nth(1)
    entry.index = pd.Index(win.index[win.groupby(win.index.date).cumcount() == 1].date)
    entry = entry.reindex(days)
    for exit_t in ["10:00", "11:00", "13:00", "17:45"]:
        sel = win[win.index.time == pd.Timestamp(exit_t).time()]["close"]
        sel.index = sel.index.date
        fwd = (sel.reindex(days) - entry)
        for label, sig in [("gap do S&P", gap_s), ("gap do próprio WIN", gap_w),
                           ("WIN atrasado (beta x gapS&P - gapWIN)", beta * gap_s - gap_w)]:
            d = pd.DataFrame({"s": sig, "f": fwd})[ok].dropna()
            # usa só a metade do sinal mais forte (|sinal| acima da mediana)
            strong = d[d["s"].abs() > d["s"].abs().median()]
            pnl = np.sign(strong["s"]) * strong["f"]
            print(f"  9:05->{exit_t} | {label:<38} corr {d['s'].corr(d['f']):+.3f} | "
                  f"operando metade forte: {pnl.mean():+7.1f} pts/op (t {tstat(pnl):+.1f}, "
                  f"n={len(pnl)}) vs custo {COST['WIN']}")
            if exit_t in ("13:00", "17:45") and not label.startswith("WIN"):
                yr = pnl.groupby(pd.Index([x.year for x in pnl.index])).mean().round(1)
                print(f"      por ano (pts/op): {yr.to_dict()}")
    # controle: o gap do S&P ainda explica algo depois de descontar o gap do próprio WIN?
    sel = win[win.index.time == pd.Timestamp("13:00").time()]["close"]; sel.index = sel.index.date
    fwd = (sel.reindex(days) - entry) / entry
    d = pd.DataFrame({"f": fwd, "gs": gap_s, "gw": gap_w})[ok].dropna()
    X = np.column_stack([np.ones(len(d)), d["gs"], d["gw"]])
    coef, *_ = np.linalg.lstsq(X, d["f"], rcond=None)
    resid = d["f"] - X @ coef
    se = np.sqrt(np.diag(np.linalg.inv(X.T @ X)) * resid.var())
    print(f"  regressão 9:05->13:00 ~ gapS&P + gapWIN: t(gapS&P) = {coef[1]/se[1]:+.1f}, "
          f"t(gapWIN) = {coef[2]/se[2]:+.1f}")


def intraday_test(target: str, tgt: pd.DataFrame, ext: dict[str, pd.DataFrame]) -> None:
    print(f"\n================ TESTE 2: lead-lag intradiário -> {target} ================")
    day = pd.Index(tgt.index.date)
    t = pd.Series(tgt.index.time, index=tgt.index)
    ok = (t >= pd.Timestamp("09:10").time()) & (t <= pd.Timestamp("17:30").time())
    for name, e in ext.items():
        er = (e["close"] / e["open"] - 1).reindex(tgt.index)
        same = daily_ic(er[ok], (tgt["close"] / tgt["open"] - 1)[ok])
        line = f"  {name:<7} mesma barra IC {same.mean():+.2f}"
        for h in (1, 3, 6):
            entry = tgt["open"].groupby(day).shift(-1)
            fwd = tgt["close"].groupby(day).shift(-h) - entry
            ic = daily_ic(er[ok], fwd[ok])
            line += f" | {h * 5:>2} min: IC {ic.mean():+.3f} (t {tstat(ic):+.1f})"
        print(line)
        # economia: opera na direção (ou contra) do movimento extremo do mercado externo
        entry = tgt["open"].groupby(day).shift(-1)
        fwd = (tgt["close"].groupby(day).shift(-1) - entry)[ok]
        x = er[ok]
        hi = x > x.quantile(0.95)
        lo = x < x.quantile(0.05)
        pnl = pd.concat([fwd[hi], -fwd[lo]]).dropna()
        print(f"          seguindo os 10% movimentos mais fortes de {name} (5 min): "
              f"{pnl.mean():+.2f} pts/op (t {tstat(pnl.groupby(pnl.index.date).mean()):+.1f}) "
              f"vs custo {COST[target]}")


def main() -> None:
    win, wdo = load("WIN"), load("WDO")
    wsp, di1, dol = load("WSP_N"), load("DI1_N"), load("DOL_N")
    # DI "por liquidez" troca de contrato: descarta barras com salto > 30 bps
    jump = (di1["close"] - di1["close"].groupby(di1.index.date).shift()).abs() > 0.3
    di1 = di1[~jump]
    opening_test(win, wsp)
    intraday_test("WIN", win, {"WSP": wsp, "DI1": di1, "DOL": dol})
    intraday_test("WDO", wdo, {"WSP": wsp, "DI1": di1, "WIN": win})


if __name__ == "__main__":
    main()
