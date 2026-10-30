"""Mapeia em quais dias existem ticks de negócios, por símbolo."""
import sys
from datetime import date, datetime, timedelta
import MetaTrader5 as mt5
sys.stdout.reconfigure(encoding="utf-8")
assert mt5.initialize(), mt5.last_error()
syms = sorted(s.name for s in mt5.symbols_get("WIN*") + mt5.symbols_get("WDO*"))
print("símbolos:", syms)
days = [date(2026, 10, 28) - timedelta(days=i) for i in range(0, 400)]
days = [d for d in days if d.weekday() < 5]
for sym in sys.argv[1:]:
    mt5.symbol_select(sym, True)
    have = []
    for d in days:
        t0 = datetime(d.year, d.month, d.day, 10, 30)
        t = mt5.copy_ticks_range(sym, t0, t0 + timedelta(minutes=1), mt5.COPY_TICKS_TRADE)
        if t is not None and len(t):
            have.append(d)
    if have:
        print(f"{sym}: {len(have)} dias com ticks, de {min(have)} a {max(have)}; "
              f"lacunas recentes: {sorted(set(days[:60]) - set(have))[:8] if len(have) < 60 else 'nenhuma nos últimos 60 dias úteis'}")
    else:
        print(f"{sym}: nenhum tick")
mt5.shutdown()
