"""O fluxo de agressão antecipa o preço?

Para cada barra calcula o desequilíbrio de agressão  imb = delta / volume  (entre -1 e 1)
e mede a relação com o retorno das barras SEGUINTES, dentro do mesmo pregão.

- IC (information coefficient): correlação de Spearman entre sinal e retorno futuro,
  calculada dia a dia; t = média / erro padrão dos ICs diários.
- Quintis: retorno futuro médio (em pontos) por faixa do sinal, comparado com o custo
  de uma operação (custos + slippage de 1 tick em cada ponta).

Uso: .venv\\Scripts\\python research\\flow_analysis.py [--minutes 5]
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from robotrader.config import load_config  # noqa: E402
from robotrader.contracts import get_spec  # noqa: E402
from robotrader.flow import load_flow  # noqa: E402

EXCLUDE = {pd.Timestamp("2026-10-05").date(), pd.Timestamp("2026-10-26").date()}


def daily_ic(signal: pd.Series, fwd: pd.Series, day: pd.Index) -> pd.Series:
    df = pd.DataFrame({"s": signal, "f": fwd, "d": day}).dropna()
    return df.groupby("d").apply(
        lambda g: g["s"].rank().corr(g["f"].rank()) if len(g) > 10 else np.nan
    ).dropna()


def describe_ic(ic: pd.Series) -> str:
    t = ic.mean() / ic.std() * math.sqrt(len(ic)) if len(ic) > 1 else float("nan")
    pos = 100 * (ic > 0).mean()
    return f"IC médio {ic.mean():+.3f} | t {t:+.1f} | dias com IC>0 {pos:.0f}% | n={len(ic)}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=int, default=5)
    args = p.parse_args()
    cfg = load_config()

    for root in ["WIN", "WDO"]:
        path = ROOT / "data" / "flow" / f"{root}_M1_flow.csv"
        if not path.exists():
            print(f"{root}: sem arquivo de fluxo"); continue
        b = load_flow(path, args.minutes)
        b = b[(b.index.time >= pd.Timestamp("09:05").time()) &
              (b.index.time <= pd.Timestamp("17:45").time())]
        b = b[~pd.Index(b.index.date).isin(EXCLUDE)]
        day = pd.Index(b.index.date)
        spec, inst = get_spec(root), cfg.instruments[root]
        friction_pts = (2 * inst.cost_per_contract / spec.point_value) + 2 * spec.tick_size

        imb = b["delta"] / b["volume"].replace(0, np.nan)
        ret = b["close"] - b["open"]
        print(f"\n================ {root} | {args.minutes} min | {day.nunique()} pregões "
              f"({day.min()} a {day.max()}) ================")
        print(f"agressão identificada: {100 * (b.buy_vol + b.sell_vol).sum() / b.volume.sum():.1f}% "
              f"do volume | custo por operação ≈ {friction_pts:.1f} pontos")
        print("Mesma barra (não operável, só confere os dados):",
              describe_ic(daily_ic(imb, ret, day)))

        cum = b["delta"].groupby(day).cumsum()
        roll = {n: b["delta"].groupby(day).transform(lambda s: s.rolling(n, min_periods=n).sum())
                / b["volume"].groupby(day).transform(lambda s: s.rolling(n, min_periods=n).sum())
                for n in (3, 6, 12)}
        signals = {
            "desequilíbrio da barra": imb,
            "desequilíbrio 3 barras": roll[3],
            "desequilíbrio 6 barras": roll[6],
            "desequilíbrio 12 barras": roll[12],
            "delta acumulado do dia (z)": (cum - cum.groupby(day).transform(
                lambda s: s.expanding().mean())) / cum.groupby(day).transform(
                lambda s: s.expanding().std()),
            # absorção: muito fluxo e pouco preço -> resíduo do retorno explicado pelo fluxo
            "absorção (retorno - beta x fluxo)": ret - imb * (ret.cov(imb) / imb.var()),
        }
        for h in (1, 3, 6):
            fwd = b["close"].groupby(day).shift(-h) - b["close"]
            print(f"\n-- Retorno das próximas {h} barras ({h * args.minutes} min) --")
            for name, s in signals.items():
                print(f"  {name:<36} {describe_ic(daily_ic(s, fwd, day))}")
            # quintis do sinal mais simples
            q = pd.qcut(imb, 5, labels=["Q1 venda forte", "Q2", "Q3", "Q4", "Q5 compra forte"])
            m = fwd.groupby(q, observed=True).mean()
            print("  pontos médios por quintil do desequilíbrio da barra:",
                  {k: round(v, 2) for k, v in m.items()},
                  f"| spread Q5-Q1 = {m.iloc[-1] - m.iloc[0]:+.2f} pts vs custo {friction_pts:.1f}")

        # estabilidade por mês do sinal mais promissor (6 barras, horizonte 3)
        fwd3 = b["close"].groupby(day).shift(-3) - b["close"]
        ic = daily_ic(roll[6], fwd3, day)
        ic.index = pd.to_datetime(ic.index)
        print("\nIC mensal (desequilíbrio 6 barras -> próximos 15 min):",
              {f"{k:%m/%y}": round(v, 3) for k, v in ic.groupby(ic.index.to_period("M")).mean().items()})


if __name__ == "__main__":
    main()
