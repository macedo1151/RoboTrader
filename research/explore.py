"""Análise exploratória do comportamento intradiário de WIN e WDO."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from robotrader.data import load_csv

pd.set_option("display.width", 200)

def variance_ratio(r, k):
    r = r.dropna()
    return r.rolling(k).sum().var() / (k * r.var())

for root in ["WIN", "WDO"]:
    d = load_csv(f"data/{root}_M5.csv")
    # exclui os dias de eleição para não distorcer
    d = d[~pd.Index(d.index.date).isin([pd.Timestamp("2026-10-05").date(), pd.Timestamp("2026-10-26").date()])]
    day = pd.Index(d.index.date)
    r = np.log(d.close).groupby(day).diff()  # retornos intradiários (sem gap)
    print(f"\n================ {root} ================")
    # 1) autocorrelação / variance ratio: >1 tendência, <1 reversão
    print("Variance ratio (>1 tendência, <1 reversão):",
          {k: round(variance_ratio(r, k), 3) for k in (2, 3, 6, 12, 24)})
    print("Autocorrelação lag1..3:", [round(r.autocorr(l), 3) for l in (1, 2, 3)])
    # 2) volatilidade por hora
    vol = (d.high - d.low).groupby(d.index.hour).mean().round(1)
    print("Amplitude média da barra por hora:", vol.to_dict())
    # 3) abertura prevê o resto do dia?
    g = d.groupby(day)
    o = g.open.first(); c = g.close.last()
    def at(t):
        return d[d.index.time == pd.Timestamp(t).time()].groupby(pd.Index(d[d.index.time == pd.Timestamp(t).time()].index.date)).close.first()
    for t in ["09:30", "10:00", "11:00"]:
        p = at(t).reindex(o.index)
        first = np.sign(p - o); rest = np.sign(c - p)
        ok = first.notna() & rest.notna() & (first != 0)
        print(f"Direção 9:00→{t} se repete até o fechamento em {100*(first[ok]==rest[ok]).mean():.1f}% dos dias")
    # 4) gap de abertura: fecha ou continua?
    prev_c = c.shift(); gap = o - prev_c
    filled = []
    for dt, gp in gap.dropna().items():
        x = d[day == dt]
        filled.append((x.low.min() <= prev_c[dt]) if gp > 0 else (x.high.max() >= prev_c[dt]))
    print(f"Gaps fechados no mesmo dia: {100*np.mean(filled):.1f}%")
    # 5) distância até VWAP e retorno futuro (reversão à VWAP?)
    tp = (d.high + d.low + d.close) / 3
    vwap = (tp * d.volume).groupby(day).cumsum() / d.volume.groupby(day).cumsum()
    atr = (d.high - d.low).rolling(14).mean()
    z = (d.close - vwap) / atr
    fwd = d.close.groupby(day).shift(-6) - d.close  # 30 min à frente
    bins = pd.cut(z, [-np.inf, -3, -2, -1, 1, 2, 3, np.inf])
    print("Retorno médio 30min à frente (pontos) por distância da VWAP em ATRs:")
    print(fwd.groupby(bins, observed=True).agg(["mean", "count"]).round(1).T.to_string())
    # 6) momentum de 1h: retorno das últimas 12 barras vs próximas 12
    past = d.close - d.close.groupby(day).shift(12)
    fut = d.close.groupby(day).shift(-12) - d.close
    q = pd.qcut(past, 5, labels=False, duplicates="drop")
    print("Retorno médio 1h à frente por quintil do retorno da última 1h:", fut.groupby(q).mean().round(1).to_dict())
