"""Linha de comando.

Exemplos:
    python -m robotrader check                 (testa conexão com o MT5)
    python -m robotrader download --days 365   (baixa histórico do MT5)
    python -m robotrader backtest --synthetic
    python -m robotrader backtest --instrument WIN --csv data/WIN_M5.csv
    python -m robotrader replay --instrument WDO --synthetic
    python -m robotrader contracts
    python -m robotrader signal                (decisão do dia da estratégia do S&P)
    python -m robotrader live                  (requer MT5; modo definido em config)
    python -m robotrader paper-report          (resultado do modo simulado)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .backtest import run_backtest
from .config import load_config
from .contracts import SPECS, active_contract, get_spec
from .data import TIMEFRAME_MINUTES, load_csv, synthetic_bars

START_PRICES = {"WIN": 130_000.0, "WDO": 5_500.0}


def _roots(cfg, arg: str) -> list[str]:
    if arg.upper() == "ALL":
        return [r for r, i in cfg.instruments.items() if i.enabled]
    return [arg.upper()]


def _contract_roots(cfg, arg: str) -> list[str]:
    """Contratos distintos (WIN/WDO) das entradas escolhidas — para downloads."""
    return sorted({cfg.instruments[k].root for k in _roots(cfg, arg)})


def _load_bars(args, key: str, cfg):
    if args.csv:
        return load_csv(args.csv)
    root = cfg.instruments[key].root
    return synthetic_bars(
        get_spec(root), START_PRICES[root], days=args.days,
        minutes=TIMEFRAME_MINUTES[cfg.timeframe], seed=args.seed,
    )


def cmd_backtest(args, cfg) -> int:
    if not args.csv:
        print("AVISO: dados SINTÉTICOS (passeio aleatório). Serve para testar o código, "
              "não para avaliar a estratégia.\n")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for root in _roots(cfg, args.instrument):
        bars = _load_bars(args, root, cfg)
        res = run_backtest(bars, cfg.instruments[root], cfg)
        print(f"=== {root} | {res.strategy} | {len(bars)} barras "
              f"{bars.index[0]:%d/%m/%Y} a {bars.index[-1]:%d/%m/%Y} ===")
        print(json.dumps(res.metrics, indent=2, ensure_ascii=False, default=str))
        if res.blocked:
            print("Sinais bloqueados pelo risco:", res.blocked)
        path = out_dir / f"trades_{root}.csv"
        res.trades.to_csv(path, index=False)
        print(f"Operações salvas em {path}\n")
    return 0


def cmd_replay(args, cfg) -> int:
    """Roda o loop ao vivo com corretora simulada sobre dados históricos."""
    from .broker.paper import ReplayFeed, build_paper_broker
    from .live import LiveTrader, setup_logging
    from .metrics import summarize, trades_to_frame

    from .external import load_external
    from .strategies import get_strategy

    setup_logging(cfg.log_dir)
    for root in _roots(cfg, args.instrument):
        bars = _load_bars(args, root, cfg)
        inst = cfg.instruments[root]
        needs = get_strategy(inst.strategy_name, inst.strategy_params).external_symbols
        feed = ReplayFeed(bars, extra=load_external(needs, cfg.timeframe) if needs else None)
        broker = build_paper_broker([inst])
        trader = LiveTrader.build(cfg, feed, broker, [root])
        while not feed.finished:
            feed.advance()
            trader.step()
        print(f"=== Replay {root} ===")
        print(json.dumps(summarize(trades_to_frame(broker.trades)),
                         indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_live(args, cfg) -> int:
    from .live import LiveTrader, setup_logging

    setup_logging(cfg.log_dir)
    from .broker.mt5 import MT5Connector

    roots = _roots(cfg, args.instrument)
    contracts = [cfg.instruments[r].root for r in roots]
    if cfg.mode == "mt5" and len(contracts) != len(set(contracts)):
        print("Modo mt5 bloqueado: há mais de uma estratégia no mesmo contrato "
              f"({', '.join(roots)}). Numa conta netting as posições se misturariam. "
              "Use mode = \"paper\" ou deixe só uma estratégia por contrato.")
        return 2
    connector = MT5Connector(cfg.magic, cfg.allow_real_account)
    connector.connect(trading=cfg.mode == "mt5")
    if cfg.mode == "mt5":
        if not args.confirm:
            print("Modo mt5 envia ORDENS. Rode novamente com --confirm.")
            return 2
        broker = connector
    else:
        from .broker.paper import build_paper_broker
        from .live import PROJECT_ROOT
        journal = PROJECT_ROOT / cfg.log_dir / "paper"
        broker = build_paper_broker([cfg.instruments[r] for r in roots], journal)
        print(f"Modo SIMULADO: operações gravadas em {journal}")

    trader = LiveTrader.build(cfg, connector, broker, roots)
    if cfg.mode == "mt5":
        connector.symbol_roots = {r.order_symbol: r.inst.root for r in trader.runners}
    for r in trader.runners:
        connector.ensure_symbol(r.data_symbol)
        for sym in r.strategy.external_symbols:
            connector.ensure_symbol(sym)
        if cfg.mode == "mt5":
            connector.ensure_symbol(r.order_symbol)
    try:
        trader.run_forever(args.poll)
    except KeyboardInterrupt:
        print("Encerrado pelo usuário (posições abertas NÃO foram zeradas).")
    finally:
        connector.shutdown()
    return 0


def _connect(cfg, trading: bool = False):
    from .broker.mt5 import MT5Connector

    connector = MT5Connector(cfg.magic, cfg.allow_real_account)
    connector.connect(trading=trading)
    return connector


def cmd_check(args, cfg) -> int:
    """Testa a conexão com o MT5 e mostra os símbolos de WIN/WDO disponíveis."""
    connector = _connect(cfg)
    try:
        for k, v in connector.describe().items():
            print(f"{k:>20}: {v}")
        for key, inst in cfg.instruments.items():
            root = inst.root
            contract = inst.contract or active_contract(root, date.today())
            found = connector.find_symbols(f"{root}*")
            print(f"\n{key} ({root}): {len(found)} símbolos encontrados")
            for sym in (inst.data_symbol, contract):
                status = "OK" if sym in found else "NÃO ENCONTRADO"
                print(f"  {sym:<10} {status}")
            if inst.data_symbol not in found:
                print("  Sugestões:", ", ".join(sorted(found)[:15]))
    finally:
        connector.shutdown()
    return 0


def cmd_download(args, cfg) -> int:
    """Baixa histórico do MT5 e salva em data/<ATIVO>_<TF>.csv."""
    from datetime import datetime, timedelta

    connector = _connect(cfg)
    tf = args.timeframe or cfg.timeframe
    # só pregões encerrados: o dia corrente ficaria incompleto
    end = datetime.combine(date.today(), datetime.min.time())
    start = end - timedelta(days=args.days)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        for root in _contract_roots(cfg, args.instrument):
            symbol = args.symbol or next(i.data_symbol for i in cfg.instruments.values()
                                         if i.root == root)
            bars = connector.history(symbol, tf, start, end)
            path = out / f"{root}_{tf}.csv"
            bars.to_csv(path)
            print(f"{symbol}: {len(bars)} barras de {bars.index[0]:%d/%m/%Y} a "
                  f"{bars.index[-1]:%d/%m/%Y} -> {path}")
            if bars.index[0] > start + timedelta(days=5):
                info = connector.describe()
                if len(bars) >= 0.95 * info["max_barras_grafico"]:
                    print("  Aviso: atingiu o limite de barras do MT5. Aumente 'Máx. barras no "
                          "gráfico' em Ferramentas > Opções > Gráficos.")
                else:
                    print("  Aviso: a corretora não tem histórico anterior a "
                          f"{bars.index[0]:%d/%m/%Y} neste tempo gráfico.")
    finally:
        connector.shutdown()
    return 0


def cmd_download_flow(args, cfg) -> int:
    """Baixa negócios do MT5 dia a dia e salva barras de fluxo de 1 minuto.

    Retomável: dias já presentes no arquivo são pulados.
    """
    import time as systime
    from datetime import datetime, timedelta

    import pandas as pd

    from .flow import aggregate, ticks_to_frame

    connector = _connect(cfg)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    today = date.today()
    try:
        for root in _contract_roots(cfg, args.instrument):
            fixed_symbol = args.symbol or next(i.data_symbol for i in cfg.instruments.values()
                                               if i.root == root)
            path = out / f"{root}_M1_flow.csv"
            done: set = set()
            if path.exists():
                done = set(pd.read_csv(path, usecols=["datetime"], parse_dates=["datetime"])
                           ["datetime"].dt.date)
            days = [today - timedelta(days=i) for i in range(args.days, 0, -1)]
            days = [d for d in days if d.weekday() < 5 and d not in done]
            label = "contrato vigente de cada dia" if args.by_contract else fixed_symbol
            print(f"{root} ({label}): {len(done)} dias já baixados, {len(days)} a verificar")
            for d in days:
                t0 = systime.time()
                symbol = active_contract(root, d) if args.by_contract else fixed_symbol
                if args.by_contract and symbol not in connector.find_symbols(f"{root}*"):
                    continue
                start = datetime(d.year, d.month, d.day, 8, 0)
                try:
                    ticks = connector.trades(symbol, start, start + timedelta(hours=11))
                except RuntimeError as exc:
                    print(f"  {d} {symbol}: pulado ({exc})")
                    continue
                if len(ticks) == 0:
                    continue
                bars = aggregate(ticks_to_frame(ticks), 1)
                bars.to_csv(path, mode="a", header=not path.exists())
                share = bars[["buy_vol", "sell_vol"]].sum().sum() / max(bars["volume"].sum(), 1)
                print(f"  {d} {symbol}: {len(ticks):>9} negócios -> {len(bars)} barras "
                      f"({100 * share:.0f}% com agressor) em {systime.time() - t0:.0f}s",
                      flush=True)
    finally:
        connector.shutdown()
    return 0


def cmd_signal(args, cfg) -> int:
    """Mostra a decisão do dia das estratégias que dependem de mercado externo."""
    from .strategies import get_strategy

    connector = _connect(cfg)
    try:
        for root, inst in cfg.instruments.items():
            if not inst.enabled:
                continue
            strat = get_strategy(inst.strategy_name, inst.strategy_params)
            if not strat.external_symbols:
                print(f"{root}: estratégia {strat.name} não usa mercado externo")
                continue
            ext = {s: connector.get_bars(s, cfg.timeframe, strat.external_bars)
                   for s in strat.external_symbols}
            strat.update_external(ext)
            gaps, sig = strat.gaps, strat._day_signal()
            if gaps.empty:
                print(f"{root}: sem dados de {strat.external_symbols}")
                continue
            last = gaps.index[-1]
            past = gaps.abs().iloc[-(strat.lookback + 1):-1]
            thr = past.quantile(strat.threshold_q)
            decision = {1: "COMPRAR", -1: "VENDER", 0: "não operar"}[int(sig.iloc[-1])]
            print(f"{root} | {strat.external_symbols[0]} gap de {last:%d/%m/%Y}: "
                  f"{100 * gaps.iloc[-1]:+.2f}% | filtro (mediana de {len(past)} pregões): "
                  f"±{100 * thr:.2f}% | decisão às 9:05: {decision} "
                  f"| saída após {strat.params['exit_at']}")
            if last != date.today():
                print("   (o gap de hoje ainda não existe: o WSP abre às 9:00)")
    finally:
        connector.shutdown()
    return 0


def cmd_paper_report(args, cfg) -> int:
    """Resumo das operações do modo simulado (logs/paper/paper_trades.csv)."""
    import pandas as pd

    from .live import PROJECT_ROOT
    from .metrics import summarize

    path = PROJECT_ROOT / cfg.log_dir / "paper" / "paper_trades.csv"
    if not path.exists():
        print(f"Nenhuma operação simulada ainda ({path}).")
        return 0
    t = pd.read_csv(path, parse_dates=["entry_time", "exit_time"])
    cols = ["symbol", "side", "entry_time", "entry_price", "exit_time", "exit_price",
            "points", "net", "reason"]
    print(t[cols].to_string(index=False))
    print()
    print("=== Por estratégia ===")
    by = t.groupby("symbol").net.agg(operacoes="count", resultado="sum")
    by["acerto_%"] = t.groupby("symbol").net.apply(lambda x: round(100 * (x > 0).mean()))
    print(by.to_string())
    print()
    print("=== Por grupo de carteira ===")
    for g in cfg.portfolios:
        part = t[t.symbol.isin(g.members)]
        print(f"{g.name:<10} ({', '.join(g.members)}): {len(part)} operações, "
              f"R$ {part.net.sum():+.2f}")
    print()
    print("=== Total ===")
    print(json.dumps(summarize(t), indent=2, ensure_ascii=False, default=str))
    print("Referência do backtest 2023-2026 (research/portfolio_check.py): grupo 'principal' "
          "~R$ 10 mil/ano e grupo 'trailing' ~R$ 8,7 mil/ano por contrato-base.")
    return 0


def cmd_contracts(args, cfg) -> int:
    today = date.fromisoformat(args.date) if args.date else date.today()
    for root, spec in SPECS.items():
        print(f"{root}: contrato vigente em {today:%d/%m/%Y} = {active_contract(root, today)} "
              f"| tick {spec.tick_size} pts = R$ {spec.tick_value:.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="robotrader")
    p.add_argument("--config", help="caminho do settings.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("backtest", "replay"):
        s = sub.add_parser(name)
        s.add_argument("--instrument", default="all", help="WIN, WDO ou all")
        s.add_argument("--csv", help="arquivo de barras (formato padrão ou exportado do MT5)")
        s.add_argument("--synthetic", action="store_true", help="usa dados sintéticos")
        s.add_argument("--days", type=int, default=120)
        s.add_argument("--seed", type=int, default=42)
        s.add_argument("--out", default="reports")

    s = sub.add_parser("live")
    s.add_argument("--instrument", default="all")
    s.add_argument("--confirm", action="store_true", help="obrigatório no modo mt5")
    s.add_argument("--poll", type=float, default=5.0, help="segundos entre verificações")

    s = sub.add_parser("contracts")
    s.add_argument("--date", help="AAAA-MM-DD")

    sub.add_parser("check", help="testa a conexão com o MT5")
    sub.add_parser("signal", help="mostra a decisão do dia (estratégias com mercado externo)")
    sub.add_parser("paper-report", help="resumo das operações do modo simulado")

    s = sub.add_parser("download", help="baixa histórico do MT5 para CSV")
    s.add_argument("--instrument", default="all")
    s.add_argument("--symbol", help="símbolo no MT5 (padrão: data_symbol da config)")
    s.add_argument("--timeframe", help="padrão: timeframe da config")
    s.add_argument("--days", type=int, default=365)
    s.add_argument("--out", default="data")

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    s = sub.add_parser("download-flow", help="baixa negócios do MT5 e gera barras de fluxo")
    s.add_argument("--instrument", default="all")
    s.add_argument("--symbol", help="símbolo no MT5 (padrão: data_symbol da config)")
    s.add_argument("--days", type=int, default=400, help="dias corridos para trás")
    s.add_argument("--by-contract", action="store_true",
                   help="usa o contrato vigente de cada dia (ex.: WIN, sem ticks na série contínua)")
    s.add_argument("--out", default="data/flow")

    args = p.parse_args(argv)
    cfg = load_config(args.config)
    try:
        return _dispatch(args, cfg)
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1


def _dispatch(args, cfg) -> int:
    return {"backtest": cmd_backtest, "replay": cmd_replay, "live": cmd_live,
            "contracts": cmd_contracts, "check": cmd_check,
            "download": cmd_download, "download-flow": cmd_download_flow,
            "signal": cmd_signal, "paper-report": cmd_paper_report}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
