"""Exporta para o dashboard a configuração do robô e os resultados do simulado.

Saída (reports/dashboard/): paper_config.csv (uma linha por entrada do settings.toml) e
paper_trades.csv (operações de logs/paper/paper_trades.csv, vazio se ainda não houver).
Depois, envie os dois arquivos ao dashboard para atualizá-lo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from robotrader.config import load_config  # noqa: E402

OUT = ROOT / "reports" / "dashboard"
DESC = {"external_gap": "S&P noturno: opera contra o gap do micro S&P quando maior que a mediana de 120 pregões",
        "prev_day_reversal": "Reversão do dia anterior: opera contra o movimento de ontem se passou da mediana de 120 pregões",
        "opening_range_breakout": "Rompimento dos primeiros 30 min"}
COLS = ["symbol", "grupo", "lado", "entry_time", "entry_price", "exit_time", "exit_price", "points", "net", "reason"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    group_of = {m: g.name for g in cfg.portfolios for m in g.members}
    rows = []
    for key, i in cfg.instruments.items():
        p, name = i.strategy_params, i.strategy_name
        rows.append({"entrada": key, "ativa": "sim" if i.enabled else "não", "grupo": group_of.get(key, "—"),
                     "estrategia": DESC.get(name, name), "saida": str(p.get("exit_at", "")),
                     "stop_atr": getattr(i, "stop_atr", ""), "trailing_atr": getattr(i, "trail_atr", 0) or "",
                     "custo_por_lado": getattr(i, "cost_per_contract", "")})
    pd.DataFrame(rows).to_csv(OUT / "paper_config.csv", index=False)

    src = ROOT / cfg.log_dir / "paper" / "paper_trades.csv"
    if src.exists():
        t = pd.read_csv(src)
        t["grupo"] = t.symbol.map(group_of).fillna("—")
        t["lado"] = t.side.map(lambda s: "Compra" if s > 0 else "Venda")
        t = t[COLS]
    else:
        t = pd.DataFrame(columns=COLS)
    t.to_csv(OUT / "paper_trades.csv", index=False)
    print(len(rows), "entradas |", len(t), "operações simuladas")


if __name__ == "__main__":
    main()
