"""Baixa barras diárias (D1) para data/daily/. Uso: download_daily.py SIMBOLO [...]"""
import sys
from pathlib import Path
import MetaTrader5 as mt5
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
out = Path(__file__).resolve().parent.parent / "data" / "daily"; out.mkdir(parents=True, exist_ok=True)
assert mt5.initialize()
for sym in sys.argv[1:]:
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 1, 8000)  # pos=1: ignora o dia em andamento
    df = pd.DataFrame(r); df.index = pd.to_datetime(df["time"], unit="s").dt.normalize(); df.index.name = "date"
    df = df[["open", "high", "low", "close", "real_volume"]].rename(columns={"real_volume": "volume"})
    df.to_csv(out / f"{sym.replace('$', '_')}_D1.csv")
    print(f"{sym}: {len(df)} dias, {df.index.min():%d/%m/%Y} a {df.index.max():%d/%m/%Y}")
mt5.shutdown()
