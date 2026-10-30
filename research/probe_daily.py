"""Profundidade do histórico diário (D1) de um símbolo. Uso: probe_daily.py SIMBOLO"""
import sys
import MetaTrader5 as mt5
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
sym = sys.argv[1]
assert mt5.initialize()
mt5.symbol_select(sym, True)
r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 8000)
if r is None or len(r) == 0:
    print(f"{sym}: sem D1 ({mt5.last_error()})")
else:
    d = pd.to_datetime(pd.DataFrame(r)["time"], unit="s")
    print(f"{sym}: {len(r)} barras diárias de {d.min():%d/%m/%Y} a {d.max():%d/%m/%Y}")
mt5.shutdown()
