"""Operações dos últimos 120 pregões da carteira principal com trailing de 4 ATR (5 min),
ao lado do resultado da mesma operação sem trailing. Saída: reports/dashboard/trades120_trail.csv"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from risk_lab import COST, OUT, PV, TICK  # noqa: E402
from risk_lab2 import load, simulate  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
K = 4


def main() -> None:
    cfg, win, t = load()
    O, H, L = win.open.to_numpy(), win.high.to_numpy(), win.low.to_numpy()
    rows = []
    for r in t.itertuples():
        s, e, trd = r.side, r.entry_price, K * r.atr
        stop0 = e - s * 10 * r.atr
        stop, best, exit_px, exit_b, why = stop0, e, None, None, "horário"
        for b in range(r.p_in, r.p_last + 1):
            if (s > 0 and L[b] <= stop) or (s < 0 and H[b] >= stop):
                exit_px = (min(O[b], stop) if s > 0 else max(O[b], stop)) - s * TICK
                exit_b, why = b, ("stop" if stop == stop0 else "trailing")
                break
            best = max(best, H[b]) if s > 0 else min(best, L[b])
            if (best - e) * s >= trd:
                lvl = best - s * trd
                stop = max(stop, lvl) if s > 0 else min(stop, lvl)
        if exit_px is None:
            exit_px, exit_t = r.exit_price, r.exit_time
        else:
            exit_t = win.index[exit_b]
        pts = (exit_px - e) * s
        rows.append((pts, exit_px, exit_t, why))
    t["pts_t"], t["px_t"], t["exit_t"], t["motivo_t"] = zip(*rows)
    t["net_t"] = t.pts_t * PV * t.qty - COST * t.qty
    chk = (simulate(t, win, 10 * t.atr, trail_d=K * t.atr) * t.qty - t.net_t).abs().max()
    print(f"conferência com o simulador da rodada 2: diferença máx R$ {chk:.2f}")

    days = win.index.normalize().unique()
    m = t.dia.isin(days[-120:])
    x = t[m]
    out = pd.DataFrame({
        "id": x.estrategia.map({"S&P noturno": "SP", "Reversão dia anterior": "REV"}) + "-" + x.dia.dt.strftime("%Y-%m-%d"),
        "data": x.dia.dt.strftime("%Y-%m-%d"), "estrategia": x.estrategia,
        "lado": np.where(x.side > 0, "Compra", "Venda"), "contratos": x.qty.astype(int),
        "saida_sem": x.exit_time.dt.strftime("%H:%M"), "resultado_sem": x.net.round(0),
        "saida_trail": pd.to_datetime(x.exit_t).dt.strftime("%H:%M"), "motivo_trail": x.motivo_t,
        "pontos_trail": x.pts_t.round(0), "resultado_trail": x.net_t.round(0)})
    out.to_csv(OUT / "trades120_trail.csv", index=False)
    print(len(out), "operações | sem trailing", round(x.net.sum()), "| com trailing", round(x.net_t.sum()))
    print(out.motivo_trail.value_counts().to_dict(), "| melhor/pior op com trailing:", out.resultado_trail.max(), out.resultado_trail.min())
    d = x.groupby("dia").net_t.sum(); eq = d.cumsum(); print("pior queda", round((eq - eq.cummax().clip(lower=0)).min()))


if __name__ == "__main__":
    main()
