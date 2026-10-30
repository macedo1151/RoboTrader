"""Roda o walk-forward de todas as famílias de estratégia em WIN e WDO.

Uso:
    .venv\\Scripts\\python research\\run_research.py
    .venv\\Scripts\\python research\\run_research.py --instrument WIN --timeframe M5

Saída: tabela resumo no console e detalhes em reports/research/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from robotrader.config import load_config  # noqa: E402
from robotrader.data import load_csv  # noqa: E402
from robotrader.research import Candidate, walk_forward  # noqa: E402

HOURS = ["11:00", "13:00", "17:00"]

CANDIDATES = [
    Candidate("Cruzamento de médias", "sma_cross", {
        "fast": [5, 9, 15],
        "slow": [21, 40, 80],
        "inst.stop_atr": [1.0, 2.0, 3.0],
        "inst.target_atr": [0.0, 2.0, 4.0],
        "entry_until": ["11:00", "17:00"],
    }),
    Candidate("Rompimento de canal (Donchian)", "donchian_breakout", {
        "n": [12, 24, 48],
        "exit_n": [0, 6, 12],
        "inst.stop_atr": [1.5, 3.0],
        "inst.target_atr": [0.0, 3.0, 6.0],
        "entry_until": HOURS,
    }),
    Candidate("Faixa de abertura: rompimento", "opening_range_breakout", {
        "range_minutes": [15, 30, 60],
        "inst.stop_atr": [1.0, 2.0, 3.0],
        "inst.target_atr": [0.0, 1.0, 2.0, 4.0],
        "entry_until": HOURS,
    }, fixed={"mode": "breakout"}),
    Candidate("Faixa de abertura: contra (fade)", "opening_range_breakout", {
        "range_minutes": [15, 30, 60],
        "inst.stop_atr": [1.0, 2.0, 3.0],
        "inst.target_atr": [0.0, 1.0, 2.0, 4.0],
        "entry_until": HOURS,
    }, fixed={"mode": "fade"}),
    Candidate("Reversão à média (Bollinger)", "mean_reversion", {
        "n": [20, 50],
        "k": [1.5, 2.0, 2.5, 3.0],
        "inst.stop_atr": [1.5, 3.0],
        "inst.target_atr": [0.0],
        "entry_until": ["12:00", "17:00"],
    }, fixed={"anchor": "sma"}),
    Candidate("Reversão à VWAP", "mean_reversion", {
        "n": [14, 50],
        "k": [1.5, 2.0, 3.0, 4.0],
        "inst.stop_atr": [1.5, 3.0],
        "inst.target_atr": [0.0],
        "entry_until": ["12:00", "17:00"],
    }, fixed={"anchor": "vwap"}),
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--instrument", default="all")
    p.add_argument("--timeframe", default="M5")
    p.add_argument("--train-days", type=int, default=120)
    p.add_argument("--test-days", type=int, default=21)
    p.add_argument("--out", default=str(ROOT / "reports" / "research"))
    args = p.parse_args()

    cfg = load_config()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    roots = ["WIN", "WDO"] if args.instrument == "all" else [args.instrument.upper()]

    summary = []
    for root in roots:
        bars = load_csv(ROOT / "data" / f"{root}_{args.timeframe}.csv")
        inst = cfg.instruments[root]
        for cand in CANDIDATES:
            t0 = time.time()
            res = walk_forward(bars, inst, cfg, cand,
                               train_days=args.train_days, test_days=args.test_days)
            m = res.oos_metrics
            full = res.full_period
            row = {
                "ativo": root,
                "estrategia": cand.label,
                "combinacoes": len(full),
                "oos_liquido": m.get("resultado_liquido", 0.0),
                "oos_ops": m.get("operacoes", 0),
                "oos_fator_lucro": m.get("fator_lucro"),
                "oos_acerto_%": m.get("taxa_acerto_%"),
                "oos_drawdown": m.get("drawdown_maximo"),
                "oos_t": round(res.t_stat(), 2),
                "melhor_no_ano_todo": float(full["liquido"].iloc[0]),
                "%_combinacoes_lucrativas": round(100 * float((full["liquido"] > 0).mean()), 1),
            }
            summary.append(row)
            slug = f"{root}_{cand.strategy}_{cand.fixed.get('mode', cand.fixed.get('anchor', ''))}"
            res.folds.to_csv(out / f"{slug}_folds.csv", index=False)
            res.oos_trades.to_csv(out / f"{slug}_oos_trades.csv", index=False)
            full.head(30).to_csv(out / f"{slug}_top30.csv", index=False)
            print(f"{root} | {cand.label:<34} | OOS R$ {row['oos_liquido']:>9.2f} "
                  f"| {row['oos_ops']:>4} ops | PF {row['oos_fator_lucro']} | t {row['oos_t']} "
                  f"| {row['%_combinacoes_lucrativas']}% combos lucrativas | {time.time()-t0:.0f}s",
                  flush=True)

    with open(out / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    main()
