"""Baixa barras M5 de mercados relacionados listados na B3 para data/external/."""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.stdout.reconfigure(encoding="utf-8")
from robotrader.broker.mt5 import MT5Connector

SYMBOLS = sys.argv[1:] or ["WSP$N", "ISP$N", "DI1$N", "T10$N", "DOL$N", "EUR$N", "IVVB11", "BOVA11"]
out = ROOT / "data" / "external"; out.mkdir(parents=True, exist_ok=True)
c = MT5Connector(magic=0); c.connect(trading=False)
end = datetime.combine(date.today(), datetime.min.time()); start = end - timedelta(days=2600)
try:
    for sym in SYMBOLS:
        try:
            b = c.history(sym, "M5", start, end)
        except RuntimeError as exc:
            print(f"{sym}: {exc}"); continue
        name = sym.replace("$", "_")
        b.to_csv(out / f"{name}_M5.csv")
        days = b.index.normalize().nunique()
        per_day = b.groupby(b.index.date).size().median()
        print(f"{sym:<8} {len(b):>7} barras | {days} pregões | {b.index[0]:%d/%m/%Y} a {b.index[-1]:%d/%m/%Y} "
              f"| {b.index.time.min()}-{b.index.time.max()} | barras/dia {per_day:.0f} | vol/dia {b.volume.sum()/days:,.0f}")
finally:
    c.shutdown()
