"""Alvo de lucro (saída ao atingir R$ X por contrato): vale a pena? Teste em 3,6 anos.

Para cada estratégia e para a carteira (1 contrato de cada), simula alvos fixos em R$ e em
ATR (5 min), com o stop de 10 ATR e a saída por horário mantidos. Avalia: período inteiro,
as duas metades (antes/depois de SPLIT) e os últimos 120 pregões. Teste extra: walk-forward
da ESCOLHA do alvo (a cada 21 pregões, o alvo com melhor resultado/queda nos 250 anteriores).

Saída: reports/dashboard/target_rules.csv e wf_target.csv
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from risk_lab import OUT, PV, SPLIT, ExitRule, load, simulate_exits  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
BRL = (100, 150, 200, 250, 300, 400, 500, 600, 800, 1000)
ATRS = (1, 2, 3, 4, 6)


def dd_ratio(d: pd.Series) -> tuple[float, float, float]:
    if d.empty:
        return 0.0, 0.0, float("nan")
    eq = d.cumsum()
    dd = float((eq - eq.cummax().clip(lower=0)).min())
    return float(d.sum()), dd, (float(d.sum()) / -dd if dd < 0 else float("nan"))


def main() -> None:
    cfg, win, t = load()
    days = pd.to_datetime(win.index.normalize().unique())
    last120 = set(days[-120:])
    nets: dict[str, pd.Series] = {"Sem alvo": t.net.copy()}
    for g in BRL:
        nets[f"R$ {g}"] = simulate_exits(t, win, ExitRule(target_pts=g / PV))
    for k in ATRS:
        nets[f"{k} ATR"] = simulate_exits(t, win, ExitRule(target_k=k))
    chk = (simulate_exits(t, win, ExitRule()) - t.net).abs().max()
    print(f"simulador x motor: diferença máx R$ {chk:.2f}")

    scopes = {"Carteira": np.ones(len(t), bool)}
    for e in t.estrategia.unique():
        scopes[e] = (t.estrategia == e).to_numpy()

    rows = []
    for sc, m in scopes.items():
        for name, net in nets.items():
            d = net[m].groupby(t.dia[m]).sum()
            full, h1, h2 = dd_ratio(d), dd_ratio(d[d.index < SPLIT]), dd_ratio(d[d.index >= SPLIT])
            l120 = float(d[d.index.isin(last120)].sum())
            rows.append({"escopo": sc, "alvo": name, "liquido_3a": round(full[0]), "queda_max_3a": round(full[1]),
                         "razao_3a": round(full[2], 2), "liquido_1a_metade": round(h1[0]), "liquido_2a_metade": round(h2[0]),
                         "liquido_120": round(l120)})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "target_rules.csv", index=False)
    pd.set_option("display.width", 220)
    print(res.to_string(index=False))

    # ---- walk-forward da escolha do alvo
    out = []
    for sc, m in scopes.items():
        daily = pd.DataFrame({n: net[m].groupby(t.dia[m]).sum() for n, net in nets.items()}).reindex(
            days, fill_value=0.0).fillna(0.0)
        oos, picks = [], []
        for s in range(250, len(daily), 21):
            train = daily.iloc[s - 250:s]
            score = {c: (r if np.isfinite(r := dd_ratio(train[c])[2]) else -1e9) for c in daily.columns}
            best = max(score, key=score.get)
            oos.append(daily.iloc[s:s + 21][best]); picks.append(best)
        wf = pd.concat(oos)
        base = daily["Sem alvo"].loc[wf.index]
        f, b = dd_ratio(wf), dd_ratio(base)
        top = pd.Series(picks).value_counts().head(3).to_dict()
        out.append({"escopo": sc, "periodo": f"{wf.index[0]:%m/%Y} a {wf.index[-1]:%m/%Y}",
                    "wf_liquido": round(f[0]), "wf_queda": round(f[1]),
                    "sem_alvo_liquido": round(b[0]), "sem_alvo_queda": round(b[1]), "alvos_mais_escolhidos": str(top)})
    wfd = pd.DataFrame(out)
    wfd.to_csv(OUT / "wf_target.csv", index=False)
    print(wfd.to_string(index=False))


if __name__ == "__main__":
    main()
