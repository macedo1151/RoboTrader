"""Compara, no motor real, as carteiras configuradas: sem regra, posição líquida, + trailing."""
import sys
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.stdout.reconfigure(encoding="utf-8")
from robotrader.backtest import run_backtest
from robotrader.config import load_config
from robotrader.data import load_csv
from robotrader.external import load_external
from robotrader.portfolio import run_portfolio

SPLIT = pd.Timestamp("2025-03-01")
cfg = load_config(); win = load_csv(ROOT / "data" / "WIN_M5.csv"); ext = load_external(["WSP$N"])
last120 = win.index.normalize().unique()[-120]


def stats(trades: pd.DataFrame) -> dict:
    d = trades.groupby(pd.to_datetime(trades.entry_time).dt.normalize()).net.sum()
    def part(x):
        eq = x.cumsum(); dd = float((eq - eq.cummax().clip(lower=0)).min())
        return float(x.sum()), dd, (float(x.sum()) / -dd if dd < 0 else float("nan"))
    f, a, b, c = part(d), part(d[d.index < SPLIT]), part(d[d.index >= SPLIT]), part(d[d.index >= last120])
    m = d.groupby(d.index.to_period("M")).sum()
    contracts = trades.qty.sum() / max(1, d.index.nunique())
    return {"liquido": round(f[0]), "queda": round(f[1]), "razao": round(f[2], 2),
            "razao_1a": round(a[2], 2), "razao_2a": round(b[2], 2),
            "meses_neg_%": round(100 * (m < 0).mean()), "liq_120": round(c[0]), "queda_120": round(c[1]),
            "contratos/dia": round(contracts, 2)}


rows = {}
solo = {n: run_backtest(win, cfg.instruments[n], cfg, external=ext).trades for n in ["WIN", "WIN_REV", "WIN_TR", "WIN_REV_TR"]}
rows["Sem regra (1 contrato cada)"] = stats(pd.concat([solo["WIN"], solo["WIN_REV"]]))
rows[f"Sem regra + trailing {cfg.instruments['WIN_TR'].trail_atr:g} ATR"] = stats(pd.concat([solo["WIN_TR"], solo["WIN_REV_TR"]]))
for g in cfg.portfolios:
    res = run_portfolio(win, cfg, g, external=ext)
    rows[f"Grupo '{g.name}' (posição líquida{' + trailing' if g.name == 'trailing' else ''})"] = \
        stats(pd.concat([r.trades for r in res.values()]))
out = pd.DataFrame(rows).T
pd.set_option("display.width", 220)
print(out.to_string())
out.to_csv(ROOT / "reports" / "portfolio_check.csv")
