"""Painel local para ligar e desligar o robô (modo simulado) e acompanhar o que ele faz.

    python -m robotrader.painel            (abre http://localhost:8765)

Mostra posições abertas, o que o robô está fazendo (lido do log do dia), resultado do
simulado, estratégias em teste e onde tudo fica salvo. Só aceita conexões do próprio
computador. Ligar inicia `python -m robotrader live`; desligar cria o arquivo STOP (o robô
zera as posições), espera alguns segundos e encerra o processo. Recusa ligar se `mode` não
for "paper" (conta real exige --confirm e conferência manual).
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import load_config
from .contracts import get_spec
from .live import KILL_SWITCH, PROJECT_ROOT

PORT = 8765
PID_FILE = PROJECT_ROOT / "logs" / "robo.pid"
OUT_FILE = PROJECT_ROOT / "logs" / "painel_robo.out"
GRACE_SECONDS = 15   # tempo para o robô ver o STOP (verifica a cada ~5 s) e zerar
STALE_SECONDS = 420  # o robô só loga ao fechar cada barra de 5 min; mais que isso sem linha = aviso
LOCK = threading.Lock()

DESC = {"external_gap": "S&P noturno: opera contra o gap do micro S&P quando maior que a mediana de 120 pregões",
        "prev_day_reversal": "Reversão do dia anterior: opera contra o movimento de ontem se passou da mediana de 120 pregões",
        "opening_range_breakout": "Rompimento dos primeiros 30 minutos"}
BAR_RE = re.compile(r"^(\S+ \S+) \w+ robotrader: (\w+): barra (\d\d:\d\d) fechou ([\d.]+) \| posição: (.*?) "
                    r"\| dia: R\$ (-?[\d.]+) em (\d+) ops(?: \| bloqueado: (.*))?$")
LINE_RE = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d),\d+ (\w+) robotrader[\w.]*: (.*)$")

PAGE = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RoboTrader — painel</title>
<style>
:root{--bg:#f6f6f4;--card:#fff;--fg:#1c1c1a;--mut:#74746e;--line:#e6e6e2;--pos:#1b6b2c;--neg:#b3261e;--code:#f0f0ee}
@media(prefers-color-scheme:dark){:root{--bg:#1b1b19;--card:#262624;--fg:#ecece8;--mut:#9a9a93;--line:#363633;--pos:#6fd08a;--neg:#ff8a80;--code:#1f1f1d}}
body{font:14px system-ui,sans-serif;margin:0;padding:20px;background:var(--bg);color:var(--fg)}
.card{background:var(--card);border-radius:10px;padding:16px 18px;max-width:1000px;margin:0 auto 14px}
h1{font-size:20px;margin:0}h2{font-size:15px;margin:0 0 8px}.sub,.note{color:var(--mut);margin:4px 0}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.st{display:inline-block;padding:3px 12px;border-radius:99px;font-weight:600;background:#eee;color:#555}
.st.on{background:#dff3e3;color:#1b6b2c}.st.warn{background:#fdecc8;color:#8a5a00}
button{font:inherit;padding:9px 20px;border:0;border-radius:8px;cursor:pointer;margin-right:8px;font-weight:600}
#on{background:#1b6b2c;color:#fff}#off{background:#b3261e;color:#fff}button:disabled{opacity:.35;cursor:default}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:500;font-size:12.5px}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.pos{color:var(--pos)}.neg{color:var(--neg)}
.tiles{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0 10px}.tile{background:var(--code);border-radius:8px;padding:8px 14px;min-width:120px}
.tile b{display:block;font-size:19px}.tile span{color:var(--mut);font-size:12px}
pre{background:var(--code);padding:10px;border-radius:8px;overflow:auto;max-height:260px;font-size:12px;margin:8px 0 0;white-space:pre-wrap;word-break:break-word}
.ev{font-size:13px;margin:3px 0}.ev i{color:var(--mut);font-style:normal;margin-right:8px}
details summary{cursor:pointer;color:var(--mut)}
</style>
<div class="card"><div class="top"><div><h1>RoboTrader</h1><p class="sub" id="sub">—</p></div>
<div><span class="st" id="st">—</span> <span class="sub" id="info"></span></div></div>
<p><button id="on">Ligar robô</button><button id="off">Desligar robô</button><span class="sub" id="msg"></span></p>
<p class="note" id="beat"></p></div>

<div class="card"><h2>Posições abertas agora</h2><div id="open"></div></div>
<div class="card"><h2>O que está fazendo</h2><div id="now"></div><div id="events" style="margin-top:8px"></div></div>
<div class="card"><h2>Resultado do simulado</h2><div class="tiles" id="tiles"></div><div id="bygroup"></div>
<h2 style="margin-top:14px">Operações encerradas</h2><div id="trades"></div></div>
<div class="card"><h2>Estratégias em teste</h2><div id="strats"></div></div>
<div class="card"><h2>Onde fica salvo</h2><div id="files"></div>
<details><summary>Log bruto de hoje</summary><pre id="log">—</pre></details></div>
<script>
const $=id=>document.getElementById(id);
const brl=v=>(v<0?'−':'+')+'R$ '+Math.abs(v).toLocaleString('pt-BR',{minimumFractionDigits:0,maximumFractionDigits:0});
const cls=v=>v<0?'neg':v>0?'pos':'';
function el(tag,text,c){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(c)e.className=c;return e}
function table(box,heads,rows,empty){box.replaceChildren();
 if(!rows.length){if(empty)box.appendChild(el('p',empty,'note'));return}
 const t=el('table'),th=el('tr');heads.forEach(h=>th.appendChild(el('th',h[0],h[1]||'')));t.appendChild(th);
 rows.forEach(r=>{const tr=el('tr');r.forEach((c,i)=>{const v=(c&&typeof c==='object')?c:{t:c};
  tr.appendChild(el('td',v.t,((heads[i][1]||'')+' '+(v.c||'')).trim()))});t.appendChild(tr)});box.appendChild(t)}
async function act(a){$('msg').textContent=' Aguarde…';
 const r=await fetch('/'+a,{method:'POST'});const j=await r.json();$('msg').textContent=' '+(j.msg||'');refresh()}
async function refresh(){let j;try{j=await (await fetch('/status')).json()}catch(e){$('st').textContent='Painel sem resposta';return}
 $('sub').textContent='Modo '+j.mode+(j.mode==='paper'?' — simulado, nenhuma ordem é enviada':'');
 const s=$('st');s.textContent=j.running?'Rodando':'Parado';s.className='st'+(j.running?(j.stale?' warn':' on'):'');
 $('info').textContent=j.running?'PID '+j.pid:'';$('on').disabled=j.running||j.mode!=='paper';$('off').disabled=!j.running;
 $('beat').textContent=j.last_activity?('Última atividade do robô: '+j.last_activity+(j.running&&j.stale?' — sem novidade há mais de 7 minutos, confira o MetaTrader 5.':'')):'Sem atividade registrada hoje.';
 table($('open'),[['Entrada'],['Lado'],['Contratos','n'],['Entrada em'],['Preço de entrada','n'],['Preço atual','n'],['Stop','n'],['Trailing','n'],['Resultado agora','n']],
  j.open.map(p=>[p.symbol,p.lado,p.qty,p.entry_time,p.entry_price,p.price==null?'—':p.price,p.stop==null?'—':p.stop,p.trail==null?'—':p.trail,{t:p.floating==null?'—':brl(p.floating),c:cls(p.floating||0)}]),
  'Nenhuma posição aberta.');
 table($('now'),[['Entrada'],['Última barra'],['Preço','n'],['Posição'],['Dia','n'],['Operações no dia','n']],
  j.now.map(r=>[r.symbol,r.bar,r.price,r.pos,{t:brl(r.day),c:cls(r.day)},r.ops+(r.halt?' · '+r.halt:'')]),'Aguardando a primeira barra.');
 const ev=$('events');ev.replaceChildren();j.events.forEach(e=>{const d=el('div',undefined,'ev');d.appendChild(el('i',e.t));d.appendChild(document.createTextNode(e.msg));ev.appendChild(d)});
 const R=j.results;const tl=$('tiles');tl.replaceChildren();
 [['Total',brl(R.total),cls(R.total)],['Hoje',brl(R.today),cls(R.today)],['Operações',R.n,''],['Acerto',R.n?Math.round(R.win_pct)+'%':'—','']].forEach(x=>{
  const t=el('div',undefined,'tile');t.appendChild(el('b',x[1],x[2]));t.appendChild(el('span',x[0]));tl.appendChild(t)});
 table($('bygroup'),[['Grupo'],['Entradas'],['Operações','n'],['Resultado','n']],
  R.groups.map(g=>[g.grupo,g.members,g.n,{t:brl(g.net),c:cls(g.net)}]),'');
 table($('trades'),[['Saída'],['Entrada'],['Lado'],['Contratos','n'],['Pontos','n'],['Motivo'],['Resultado','n']],
  R.trades.map(t=>[t.exit_time,t.symbol,t.lado,t.qty,t.points,t.reason,{t:brl(t.net),c:cls(t.net)}]),'Ainda não há operações encerradas.');
 table($('strats'),[['Entrada'],['Ativa'],['Grupo'],['Estratégia'],['Saída'],['Stop (ATR)','n'],['Trailing (ATR)','n']],
  j.strats.map(r=>[r.entrada,r.ativa,r.grupo,r.estrategia,r.saida,r.stop_atr,r.trail_atr]),'');
 table($('files'),[['Arquivo'],['O que guarda'],['Situação']],j.files.map(f=>[f.path,f.what,f.state]),'');
 $('log').textContent=j.log||'(sem log hoje)'}
$('on').onclick=()=>act('ligar');$('off').onclick=()=>act('desligar');
refresh();setInterval(refresh,4000);
</script></html>"""


def _alive(pid: int) -> bool:
    # bytes + errors="replace": a saída do tasklist usa a página de código do console (cp850),
    # não UTF-8, e decodificar como texto estourava com acentos.
    raw = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True).stdout or b""
    out = raw.decode("utf-8", errors="replace").lower()
    return f'"{pid}"' in out and "python" in out


def _pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _alive(pid) else None


def _python() -> str:
    for env in (".venv-mt5", ".venv"):
        p = PROJECT_ROOT / env / "Scripts" / "python.exe"
        if p.exists():
            return str(p)
    return sys.executable


def start() -> str:
    cfg = load_config()
    if cfg.mode != "paper":
        return f"Recusado: mode = {cfg.mode!r}. O painel só liga o robô em modo paper (simulado)."
    if _pid():
        return "O robô já está rodando."
    KILL_SWITCH.unlink(missing_ok=True)
    OUT_FILE.parent.mkdir(exist_ok=True)
    with OUT_FILE.open("ab") as out:
        p = subprocess.Popen([_python(), "-m", "robotrader", "live"], cwd=PROJECT_ROOT, stdout=out,
                             stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
    PID_FILE.write_text(str(p.pid))
    return f"Robô ligado (PID {p.pid}). Mantenha o MetaTrader 5 aberto e logado."


def stop() -> str:
    pid = _pid()
    if not pid:
        KILL_SWITCH.unlink(missing_ok=True)
        return "O robô já estava parado."
    KILL_SWITCH.write_text("parado pelo painel\n")
    deadline = time.time() + GRACE_SECONDS
    while time.time() < deadline and _alive(pid):
        time.sleep(1)
    if _alive(pid):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    KILL_SWITCH.unlink(missing_ok=True)
    PID_FILE.unlink(missing_ok=True)
    return "Robô desligado: posições zeradas pelo arquivo STOP e processo encerrado."


# ---------------------------------------------------------------- leitura do estado
def _num(v, nd=0):
    return None if v is None else round(float(v), nd)


def _file_info(path, what: str, note_missing: str) -> dict:
    rel = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    if path.exists():
        st = path.stat()
        return {"path": rel, "what": what,
                "state": f"{st.st_size / 1024:.1f} KB, atualizado {datetime.fromtimestamp(st.st_mtime):%d/%m %H:%M:%S}"}
    return {"path": rel, "what": what, "state": note_missing}


def _read_log(path):
    """Linhas do log do dia, última barra por entrada e eventos relevantes."""
    if not path.exists():
        return [], {}, [], None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    bars, events, last_ts = {}, [], None
    for ln in lines:
        m = LINE_RE.match(ln)
        if not m:
            continue
        last_ts = datetime.fromisoformat(f"{m.group(1)} {m.group(2)}")
        b = BAR_RE.match(ln)
        if b:
            bars[b.group(2)] = {"symbol": b.group(2), "bar": b.group(3), "price": float(b.group(4)), "pos": b.group(5),
                                "day": float(b.group(6)), "ops": int(b.group(7)), "halt": b.group(8) or ""}
        elif "dados=" not in m.group(4):
            events.append({"t": m.group(2), "msg": m.group(4)[:220]})
    return lines, bars, events[-12:][::-1], last_ts


def _read_trades(path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def status() -> dict:
    cfg = load_config()
    log_dir = PROJECT_ROOT / cfg.log_dir
    log_path = log_dir / f"robo_{date.today():%Y%m%d}.log"
    lines, bars, events, last_ts = _read_log(log_path)
    pid = _pid()
    group_of = {m: g.name for g in cfg.portfolios for m in g.members}

    # posições abertas (paper_state.json) com o preço da última barra
    open_pos = []
    state_path = log_dir / "paper" / "paper_state.json"
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except ValueError:
            raw = {}
        for sym, p in raw.items():
            inst = cfg.instruments.get(sym)
            pv = get_spec(inst.root).point_value if inst else 0.0
            price = bars.get(sym, {}).get("price")
            side, e = int(p["side"]), float(p["entry_price"])
            open_pos.append({"symbol": sym, "lado": "Compra" if side > 0 else "Venda", "qty": p["qty"],
                             "entry_time": str(p["entry_time"])[:16].replace("T", " "), "entry_price": e, "price": price,
                             "stop": _num(p.get("stop"), 1), "trail": _num(p.get("trail_dist"), 1),
                             "floating": None if price is None else round((price - e) * side * pv * p["qty"], 2)})

    # resultados (paper_trades.csv)
    trades = _read_trades(log_dir / "paper" / "paper_trades.csv")
    today = f"{date.today():%Y-%m-%d}"
    net = [float(t["net"]) for t in trades]
    groups = []
    for g in cfg.portfolios:
        part = [float(t["net"]) for t in trades if t["symbol"] in g.members]
        groups.append({"grupo": g.name, "members": ", ".join(g.members), "n": len(part), "net": round(sum(part), 2)})
    results = {"total": round(sum(net), 2), "n": len(net), "win_pct": 100 * sum(x > 0 for x in net) / len(net) if net else 0,
               "today": round(sum(float(t["net"]) for t in trades if t["exit_time"].startswith(today)), 2),
               "groups": groups,
               "trades": [{"exit_time": t["exit_time"][:16], "symbol": t["symbol"], "lado": "Compra" if int(t["side"]) > 0 else "Venda",
                           "qty": t["qty"], "points": round(float(t["points"])), "reason": t["reason"], "net": float(t["net"])}
                          for t in trades[-30:][::-1]]}

    strats = []
    for key, i in cfg.instruments.items():
        strats.append({"entrada": key, "ativa": "sim" if i.enabled else "não", "grupo": group_of.get(key, "—"),
                       "estrategia": DESC.get(i.strategy_name, i.strategy_name), "saida": str(i.strategy_params.get("exit_at", "")),
                       "stop_atr": i.stop_atr or "", "trail_atr": i.trail_atr or ""})

    files = [_file_info(log_dir / "paper" / "paper_trades.csv", "cada operação encerrada (uma linha por operação)",
                        "ainda não criado: aparece na primeira operação encerrada"),
             _file_info(state_path, "posições abertas; o robô retoma de onde parou se reiniciar",
                        "ainda não criado: aparece na primeira entrada"),
             _file_info(log_path, "tudo o que o robô decidiu e viu hoje", "sem log hoje")]
    age = (datetime.now() - last_ts).total_seconds() if last_ts else None
    return {"mode": cfg.mode, "running": pid is not None, "pid": pid,
            "stale": age is not None and age > STALE_SECONDS,
            "last_activity": f"{last_ts:%d/%m %H:%M:%S}" if last_ts else None,
            "open": open_pos, "now": sorted(bars.values(), key=lambda b: b["symbol"]), "events": events,
            "results": results, "strats": strats, "files": files, "log": "\n".join(lines[-25:])}


LOCAL = ("localhost", "127.0.0.1")


class Handler(BaseHTTPRequestHandler):
    def _ok_host(self) -> bool:
        """Barra páginas de outros sites (DNS rebinding / CSRF): Host e Origin precisam ser locais."""
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        return host in LOCAL and (origin is None or origin.split("//")[-1].split(":")[0] in LOCAL)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._ok_host():
            return self._send(403, b"proibido", "text/plain")
        if self.path == "/status":
            return self._send(200, json.dumps(status()).encode(), "application/json")
        self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if not self._ok_host():
            return self._send(403, b"proibido", "text/plain")
        action = {"/ligar": start, "/desligar": stop}.get(self.path)
        if action is None:
            return self._send(404, b"{}", "application/json")
        with LOCK:
            msg = action()
        self._send(200, json.dumps({"msg": msg}).encode(), "application/json")

    def log_message(self, *args) -> None:  # sem barulho no terminal
        pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Painel em http://localhost:{PORT}  (Ctrl+C fecha o painel; o robô continua rodando)")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
