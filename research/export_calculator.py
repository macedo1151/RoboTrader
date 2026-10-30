"""Exporta o resultado diário por contrato-base de cada carteira configurada no robô,
para a calculadora de capital (reports/calculator/daily_pnl.json).

Cada valor é o resultado líquido (custos e 1 tick de slippage incluídos) de um pregão
operando 1 contrato-base por estratégia. No grupo com posição líquida, os dias em que as
estratégias concordam já contam 2 contratos de cada — o valor escala linearmente com o
número de contratos escolhido na calculadora.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from robotrader.backtest import run_backtest  # noqa: E402
from robotrader.config import load_config  # noqa: E402
from robotrader.data import load_csv  # noqa: E402
from robotrader.external import load_external  # noqa: E402
from robotrader.portfolio import run_portfolio  # noqa: E402


def main() -> None:
    cfg = load_config()
    win = load_csv(ROOT / "data" / "WIN_M5.csv")
    ext = load_external(["WSP$N"])
    days = pd.to_datetime(win.index.normalize().unique())

    series, qty, meta = {}, {}, {}

    def daily(trades: pd.DataFrame) -> pd.Series:
        d = trades.groupby(pd.to_datetime(trades.exit_time).dt.normalize()).net.sum()
        return d.reindex(days, fill_value=0.0)

    for g in cfg.portfolios:
        res = run_portfolio(win, cfg, g, external=ext)
        t = pd.concat([r.trades for r in res.values()])
        series[g.name] = daily(t)
        qty[g.name] = t.groupby(t.entry_time.dt.normalize()).qty.sum().reindex(days, fill_value=0)
        meta[g.name] = {"members": g.members, "ops": int(len(t)),
                        "max_contracts_per_base": int(t.groupby(t.entry_time.dt.normalize()).qty.sum().max())}
    sp = run_backtest(win, cfg.instruments["WIN"], cfg, external=ext).trades
    series["sp"] = daily(sp)
    qty["sp"] = sp.groupby(sp.entry_time.dt.normalize()).qty.sum().reindex(days, fill_value=0)
    meta["sp"] = {"members": ["WIN"], "ops": int(len(sp)), "max_contracts_per_base": 1}

    out = ROOT / "reports" / "calculator"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "dates": [d.strftime("%Y-%m-%d") for d in days],
        "series": {k: [round(float(v), 2) for v in s] for k, s in series.items()},
        "qty": {k: [int(v) for v in q] for k, q in qty.items()},  # contratos usados no dia por contrato-base
        "meta": meta,
    }
    data = json.dumps(payload, ensure_ascii=False)
    (out / "daily_pnl.json").write_text(data, encoding="utf-8")
    # monta a página da calculadora com os dados embutidos
    template = (out / "calculadora_template.html").read_text(encoding="utf-8")
    (out / "calculadora.html").write_text(template.replace("__DATA__", data), encoding="utf-8")
    print(f"calculadora atualizada: {out / 'calculadora.html'}")
    for k, s in series.items():
        eq = s.cumsum()
        print(f"{k:<10} total R$ {s.sum():+,.0f} | pior dia R$ {s.min():,.0f} | pior queda R$ {(eq - eq.cummax().clip(lower=0)).min():,.0f} "
              f"| primeiro pregão operado {s[s != 0].index[0]:%d/%m/%Y} | {meta[k]}")


if __name__ == "__main__":
    main()
