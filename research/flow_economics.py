"""Tamanho econômico do efeito de reversão do fluxo (entrada na ABERTURA da barra seguinte)."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.stdout.reconfigure(encoding="utf-8")
from robotrader.flow import load_flow

EXCLUDE = {pd.Timestamp("2026-10-05").date(), pd.Timestamp("2026-10-26").date()}
COST = {"WIN": 15.0, "WDO": 1.3}
for root in ["WDO", "WIN"]:
    b = load_flow(ROOT / "data" / "flow" / f"{root}_M1_flow.csv", 5)
    b = b[~pd.Index(b.index.date).isin(EXCLUDE)]
    day = pd.Index(b.index.date)
    cum = b["delta"].groupby(day).cumsum()
    z = (cum - cum.groupby(day).transform(lambda s: s.expanding().mean())) / \
        cum.groupby(day).transform(lambda s: s.expanding().std())
    imb12 = b["delta"].groupby(day).transform(lambda s: s.rolling(12).sum()) / \
        b["volume"].groupby(day).transform(lambda s: s.rolling(12).sum())
    entry = b["open"].groupby(day).shift(-1)
    t = pd.Series(b.index.time, index=b.index)
    ok = (t >= pd.Timestamp("09:30").time()) & (t <= pd.Timestamp("16:30").time())
    print(f"\n===== {root}: custo ida e volta ≈ {COST[root]} pts | {day.nunique()} pregões =====")
    for name, sig in [("delta acumulado do dia (z)", z), ("desequilíbrio 12 barras", imb12)]:
        for h in (6, 12, 24):
            exit_ = b["close"].groupby(day).shift(-h)
            fwd = (exit_ - entry)[ok]
            s = sig[ok]
            dec = pd.qcut(s, 10, labels=False, duplicates="drop")
            m = fwd.groupby(dec).mean()
            # operação contrária nos extremos: vende decil 9, compra decil 0
            contra = pd.concat([-fwd[dec == 9], fwd[dec == 0]]).dropna()
            dd = contra.groupby(pd.Index(contra.index.date)).mean()
            tstat = dd.mean() / dd.std() * np.sqrt(len(dd))
            print(f"  {name:<28} {h*5:>3} min | decis (pts): {[round(x,2) for x in m.values]}")
            print(f"  {'':<28}         | contrária nos extremos: {contra.mean():+.2f} pts/op "
                  f"(t {tstat:+.1f}) vs custo {COST[root]} -> líquido {contra.mean()-COST[root]:+.2f}")
