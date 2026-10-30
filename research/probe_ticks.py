"""Verifica a disponibilidade de ticks (negócios) no MT5."""
from datetime import datetime, timedelta
import MetaTrader5 as mt5
import pandas as pd

assert mt5.initialize(), mt5.last_error()
F = {"BUY": mt5.TICK_FLAG_BUY, "SELL": mt5.TICK_FLAG_SELL, "LAST": mt5.TICK_FLAG_LAST, "VOLUME": mt5.TICK_FLAG_VOLUME}
y = datetime(2026, 10, 28, 10, 0)
for sym in ["WIN$N", "WINZ26", "WINV26", "WDO$N", "WDOX26", "WDOV26"]:
    mt5.symbol_select(sym, True)
    t = mt5.copy_ticks_range(sym, y, y + timedelta(minutes=5), mt5.COPY_TICKS_TRADE)
    if t is None or len(t) == 0:
        print(f"{sym}: sem ticks ({mt5.last_error()})"); continue
    df = pd.DataFrame(t)
    fl = {k: int(((df["flags"] & v) > 0).sum()) for k, v in F.items()}
    print(f"{sym}: {len(df)} negócios em 5 min | flags {fl} | volume_real soma {df.volume_real.sum():.0f} | colunas {list(df.columns)}")
    print("   amostra:", df[["time_msc", "last", "volume_real", "flags"]].head(3).to_dict("records"))

# profundidade: até onde há ticks?
for sym in ["WIN$N", "WDO$N"]:
    for months in [1, 3, 6, 12, 24, 36]:
        d = datetime(2026, 10, 28, 10, 0) - timedelta(days=30 * months)
        while d.weekday() >= 5: d -= timedelta(days=1)
        t = mt5.copy_ticks_range(sym, d, d + timedelta(minutes=5), mt5.COPY_TICKS_TRADE)
        n = 0 if t is None else len(t)
        print(f"{sym} {d:%d/%m/%Y}: {n} negócios")
mt5.shutdown()
