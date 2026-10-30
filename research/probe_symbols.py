"""Lista símbolos do MT5 que podem servir como mercados externos."""
import re, sys
import MetaTrader5 as mt5
sys.stdout.reconfigure(encoding="utf-8")
assert mt5.initialize(), mt5.last_error()
syms = mt5.symbols_get()
print("total de símbolos:", len(syms))
paths = {}
for s in syms:
    top = s.path.split("\\")[0] if s.path else "?"
    paths[top] = paths.get(top, 0) + 1
print("grupos:", dict(sorted(paths.items(), key=lambda x: -x[1])[:25]))
pat = re.compile(r"^(WSP|ISP|SP|ES|US|DX|DXY|DOL|IND|BIT|T10|DI1|DAP|CCM|BGI|ICF|EUR|GBP|JPY|NAS|NDX|SPX|VIX|IVV|SPY|QQQ|EWZ|BOVA|DJ|YM|NQ|GOLD|XAU|OIL|BRENT|WTI|CL|BRL)", re.I)
hits = [s for s in syms if pat.match(s.name) and not re.search(r"\d{6}|C0|P0", s.name)]
for s in hits[:120]:
    print(f"{s.name:<14} | {s.path:<40} | {s.description[:50]}")
mt5.shutdown()
