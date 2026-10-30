"""Grupos de carteira: estratégias no mesmo contrato operadas como posição líquida.

Regra (validada em research/risk_lab.py, "Posição líquida das duas"), aplicada aos
sinais de entrada de uma mesma barra:
- se uma estratégia compra e outra vende: nenhuma entra (numa conta netting as ordens
  se anulariam e só pagariam custos);
- se duas ou mais apontam para o mesmo lado: cada uma entra com `agree_multiplier`
  vezes os contratos;
- sinal isolado: entra normalmente.

`net_rule` é usada tanto pelo backtest (`run_portfolio`) quanto pelo robô ao vivo.
"""

from __future__ import annotations

import pandas as pd

from .backtest import BacktestResult, run_backtest
from .config import Config, PortfolioGroup
from .strategies import get_strategy


def net_rule(signals: dict[str, int], agree_multiplier: float) -> dict[str, tuple[int, float]]:
    """{membro: sinal} -> {membro: (sinal ajustado, multiplicador de contratos)}."""
    active = {k: v for k, v in signals.items() if v}
    if len(set(active.values())) > 1:
        return {k: (0, 0.0) for k in signals}
    mult = agree_multiplier if len(active) >= 2 else 1.0
    return {k: (v, mult if v else 1.0) for k, v in signals.items()}


def apply_net_rule(raw: pd.DataFrame, agree_multiplier: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Versão vetorizada: colunas = membros, linhas = barras."""
    buys, sells = (raw > 0).sum(axis=1), (raw < 0).sum(axis=1)
    conflict = (buys > 0) & (sells > 0)
    agree = ~conflict & ((buys >= 2) | (sells >= 2))
    sig = raw.where(~conflict, 0).astype(int)
    mult = pd.DataFrame(1.0, index=raw.index, columns=raw.columns)
    mult.loc[agree, :] = agree_multiplier
    return sig, mult


def run_portfolio(bars: pd.DataFrame, cfg: Config, group: PortfolioGroup,
                  external: dict[str, pd.DataFrame] | None = None,
                  trade_from: pd.Timestamp | None = None) -> dict[str, BacktestResult]:
    """Backtest de um grupo: decide os sinais juntos e roda cada membro com eles."""
    strategies, raw = {}, {}
    for name in group.members:
        inst = cfg.instruments[name]
        strat = get_strategy(inst.strategy_name, inst.strategy_params)
        if strat.external_symbols:
            if external is None:
                from .external import load_external
                external = load_external(strat.external_symbols, cfg.timeframe)
            strat.update_external(external)
        strategies[name] = strat
        raw[name] = strat.generate(bars)
    sig, mult = apply_net_rule(pd.DataFrame(raw), group.agree_multiplier)
    return {
        name: run_backtest(bars, cfg.instruments[name], cfg, strategies[name],
                           trade_from=trade_from, external=external,
                           raw_signals=sig[name], qty_mult=mult[name])
        for name in group.members
    }
