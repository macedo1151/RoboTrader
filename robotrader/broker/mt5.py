"""Conector MetaTrader 5 (dados e ordens).

Requer Windows, o terminal MT5 da corretora aberto e logado, e o pacote
`MetaTrader5` (que hoje exige Python 3.12/3.13). Credenciais NÃO ficam no
código: o conector usa a sessão já logada no terminal, ou as variáveis de
ambiente MT5_LOGIN, MT5_PASSWORD e MT5_SERVER, se definidas.

Contas B3 no MT5 normalmente são "netting": existe no máximo uma posição por
símbolo, e uma ordem contrária reduz/zera a posição.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from ..contracts import get_spec
from .base import Broker, ClosedTrade, DataFeed, OpenPosition

log = logging.getLogger(__name__)


def server_time(dt: datetime) -> datetime:
    """Converte um horário "de relógio" do servidor para o formato que o pacote MT5 espera.

    O MT5 representa o horário do servidor (aqui, Brasília) como se fosse UTC. Datas sem
    fuso passadas ao pacote são tratadas como hora LOCAL do Windows e convertidas para UTC,
    o que desloca a consulta em 3 horas. Marcando como UTC, o horário chega intacto.
    """
    return dt.replace(tzinfo=timezone.utc)


def from_server_ts(ts: float) -> datetime:
    """Timestamp do MT5 -> horário do servidor sem fuso (mesma convenção das barras)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _import_mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Pacote MetaTrader5 não instalado. Use Python 3.12/3.13 e "
            "`pip install -r requirements-mt5.txt`."
        ) from exc
    return mt5


class MT5Connector(DataFeed, Broker):
    def __init__(self, magic: int, allow_real_account: bool = False,
                 symbol_roots: dict[str, str] | None = None) -> None:
        self.mt5 = _import_mt5()
        self.magic = magic
        self.allow_real_account = allow_real_account
        self.symbol_roots = symbol_roots or {}  # contrato -> raiz (WINZ26 -> WIN)
        self.read_only = True
        self.is_demo: bool | None = None

    # ---------- conexão ----------
    def connect(self, trading: bool = True) -> None:
        """Conecta ao terminal. Com trading=False só lê dados: aceita qualquer conta
        e bloqueia o envio de ordens."""
        mt5 = self.mt5
        kwargs = {}
        if os.environ.get("MT5_LOGIN"):
            kwargs = {
                "login": int(os.environ["MT5_LOGIN"]),
                "password": os.environ.get("MT5_PASSWORD", ""),
                "server": os.environ.get("MT5_SERVER", ""),
            }
        if not mt5.initialize(**kwargs):
            code, msg = mt5.last_error()
            hint = ""
            if code in (-10005, -10003, -6):
                hint = (" — o terminal MetaTrader 5 não foi encontrado ou não está aberto. "
                        "Instale o MT5 da sua corretora, abra-o e faça login.")
            raise RuntimeError(f"Falha ao conectar no MT5 ({code}, {msg}){hint}")

        info = mt5.account_info()
        if info is None:
            raise RuntimeError("MT5 conectado mas sem conta logada")
        is_demo = info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        self.is_demo = is_demo
        log.info("MT5 conectado: conta %s (%s), servidor %s, %s",
                 info.login, "DEMO" if is_demo else "REAL", info.server,
                 "com ordens" if trading else "somente leitura")
        self.read_only = not trading
        if trading and not is_demo and not self.allow_real_account:
            mt5.shutdown()
            raise RuntimeError(
                "Conta REAL detectada e allow_real_account=false. Operação bloqueada."
            )
        if not mt5.terminal_info().trade_allowed:
            log.warning("Algotrading desativado no terminal (botão 'Algo Trading').")

    def shutdown(self) -> None:
        self.mt5.shutdown()

    def ensure_symbol(self, symbol: str) -> None:
        if not self.mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Símbolo {symbol} indisponível no MT5")

    # ---------- dados ----------
    def get_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        mt5 = self.mt5
        tf = getattr(mt5, f"TIMEFRAME_{timeframe}")
        rates = mt5.copy_rates_from_pos(symbol, tf, 1, count)  # pos=1: ignora barra em formação
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Sem dados para {symbol}: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        df.index = pd.to_datetime(df["time"], unit="s")
        df.index.name = "datetime"
        vol = df["real_volume"] if df["real_volume"].sum() > 0 else df["tick_volume"]
        df["volume"] = vol
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    def history(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Barras de um período, em blocos (o MT5 limita o tamanho de cada pedido)."""
        mt5 = self.mt5
        self.ensure_symbol(symbol)
        tf = getattr(mt5, f"TIMEFRAME_{timeframe}")
        frames = []
        chunk = timedelta(days=30)
        cur = start
        while cur < end:
            nxt = min(cur + chunk, end)
            rates = mt5.copy_rates_range(symbol, tf, server_time(cur), server_time(nxt))
            if rates is not None and len(rates):
                frames.append(pd.DataFrame(rates))
            cur = nxt
        if not frames:
            raise RuntimeError(
                f"Sem histórico para {symbol} ({mt5.last_error()}). Abra o gráfico do "
                "símbolo no MT5 e role para trás para forçar o download."
            )
        df = pd.concat(frames).drop_duplicates("time").sort_values("time")
        df.index = pd.to_datetime(df["time"], unit="s")
        df.index.name = "datetime"
        df["volume"] = df["real_volume"] if df["real_volume"].sum() > 0 else df["tick_volume"]
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    def trades(self, symbol: str, start: datetime, end: datetime):
        """Negócios (ticks com último preço) de um período, como array do MT5."""
        mt5 = self.mt5
        self.ensure_symbol(symbol)
        ticks = mt5.copy_ticks_range(symbol, server_time(start), server_time(end),
                                     mt5.COPY_TICKS_TRADE)
        if ticks is None:
            raise RuntimeError(f"Falha ao obter ticks de {symbol}: {mt5.last_error()}")
        return ticks

    def describe(self) -> dict:
        mt5 = self.mt5
        acc, term = mt5.account_info(), mt5.terminal_info()
        return {
            "conta": acc.login,
            "tipo": "DEMO" if acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL",
            "servidor": acc.server,
            "corretora": acc.company,
            "saldo": acc.balance,
            "netting": acc.margin_mode == mt5.ACCOUNT_MARGIN_MODE_RETAIL_NETTING,
            "algotrading_ativo": term.trade_allowed,
            "max_barras_grafico": term.maxbars,
        }

    def find_symbols(self, pattern: str) -> list[str]:
        return [s.name for s in (self.mt5.symbols_get(pattern) or [])]

    # ---------- ordens ----------
    def _root(self, symbol: str) -> str:
        return self.symbol_roots.get(symbol, symbol[:3])

    def position(self, symbol: str) -> OpenPosition | None:
        positions = self.mt5.positions_get(symbol=symbol) or []
        mine = [p for p in positions if p.magic == self.magic]
        if not mine:
            return None
        p = mine[0]
        side = 1 if p.type == self.mt5.POSITION_TYPE_BUY else -1
        return OpenPosition(side, int(p.volume), p.price_open)

    def _deviation(self, symbol: str, tick_size: float, ticks: int = 3) -> int:
        """Desvio máximo aceito, em 'points' do MT5 (que variam por símbolo)."""
        point = self.mt5.symbol_info(symbol).point or tick_size
        return max(1, int(round(ticks * tick_size / point)))

    def _send(self, request: dict) -> bool:
        if self.read_only:
            raise RuntimeError("Conector MT5 em modo somente leitura: ordem bloqueada.")
        result = self.mt5.order_send(request)
        ok = result is not None and result.retcode in (
            self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_PLACED
        )
        if ok:
            log.info("Ordem executada: %s", request)
        else:
            log.error("Ordem rejeitada: %s -> %s %s", request,
                      getattr(result, "retcode", None), getattr(result, "comment", None))
        return ok

    def open(self, symbol, side, qty, stop_points, target_points, comment="",
             trail_points=0.0) -> bool:
        if trail_points:
            raise RuntimeError("Trailing stop ainda não é suportado no modo mt5 (só no simulado).")
        mt5 = self.mt5
        spec = get_spec(self._root(symbol))
        self.ensure_symbol(symbol)
        tick = mt5.symbol_info_tick(symbol)
        price = tick.ask if side > 0 else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(qty),
            "type": mt5.ORDER_TYPE_BUY if side > 0 else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": spec.round_price(price - side * stop_points) if stop_points else 0.0,
            "tp": spec.round_price(price + side * target_points) if target_points else 0.0,
            "deviation": self._deviation(symbol, spec.tick_size),
            "magic": self.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_DAY,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        return self._send(request)

    def close(self, symbol, reason="") -> bool:
        mt5 = self.mt5
        positions = [p for p in (mt5.positions_get(symbol=symbol) or []) if p.magic == self.magic]
        ok = True
        for p in positions:
            tick = mt5.symbol_info_tick(symbol)
            is_buy = p.type == mt5.POSITION_TYPE_BUY
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": p.volume,
                "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                "price": tick.bid if is_buy else tick.ask,
                "position": p.ticket,
                "deviation": self._deviation(symbol, get_spec(self._root(symbol)).tick_size),
                "magic": self.magic,
                "comment": reason[:31],
                "type_time": mt5.ORDER_TIME_DAY,
                "type_filling": mt5.ORDER_FILLING_RETURN,
            }
            ok = self._send(request) and ok
        return ok

    def closed_trades_today(self, symbol, today) -> list[ClosedTrade]:
        mt5 = self.mt5
        start = datetime(today.year, today.month, today.day)
        deals = mt5.history_deals_get(server_time(start),
                                      server_time(start + timedelta(days=1))) or []
        out = []
        for d in deals:
            if d.symbol != symbol or d.magic != self.magic:
                continue
            if d.entry not in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT):
                continue
            net = d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
            out.append(ClosedTrade(from_server_ts(d.time), net))
        return out
