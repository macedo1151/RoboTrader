"""Laboratório de gerenciamento de risco para a carteira WIN (S&P noturno + reversão).

Gera os dados do dashboard em reports/dashboard/:
- trades120.csv   operações dos últimos 120 pregões, com MAE/MFE (pior e melhor ponto
                  atingido durante a operação)
- daily120.csv    resultado diário e acumulado por estratégia e da carteira
- risk_rules.csv  cada regra de risco testada em 3,6 anos (duas metades) e nos 120 pregões
- compound.csv    tamanho fixo x tamanho que cresce com o capital (juros compostos)

As regras são avaliadas no período INTEIRO; "Ajuda" exige melhorar a relação
resultado/queda máxima nas duas metades (até 02/2025 e depois).
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from robotrader.backtest import run_backtest  # noqa: E402
from robotrader.config import load_config  # noqa: E402
from robotrader.data import load_csv  # noqa: E402
from robotrader.indicators import atr  # noqa: E402
from robotrader.strategies import overnight_gaps  # noqa: E402
from robotrader.strategies.prev_day_reversal import daily_moves  # noqa: E402

OUT = ROOT / "reports" / "dashboard"
SPLIT = pd.Timestamp("2025-03-01")
PV, TICK, COST = 0.20, 5.0, 1.0   # R$/ponto, tick, custos ida e volta por contrato (R$)
NAMES = {"WIN": "S&P noturno", "WIN_REV": "Reversão dia anterior"}


# ---------------------------------------------------------------- dados base
def load():
    cfg = load_config()
    win = load_csv(ROOT / "data" / "WIN_M5.csv")
    trades = []
    for key, label in NAMES.items():
        t = run_backtest(win, cfg.instruments[key], cfg).trades.copy()
        t["estrategia"] = label
        trades.append(t)
    t = pd.concat(trades, ignore_index=True).sort_values("entry_time", ignore_index=True)
    a = atr(win, 14)
    idx = win.index
    t["p_in"] = idx.get_indexer(t.entry_time)
    t["p_out"] = idx.get_indexer(t.exit_time)
    t["atr"] = a.to_numpy()[t.p_in - 1]  # ATR da barra do sinal (9:00)
    # última barra com risco: no stop é a própria barra de saída; nas outras, a anterior
    t["p_last"] = np.where(t.reason.str.startswith("stop"), t.p_out, t.p_out - 1)
    H, L = win.high.to_numpy(), win.low.to_numpy()
    mfe, mae = [], []
    for r in t.itertuples():
        hi, lo = H[r.p_in:r.p_last + 1].max(), L[r.p_in:r.p_last + 1].min()
        if r.side > 0:
            mfe.append(hi - r.entry_price); mae.append(r.entry_price - lo)
        else:
            mfe.append(r.entry_price - lo); mae.append(hi - r.entry_price)
    t["mfe_pts"], t["mae_pts"] = mfe, mae
    t["dia"] = t.entry_time.dt.normalize()
    return cfg, win, t


# ---------------------------------------------------------------- saídas alternativas
@dataclass
class ExitRule:
    stop_k: float = 10.0        # stop inicial em ATR
    target_k: float | None = None
    breakeven_k: float | None = None   # após andar k ATR a favor, stop vai para a entrada
    trail_k: float | None = None       # após andar k ATR a favor, stop segue k ATR atrás
    stop_pts: float | None = None      # stop FIXO em pontos (substitui stop_k)
    target_pts: float | None = None    # alvo FIXO em pontos (substitui target_k)


def simulate_exits(t: pd.DataFrame, win: pd.DataFrame, rule: ExitRule) -> pd.Series:
    O, H, L = win.open.to_numpy(), win.high.to_numpy(), win.low.to_numpy()
    out = []
    for r in t.itertuples():
        s, e, A = r.side, r.entry_price, r.atr
        stop = e - s * (rule.stop_pts if rule.stop_pts else rule.stop_k * A)
        tgt = (e + s * rule.target_pts if rule.target_pts
               else e + s * rule.target_k * A if rule.target_k else None)
        best = e
        exit_px = None
        for b in range(r.p_in, r.p_last + 1):
            o, h, lo = O[b], H[b], L[b]
            if (s > 0 and lo <= stop) or (s < 0 and h >= stop):
                px = min(o, stop) if s > 0 else max(o, stop)
                exit_px = px - s * TICK
                break
            if tgt is not None and ((s > 0 and h >= tgt) or (s < 0 and lo <= tgt)):
                exit_px = tgt
                break
            best = max(best, h) if s > 0 else min(best, lo)
            run = (best - e) * s
            if rule.breakeven_k and run >= rule.breakeven_k * A:
                stop = max(stop, e) if s > 0 else min(stop, e)
            if rule.trail_k and run >= rule.trail_k * A:
                lvl = best - s * rule.trail_k * A
                stop = max(stop, lvl) if s > 0 else min(stop, lvl)
        if exit_px is None:
            exit_px = r.exit_price
        out.append((exit_px - e) * s * PV - COST)
    return pd.Series(out, index=t.index)


def daily_stop(t: pd.DataFrame, win: pd.DataFrame, limit: float) -> pd.Series:
    """Zera a carteira no fechamento da barra em que o resultado do dia (aberto + fechado)
    chega a -limit (R$ por contrato de cada estratégia)."""
    C = win.close.to_numpy()
    net = t.net.copy()
    for _, g in t.groupby("dia"):
        start, end = g.p_in.min(), g.p_out.max()
        for b in range(start, end + 1):
            total, active = 0.0, []
            for r in g.itertuples():
                if b < r.p_in:
                    continue
                if b >= r.p_out:
                    total += net[r.Index]
                else:
                    total += (C[b] - r.entry_price) * r.side * PV - COST
                    active.append(r)
            if total <= -limit and active:
                for r in active:
                    net[r.Index] = (C[b] - r.side * TICK - r.entry_price) * r.side * PV - COST
                break
    return net


# ---------------------------------------------------------------- métricas
def portfolio_stats(t: pd.DataFrame, net: pd.Series) -> dict:
    daily = net.groupby(t.dia).sum()

    def part(d):
        if d.empty:
            return 0.0, 0.0, float("nan")
        eq = d.cumsum()
        dd = float((eq - eq.cummax().clip(lower=0)).min())
        return float(d.sum()), dd, (float(d.sum()) / -dd if dd < 0 else float("nan"))

    full = part(daily)
    disc = part(daily[daily.index < SPLIT])
    conf = part(daily[daily.index >= SPLIT])
    months = daily.groupby(daily.index.to_period("M")).sum()
    return {"liquido": full[0], "queda_max": full[1], "razao": full[2],
            "razao_descoberta": disc[2], "razao_confirmacao": conf[2],
            "meses_negativos_pct": 100 * float((months < 0).mean())}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg, win, t = load()
    days = win.index.normalize().unique()
    last120 = days[-120:]
    in120 = t.dia.isin(last120)
    base = t.net.copy()

    # sanidade: o simulador sem regras novas reproduz o motor
    check = simulate_exits(t, win, ExitRule())
    print(f"simulador x motor: diferença máxima R$ {np.abs(check - base).max():.2f}")

    # ---------------- regras
    rules: list[tuple[str, str, str, pd.Series]] = []
    add = lambda name, cat, desc, net: rules.append((name, cat, desc, net))  # noqa: E731
    add("Atual", "Referência", "1 contrato em cada estratégia, stop de emergência 10 ATR, saída por horário.", base)

    # reduzir perdas
    add("Stop 5 ATR", "Reduzir perdas", "Stop inicial na metade da distância atual.",
        simulate_exits(t, win, ExitRule(stop_k=5)))
    add("Stop no zero após 2 ATR", "Reduzir perdas",
        "Depois de andar 2 ATR a favor, o stop vai para o preço de entrada.",
        simulate_exits(t, win, ExitRule(breakeven_k=2)))
    add("Trailing 3 ATR", "Reduzir perdas",
        "Depois de andar 3 ATR a favor, o stop passa a seguir o preço 3 ATR atrás.",
        simulate_exits(t, win, ExitRule(trail_k=3)))
    for lim in (300, 500):
        add(f"Limite diário R$ {lim}", "Reduzir perdas",
            f"Se a carteira perder R$ {lim} no dia (por contrato de cada), zera tudo.",
            daily_stop(t, win, lim))
    conflict_days = t.groupby("dia").side.agg(lambda s: len(s) == 2 and s.nunique() == 2)
    conflict_days = set(conflict_days[conflict_days].index)
    add("Não operar em conflito", "Reduzir perdas",
        "Nos dias em que uma estratégia compra e a outra vende, não opera nenhuma.",
        base.where(~t.dia.isin(conflict_days), 0.0))

    # ampliar ganhos
    for k in (2, 3, 4):
        add(f"Alvo {k} ATR", "Ampliar ganhos", f"Realiza o lucro ao andar {k} ATR a favor.",
            simulate_exits(t, win, ExitRule(target_k=k)))

    # saídas fixas em pontos (pedido do usuário)
    add("Alvo fixo 2.000 pts", "Saída fixa", "Realiza o lucro ao andar 2.000 pontos a favor; stop de 10 ATR mantido.",
        simulate_exits(t, win, ExitRule(target_pts=2000)))
    add("Stop fixo 500 pts", "Saída fixa", "Stop a 500 pontos da entrada no lugar do stop de 10 ATR.",
        simulate_exits(t, win, ExitRule(stop_pts=500)))
    add("Alvo 2.000 + stop 500 pts", "Saída fixa", "Alvo de 2.000 e stop de 500 pontos juntos.",
        simulate_exits(t, win, ExitRule(stop_pts=500, target_pts=2000)))

    # dimensionamento (mesmo número MÉDIO de contratos que a referência)
    def sized(name, desc, w):
        w = pd.Series(w, index=t.index).astype(float)
        add(name, "Tamanho da posição", desc + " Escalado para a mesma média de contratos.",
            base * w / w.mean())

    atr_day = t.groupby("dia").atr.first()
    ref = atr_day.shift(1).rolling(120, min_periods=40).median()
    sized("Tamanho pela volatilidade", "Mais contratos em dias calmos, menos em dias agitados (ATR).",
          (t.dia.map(ref) / t.atr).clip(0.5, 2).fillna(1))

    gaps = overnight_gaps(load_csv(ROOT / "data" / "external" / "WSP_N_M5.csv"))
    gq = gaps.abs().shift(1).rolling(120, min_periods=60).quantile(0.75)
    gap_strong = (gaps.abs() > gq).set_axis(pd.to_datetime(gaps.index))
    mv = daily_moves(win)
    mq = mv.abs().shift(1).rolling(120, min_periods=60).quantile(0.75)
    mv_strong = (mv.abs() > mq).shift(1)  # sinal de hoje vem do movimento de ontem
    mv_strong.index = pd.to_datetime(mv_strong.index)
    strong = np.where(t.estrategia == NAMES["WIN"], t.dia.map(gap_strong), t.dia.map(mv_strong))
    sized("Dobra no sinal forte", "2 contratos quando o gatilho está no quarto mais forte, senão 1.",
          np.where(pd.Series(strong).fillna(False).astype(bool), 2, 1))
    agree = t.groupby("dia").side.agg(lambda s: len(s) == 2 and s.nunique() == 1)
    sized("Dobra quando concordam", "2 contratos em cada quando as duas apontam para o mesmo lado.",
          np.where(t.dia.map(agree).fillna(False).astype(bool), 2, 1))
    net_pos = np.where(t.dia.isin(conflict_days), 0,
                       np.where(t.dia.map(agree).fillna(False).astype(bool), 2, 1))
    sized("Posição líquida das duas", "Não opera quando discordam e usa 2 contratos quando concordam "
          "(o que uma conta netting faz sozinha).", net_pos)
    prev_win = t.groupby("estrategia").net.shift(1) > 0
    sized("Dobra após ganho", "2 contratos depois de uma operação vencedora da mesma estratégia.",
          np.where(prev_win, 2, 1))
    eq = base.cumsum()
    under = (eq.cummax() - eq).shift(1).fillna(0) > 1500
    sized("Reduz na queda", "Metade do tamanho enquanto a carteira está R$ 1.500 abaixo do topo.",
          np.where(under, 0.5, 1))

    # ---------------- tabela de regras
    ref_stats = portfolio_stats(t, base)
    rows = []
    for name, cat, desc, net in rules:
        st = portfolio_stats(t, net)
        last = portfolio_stats(t[in120], net[in120])
        better_d = st["razao_descoberta"] > ref_stats["razao_descoberta"] + 0.05
        better_c = st["razao_confirmacao"] > ref_stats["razao_confirmacao"] + 0.05
        worse_d = st["razao_descoberta"] < ref_stats["razao_descoberta"] - 0.05
        worse_c = st["razao_confirmacao"] < ref_stats["razao_confirmacao"] - 0.05
        verdict = ("Referência" if name == "Atual" else "Ajuda" if better_d and better_c
                   else "Piora" if worse_d and worse_c else "Inconclusivo")
        rows.append({
            "regra": name, "categoria": cat, "descricao": desc,
            "liquido_3a": round(st["liquido"]), "queda_max_3a": round(st["queda_max"]),
            "razao_3a": round(st["razao"], 2),
            "razao_descoberta": round(st["razao_descoberta"], 2),
            "razao_confirmacao": round(st["razao_confirmacao"], 2),
            "meses_negativos_pct": round(st["meses_negativos_pct"]),
            "liquido_120": round(last["liquido"]), "queda_max_120": round(last["queda_max"]),
            "veredito": verdict,
        })
    rr = pd.DataFrame(rows)
    rr.to_csv(OUT / "risk_rules.csv", index=False)
    pd.set_option("display.width", 250)
    print(rr.drop(columns=["descricao"]).to_string(index=False))

    # ---------------- operações e dias dos últimos 120 pregões
    t120 = t[in120].copy()
    tr = pd.DataFrame({
        "id": t120.estrategia.map({NAMES["WIN"]: "SP", NAMES["WIN_REV"]: "REV"}) + "-"
              + t120.dia.dt.strftime("%Y-%m-%d"),
        "estrategia": t120.estrategia,
        "data": t120.dia.dt.strftime("%Y-%m-%d"),
        "lado": np.where(t120.side > 0, "Compra", "Venda"),
        "entrada": t120.entry_time.dt.strftime("%H:%M"),
        "preco_entrada": t120.entry_price,
        "saida": t120.exit_time.dt.strftime("%H:%M"),
        "preco_saida": t120.exit_price,
        "pontos": t120.points,
        "resultado": t120.net,
        "motivo": t120.reason.replace({"saída": "horário", "stop": "stop", "zeragem": "zeragem"}),
        "melhor_pts": t120.mfe_pts.round(0),
        "pior_pts": t120.mae_pts.round(0),
        "atr_pts": t120.atr.round(0),
    })
    tr.to_csv(OUT / "trades120.csv", index=False)

    d = t120.pivot_table(index="dia", columns="estrategia", values="net", aggfunc="sum")
    d = d.reindex(pd.to_datetime(last120)).fillna(0.0)
    daily = pd.DataFrame({
        "data": d.index.strftime("%Y-%m-%d"),
        "sp": d.get(NAMES["WIN"], 0.0), "rev": d.get(NAMES["WIN_REV"], 0.0),
    })
    daily["carteira"] = daily.sp + daily.rev
    for c in ("sp", "rev", "carteira"):
        daily[f"acum_{c}"] = daily[c].cumsum()
    daily.to_csv(OUT / "daily120.csv", index=False)

    # ---------------- juros compostos: capital inicial R$ 50 mil, R$ 10 mil por par de
    # contratos (R$ 5 mil por contrato de cada estratégia, ~2,5x a queda máxima histórica)
    per_day = base.groupby(t.dia).sum().reindex(pd.to_datetime(days), fill_value=0.0)
    cap0, per_pair = 50_000.0, 10_000.0
    comp, eq_c = [], cap0
    for pnl in per_day:
        n = max(1, math.floor(eq_c / per_pair))   # recalcula o tamanho todo dia
        eq_c += n * pnl
        comp.append(eq_c)
    fixed = cap0 + (cap0 / per_pair) * per_day.cumsum()
    cm = pd.DataFrame({"data": per_day.index, "fixo": fixed.to_numpy(), "composto": comp})
    cm = cm.groupby(cm.data.dt.to_period("M")).last().reset_index(drop=True)
    cm["data"] = cm.data.dt.strftime("%Y-%m")
    for c in ("fixo", "composto"):
        e = cm[c]
        cm[f"queda_{c}"] = (e - e.cummax()).round(0)
    cm.round(0).to_csv(OUT / "compound.csv", index=False)
    print(f"\njuros compostos: fixo R$ {cm.fixo.iloc[-1]:,.0f} | composto R$ {cm.composto.iloc[-1]:,.0f} "
          f"| pior queda fixo R$ {cm.queda_fixo.min():,.0f} | composto R$ {cm.queda_composto.min():,.0f}")
    print(f"últimos 120: {len(tr)} operações | carteira R$ {daily.carteira.sum():+.0f}")
    print(tr.groupby(["estrategia", "motivo"]).resultado.agg(["count", "sum"]).round(0).to_string())
    lost_after_gain = tr[(tr.resultado < 0) & (tr.melhor_pts >= 2 * tr.atr_pts)]
    print(f"perdedoras que chegaram a +2 ATR antes: {len(lost_after_gain)} (R$ {lost_after_gain.resultado.sum():+.0f})")


if __name__ == "__main__":
    main()
