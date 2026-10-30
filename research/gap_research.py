"""Validação da estratégia "S&P noturno x abertura do WIN" (external_gap).

1. Regra base (definida pela análise, sem ajuste) no motor real, ano a ano.
2. Robustez: grade de horário de saída x filtro x stop no período inteiro.
3. Walk-forward (250 pregões de treino, 21 de teste).
4. Permutação: embaralha os gaps entre os dias e recalcula; p-valor = fração das
   permutações com resultado >= o real.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from robotrader.backtest import run_backtest  # noqa: E402
from robotrader.config import load_config  # noqa: E402
from robotrader.data import load_csv  # noqa: E402
from robotrader.research import Candidate, walk_forward  # noqa: E402
from robotrader.strategies import ExternalGapFade, overnight_gaps  # noqa: E402

OUT = ROOT / "reports" / "gap_research"


def yearly(trades: pd.DataFrame) -> dict:
    t = trades.assign(y=pd.to_datetime(trades.exit_time).dt.year)
    return {int(y): f"R$ {g.net.sum():+.0f} ({len(g)} ops, PF {g.net[g.net>0].sum()/max(-g.net[g.net<=0].sum(),1):.2f})"
            for y, g in t.groupby("y")}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    win = load_csv(ROOT / "data" / "WIN_M5.csv")
    wsp = load_csv(ROOT / "data" / "external" / "WSP_N_M5.csv")
    gaps = overnight_gaps(wsp)
    base_inst = replace(cfg.instruments["WIN"], stop_points=0, target_points=0,
                        stop_atr=0, target_atr=0)
    print(f"Gaps do S&P disponíveis: {len(gaps)} pregões ({gaps.index.min()} a {gaps.index.max()})")

    # 1. regra base
    print("\n=== 1. Regra base: fade, entra 9:05, sai 13:00, só gaps acima da mediana, sem stop ===")
    strat = ExternalGapFade(gaps=gaps, exit_at="13:00", threshold_q=0.5, direction=-1)
    res = run_backtest(win, base_inst, cfg, strat)
    m = res.metrics
    print({k: m[k] for k in ("operacoes", "resultado_liquido", "fator_lucro", "taxa_acerto_%",
                             "drawdown_maximo", "sharpe_diario_anualizado", "pior_dia")})
    print("por ano:", yearly(res.trades))
    t = res.trades
    print("comprado (S&P caiu):", f"R$ {t[t.side>0].net.sum():+.0f} ({(t.side>0).sum()} ops)",
          "| vendido (S&P subiu):", f"R$ {t[t.side<0].net.sum():+.0f} ({(t.side<0).sum()} ops)")
    res.trades.to_csv(OUT / "base_trades.csv", index=False)

    # 2. robustez
    print("\n=== 2. Robustez (período inteiro): resultado líquido R$ por combinação ===")
    rows = []
    for exit_at in ["10:30", "11:00", "12:00", "13:00", "14:00", "15:30", "17:00"]:
        for q in [0.0, 0.5, 0.75]:
            for stop in [0.0, 4.0, 8.0]:
                inst = replace(base_inst, stop_atr=stop)
                r = run_backtest(win, inst, cfg, ExternalGapFade(
                    gaps=gaps, exit_at=exit_at, threshold_q=q, direction=-1))
                rows.append({"saida": exit_at, "filtro_q": q, "stop_atr": stop,
                             "liquido": r.metrics.get("resultado_liquido", 0),
                             "ops": r.metrics.get("operacoes", 0),
                             "pf": r.metrics.get("fator_lucro")})
    grid = pd.DataFrame(rows)
    grid.to_csv(OUT / "robustez.csv", index=False)
    pivot = grid[grid.stop_atr == 0].pivot(index="saida", columns="filtro_q", values="liquido")
    print("sem stop (linhas = horário de saída, colunas = filtro):\n", pivot.round(0).to_string())
    print(f"combinações lucrativas: {(grid.liquido > 0).mean()*100:.0f}% de {len(grid)}")
    print("efeito do stop (média do líquido):", grid.groupby("stop_atr").liquido.mean().round(0).to_dict())

    # 3. walk-forward
    print("\n=== 3. Walk-forward (treino 250 / teste 21 pregões) ===")
    cand = Candidate("S&P noturno (fade)", "external_gap", {
        "exit_at": ["11:00", "12:00", "13:00", "14:00", "15:30"],
        "threshold_q": [0.0, 0.5, 0.75],
        "inst.stop_atr": [0.0, 4.0, 8.0],
    }, fixed={"gaps": gaps, "direction": -1})
    wf = walk_forward(win, base_inst, cfg, cand, train_days=250, test_days=21, min_trades=20)
    mm = wf.oos_metrics
    print({k: mm.get(k) for k in ("operacoes", "resultado_liquido", "fator_lucro", "taxa_acerto_%",
                                  "drawdown_maximo")}, "| t diário:", round(wf.t_stat(), 2))
    if not wf.oos_trades.empty:
        print("OOS por ano:", yearly(wf.oos_trades))
    print("parâmetros escolhidos:", wf.folds["params"].astype(str).value_counts().head(4).to_dict())
    wf.folds.to_csv(OUT / "wf_folds.csv", index=False)
    wf.oos_trades.to_csv(OUT / "wf_oos_trades.csv", index=False)

    # 4. permutação (regra base, cálculo vetorizado: entrada 9:05 aberta, saída 13:00 fechamento)
    print("\n=== 4. Teste de permutação da regra base ===")
    day = pd.Index(win.index.date)
    entry = win[win.index.time == pd.Timestamp("09:05").time()]["open"]
    entry.index = entry.index.date
    exit_ = win[win.index.time == pd.Timestamp("13:00").time()]["close"]
    exit_.index = exit_.index.date
    fwd = (exit_ - entry).dropna()
    common = fwd.index.intersection(gaps.index)
    fwd, g = fwd.loc[common].to_numpy(), gaps.loc[common].to_numpy()
    friction = 15.0  # pontos por operação (custos + slippage)

    def pnl(gv: np.ndarray) -> float:
        thr = pd.Series(np.abs(gv)).shift(1).rolling(120, min_periods=60).quantile(0.5).to_numpy()
        trade = np.abs(gv) > thr
        return float(np.sum((-np.sign(gv) * fwd - friction)[trade]) * 0.2)  # R$ por contrato

    real = pnl(g)
    rng = np.random.default_rng(0)
    perms = np.array([pnl(rng.permutation(g)) for _ in range(2000)])
    print(f"resultado real (aprox.): R$ {real:+.0f} | permutações: média R$ {perms.mean():+.0f}, "
          f"95º percentil R$ {np.percentile(perms, 95):+.0f} | p-valor = {(perms >= real).mean():.3f}")


if __name__ == "__main__":
    main()
