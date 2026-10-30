"""Rodada 2 de regras de risco, sobre a CARTEIRA PRINCIPAL (posição líquida WIN + WIN_REV).

Regras definidas antes dos resultados (famílias com motivo):
- trailing com outras distâncias (2, 3, 4, 5 ATR de 5 min);
- medidas que acompanham o nível do índice: alvo em % do preço, alvo e trailing em
  múltiplos do ATR DIÁRIO (amplitude típica de um pregão);
- saída parcial: metade no alvo, metade até o horário;
- filtro de volatilidade (ATR diário / preço acima ou abaixo da mediana de 120 pregões);
- pesos entre as estratégias e multiplicador de concordância.
Regras de tamanho são escaladas para a mesma média de contratos da referência.

Critério (o mesmo de risk_lab.py): "Ajuda" = razão resultado/queda maior nas DUAS metades.
Teste extra: walk-forward da ESCOLHA de regra (a cada 21 pregões, usa a regra com melhor
razão nos 250 anteriores). Se isso não supera a referência, as "vencedoras" podem ser sorte.

Saída: reports/dashboard/risk_rules2.csv e wf_rules.csv
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

from risk_lab import COST, NAMES, PV, SPLIT, TICK, portfolio_stats  # noqa: E402
from robotrader.config import load_config  # noqa: E402
from robotrader.data import load_csv  # noqa: E402
from robotrader.external import load_external  # noqa: E402
from robotrader.indicators import atr  # noqa: E402
from robotrader.portfolio import run_portfolio  # noqa: E402

OUT = ROOT / "reports" / "dashboard"


def load():
    cfg = load_config()
    win = load_csv(ROOT / "data" / "WIN_M5.csv")
    group = next(g for g in cfg.portfolios if g.name == "principal")
    res = run_portfolio(win, cfg, group, external=load_external(["WSP$N"]))
    t = pd.concat([r.trades.assign(estrategia=NAMES[n]) for n, r in res.items()], ignore_index=True)
    t = t.sort_values("entry_time", ignore_index=True)
    idx = win.index
    t["p_in"] = idx.get_indexer(t.entry_time)
    t["p_out"] = idx.get_indexer(t.exit_time)
    t["p_last"] = np.where(t.reason.str.startswith("stop"), t.p_out, t.p_out - 1)
    t["atr"] = atr(win, 14).to_numpy()[t.p_in - 1]
    t["dia"] = t.entry_time.dt.normalize()
    # ATR diário (amplitude verdadeira de pregões completos), conhecido antes do dia
    g = win.groupby(win.index.normalize())
    d = pd.DataFrame({"h": g.high.max(), "l": g.low.min(), "c": g.close.last()})
    tr = pd.concat([d.h - d.l, (d.h - d.c.shift()).abs(), (d.l - d.c.shift()).abs()], axis=1).max(axis=1)
    atr_d = tr.rolling(14).mean().shift(1)
    vol = (atr_d / d.c.shift(1))
    t["atr_d"] = t.dia.map(atr_d)
    t["vol_rel"] = t.dia.map(vol / vol.shift(1).rolling(120, min_periods=60).median())
    return cfg, win, t


def simulate(t, win, stop_d, tgt_d=None, trail_d=None, part_d=None, part_frac=0.5) -> pd.Series:
    """Resultado por CONTRATO de cada operação com distâncias por operação (em pontos).
    stop_d: stop inicial; tgt_d: alvo total; trail_d: trailing (ativa ao andar essa
    distância); part_d: alvo da parte `part_frac` (o resto segue as demais regras)."""
    O, H, L = win.open.to_numpy(), win.high.to_numpy(), win.low.to_numpy()
    arr = lambda x: None if x is None else np.asarray(x, dtype=float)  # noqa: E731
    stop_d, tgt_d, trail_d, part_d = map(arr, (stop_d, tgt_d, trail_d, part_d))
    out = []
    for i, r in enumerate(t.itertuples()):
        s, e = r.side, r.entry_price
        stop = e - s * stop_d[i]
        tgt = e + s * tgt_d[i] if tgt_d is not None and tgt_d[i] > 0 else None
        trd = trail_d[i] if trail_d is not None and trail_d[i] > 0 else None
        ptg = e + s * part_d[i] if part_d is not None and part_d[i] > 0 else None
        best, done_part, pnl_part, exit_px = e, False, 0.0, None
        for b in range(r.p_in, r.p_last + 1):
            o, h, lo = O[b], H[b], L[b]
            if (s > 0 and lo <= stop) or (s < 0 and h >= stop):
                exit_px = (min(o, stop) if s > 0 else max(o, stop)) - s * TICK
                break
            if ptg is not None and not done_part and ((s > 0 and h >= ptg) or (s < 0 and lo <= ptg)):
                done_part, pnl_part = True, (ptg - e) * s
            if tgt is not None and ((s > 0 and h >= tgt) or (s < 0 and lo <= tgt)):
                exit_px = tgt
                break
            best = max(best, h) if s > 0 else min(best, lo)
            if trd and (best - e) * s >= trd:
                lvl = best - s * trd
                stop = max(stop, lvl) if s > 0 else min(stop, lvl)
        if exit_px is None:
            exit_px = r.exit_price
        rest = (exit_px - e) * s
        pts = part_frac * pnl_part + (1 - part_frac) * rest if done_part else rest
        out.append(pts * PV - COST)
    return pd.Series(out, index=t.index)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg, win, t = load()
    qty = t.qty.astype(float)
    base_pc = simulate(t, win, 10 * t.atr)
    base = base_pc * qty
    print(f"simulador x motor (carteira principal): diferença máx R$ {np.abs(base - t.net).max():.2f} "
          f"| total motor R$ {t.net.sum():.0f}")

    rules: list[tuple[str, str, str, pd.Series]] = [
        ("Carteira principal", "Referência", "Posição líquida WIN + WIN_REV, stop 10 ATR, saída por horário.", base)]
    add = lambda n, c, d, net: rules.append((n, c, d, net))  # noqa: E731
    A, AD, P = t.atr, t.atr_d, t.entry_price
    stop10 = 10 * A

    for k in (2, 3, 4, 5):
        add(f"Trailing {k} ATR", "Trailing", f"Stop segue o preço a {k} ATR (5 min) depois de andar {k} ATR a favor.",
            simulate(t, win, stop10, trail_d=k * A) * qty)
    for k in (0.3, 0.5):
        add(f"Trailing {k:g} ATR diário", "Trailing",
            f"Trailing a {k:g} x a amplitude média diária (ATR diário de 14 pregões).",
            simulate(t, win, stop10, trail_d=k * AD) * qty)
    for pct in (1.0, 1.5):
        add(f"Alvo {pct:g}% do preço", "Alvo", f"Realiza ao andar {pct:g}% do preço de entrada a favor.",
            simulate(t, win, stop10, tgt_d=pct / 100 * P) * qty)
    for k in (0.75, 1.0):
        add(f"Alvo {k:g} ATR diário", "Alvo", f"Realiza ao andar {k:g} x a amplitude média diária.",
            simulate(t, win, stop10, tgt_d=k * AD) * qty)
    add("Parcial: metade em 3 ATR", "Saída parcial", "Metade sai em 3 ATR (5 min) a favor; o resto até o horário.",
        simulate(t, win, stop10, part_d=3 * A) * qty)
    add("Parcial: metade em 1%", "Saída parcial", "Metade sai em 1% do preço a favor; o resto até o horário.",
        simulate(t, win, stop10, part_d=0.01 * P) * qty)
    add("Parcial: metade em 0,75 ATR diário", "Saída parcial",
        "Metade sai em 0,75 ATR diário a favor; o resto até o horário.",
        simulate(t, win, stop10, part_d=0.75 * AD) * qty)
    add("Parcial 1% + trailing 3 ATR", "Saída parcial",
        "Metade sai em 1% a favor; o resto com trailing de 3 ATR.",
        simulate(t, win, stop10, trail_d=3 * A, part_d=0.01 * P) * qty)

    hi_vol = (t.vol_rel > 1).fillna(False)
    add("Só com volatilidade alta", "Filtro", "Opera só quando a amplitude diária (relativa ao preço) está "
        "acima da mediana dos 120 pregões anteriores.", base.where(hi_vol, 0.0))
    add("Só com volatilidade baixa", "Filtro", "Opera só quando está abaixo da mediana.",
        base.where(~hi_vol, 0.0))

    def sized(name, desc, w):
        w = pd.Series(np.asarray(w, dtype=float), index=t.index)
        scale = qty.sum() / (qty * w).sum()
        add(name, "Tamanho", desc + " Escalado para a mesma média de contratos.", base * w * scale)

    is_sp = (t.estrategia == NAMES["WIN"]).to_numpy()
    sized("Peso 2:1 para o S&P noturno", "Dobra o peso da estratégia do S&P.", np.where(is_sp, 2, 1))
    sized("Peso 2:1 para a reversão", "Dobra o peso da reversão do dia anterior.", np.where(is_sp, 1, 2))
    agree = (t.qty > 1).to_numpy()
    sized("Concordância 3x", "3 contratos (em vez de 2) quando as duas concordam.", np.where(agree, 1.5, 1))
    sized("Concordância 1x", "Só evita os conflitos; não aumenta quando concordam.", np.where(agree, 0.5, 1))

    # ---------------- tabela
    days = win.index.normalize().unique()
    in120 = t.dia.isin(days[-120:])
    ref = portfolio_stats(t, base)
    rows = []
    for name, cat, desc, net in rules:
        st, last = portfolio_stats(t, net), portfolio_stats(t[in120], net[in120])
        bd = st["razao_descoberta"] > ref["razao_descoberta"] + 0.05
        bc = st["razao_confirmacao"] > ref["razao_confirmacao"] + 0.05
        wd = st["razao_descoberta"] < ref["razao_descoberta"] - 0.05
        wc = st["razao_confirmacao"] < ref["razao_confirmacao"] - 0.05
        verdict = ("Referência" if cat == "Referência" else "Ajuda" if bd and bc
                   else "Piora" if wd and wc else "Inconclusivo")
        rows.append({"regra": name, "categoria": cat, "descricao": desc,
                     "liquido_3a": round(st["liquido"]), "queda_max_3a": round(st["queda_max"]),
                     "razao_3a": round(st["razao"], 2), "razao_descoberta": round(st["razao_descoberta"], 2),
                     "razao_confirmacao": round(st["razao_confirmacao"], 2),
                     "meses_negativos_pct": round(st["meses_negativos_pct"]),
                     "liquido_120": round(last["liquido"]), "queda_max_120": round(last["queda_max"]),
                     "veredito": verdict})
    rr = pd.DataFrame(rows)
    rr.to_csv(OUT / "risk_rules2.csv", index=False)
    pd.set_option("display.width", 260)
    print(rr.drop(columns=["descricao", "categoria"]).to_string(index=False))

    # ---------------- walk-forward da escolha de regra
    daily = pd.DataFrame({n: net.groupby(t.dia).sum() for n, _, _, net in rules}).reindex(
        pd.to_datetime(days), fill_value=0.0).fillna(0.0)

    def ratio(x):
        eq = x.cumsum(); dd = (eq - eq.cummax().clip(lower=0)).min()
        return x.sum() / -dd if dd < 0 else (np.inf if x.sum() > 0 else 0)

    picks, oos = [], []
    for start in range(250, len(daily), 21):
        train = daily.iloc[start - 250:start]
        best = max(daily.columns, key=lambda c: ratio(train[c]))
        test = daily.iloc[start:start + 21]
        oos.append(test[best])
        picks.append({"inicio_teste": test.index[0].strftime("%Y-%m-%d"), "regra_escolhida": best})
    wf = pd.concat(oos)
    ref_oos = daily["Carteira principal"].loc[wf.index]
    pk = pd.DataFrame(picks)
    pk.to_csv(OUT / "wf_rules.csv", index=False)

    def summary(x):
        eq = x.cumsum(); dd = (eq - eq.cummax().clip(lower=0)).min()
        return f"R$ {x.sum():+,.0f} | queda R$ {dd:,.0f} | razão {x.sum() / -dd:.2f}"
    def row(name, x):
        eq = x.cumsum(); dd = float((eq - eq.cummax().clip(lower=0)).min())
        return {"cenario": name, "liquido": round(float(x.sum())), "queda_max": round(dd),
                "razao": round(float(x.sum()) / -dd, 2) if dd < 0 else None}
    pd.DataFrame([row("Trocar todo mês para a melhor regra do ano anterior", wf),
                  row("Manter sempre a carteira principal", ref_oos)]).to_csv(OUT / "wf_summary.csv", index=False)
    print(f"\nWalk-forward da escolha de regra ({wf.index[0]:%m/%Y} a {wf.index[-1]:%m/%Y}):")
    print("  escolhendo a melhor regra do ano anterior:", summary(wf))
    print("  sempre a carteira principal:             ", summary(ref_oos))
    print("  regras mais escolhidas:", pk.regra_escolhida.value_counts().head(5).to_dict())


if __name__ == "__main__":
    main()
