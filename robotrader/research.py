"""Pesquisa de estratégias com validação walk-forward.

Ideia: otimizar parâmetros numa janela de treino e medir o resultado na janela
SEGUINTE, que o otimizador não viu. Repetindo isso ao longo do tempo, juntamos
só os resultados "fora da amostra" — é o que mais se aproxima de operar de verdade.

Como o robô zera tudo todo dia e os limites de risco são diários, os dias são
independentes. Por isso cada combinação de parâmetros é rodada UMA vez no
período inteiro e a seleção walk-forward é feita sobre o resultado diário —
exatamente o mesmo resultado de rodar cada janela separadamente, só que muito
mais rápido.
"""

from __future__ import annotations

import itertools
import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from .backtest import run_backtest
from .config import Config, InstrumentConfig
from .metrics import summarize


@dataclass
class Candidate:
    """Família de estratégia + grade de parâmetros.

    Chaves com prefixo "inst." alteram o InstrumentConfig (ex.: "inst.stop_atr");
    as demais são parâmetros da estratégia.
    """
    label: str
    strategy: str
    grid: dict[str, list]
    fixed: dict[str, Any] = field(default_factory=dict)

    def combos(self) -> list[dict[str, Any]]:
        keys = list(self.grid)
        out = []
        for values in itertools.product(*(self.grid[k] for k in keys)):
            out.append({**self.fixed, **dict(zip(keys, values))})
        return out


def _display(params: dict[str, Any]) -> dict[str, Any]:
    """Parâmetros legíveis (sem séries/tabelas de dados)."""
    return {k: v for k, v in params.items() if not isinstance(v, (pd.Series, pd.DataFrame))}


def split_params(inst: InstrumentConfig, params: dict[str, Any]):
    inst_over = {k[5:]: v for k, v in params.items() if k.startswith("inst.")}
    strat = {k: v for k, v in params.items() if not k.startswith("inst.")}
    return replace(inst, **inst_over), strat


# ---------- execução paralela (estado global por processo) ----------
_G: dict[str, Any] = {}


def _init(bars, inst, cfg, strategy):
    _G.update(bars=bars, inst=inst, cfg=cfg, strategy=strategy)


def _run_combo(params: dict[str, Any]) -> pd.DataFrame:
    from .strategies import get_strategy

    inst, strat_params = split_params(_G["inst"], params)
    try:
        strat = get_strategy(_G["strategy"], strat_params)
    except ValueError:
        return pd.DataFrame()  # combinação inválida (ex.: fast >= slow)
    return run_backtest(_G["bars"], inst, _G["cfg"], strat).trades


def run_grid(bars: pd.DataFrame, inst: InstrumentConfig, cfg: Config,
             cand: Candidate, workers: int = 0) -> tuple[list[dict], list[pd.DataFrame]]:
    combos = cand.combos()
    if workers == 1:
        _init(bars, inst, cfg, cand.strategy)
        results = [_run_combo(c) for c in combos]
    else:
        with ProcessPoolExecutor(max_workers=workers or None, initializer=_init,
                                 initargs=(bars, inst, cfg, cand.strategy)) as pool:
            results = list(pool.map(_run_combo, combos, chunksize=4))
    keep = [(c, t) for c, t in zip(combos, results) if t is not None]
    return [c for c, _ in keep], [t for _, t in keep]


def daily_matrix(trades: list[pd.DataFrame], days: pd.Index) -> tuple[np.ndarray, np.ndarray]:
    """Resultado líquido e nº de operações por (combinação, dia)."""
    pnl = np.zeros((len(trades), len(days)))
    count = np.zeros((len(trades), len(days)), dtype=int)
    for i, t in enumerate(trades):
        if t.empty:
            continue
        d = pd.Index(pd.to_datetime(t["exit_time"]).dt.normalize())
        g = t.groupby(d)["net"].agg(["sum", "count"]).reindex(days, fill_value=0)
        pnl[i], count[i] = g["sum"].to_numpy(), g["count"].to_numpy()
    return pnl, count


@dataclass
class WalkForwardResult:
    label: str
    root: str
    oos_trades: pd.DataFrame
    oos_metrics: dict
    folds: pd.DataFrame
    full_period: pd.DataFrame  # cada combinação no período inteiro (referência)

    @property
    def oos_daily(self) -> pd.Series:
        if self.oos_trades.empty:
            return pd.Series(dtype=float)
        return self.oos_trades.groupby(
            pd.to_datetime(self.oos_trades["exit_time"]).dt.normalize())["net"].sum()

    def t_stat(self) -> float:
        """Estatística t do resultado diário fora da amostra (|t| > 2 ~ improvável ser sorte)."""
        d = self.oos_daily
        if len(d) < 2 or d.std() == 0:
            return float("nan")
        return float(d.mean() / d.std() * math.sqrt(len(d)))


def walk_forward(bars: pd.DataFrame, inst: InstrumentConfig, cfg: Config, cand: Candidate,
                 train_days: int = 120, test_days: int = 21, min_trades: int = 20,
                 workers: int = 0) -> WalkForwardResult:
    combos, trades = run_grid(bars, inst, cfg, cand, workers)
    days = pd.Index(pd.to_datetime(bars.index.normalize().unique()))
    pnl, count = daily_matrix(trades, days)

    folds, oos = [], []
    start = train_days
    while start < len(days):
        tr = slice(start - train_days, start)
        te = slice(start, min(start + test_days, len(days)))
        score = pnl[:, tr].sum(axis=1)
        score[count[:, tr].sum(axis=1) < min_trades] = -np.inf
        best = int(np.argmax(score))
        test_set = set(days[te])
        t = trades[best]
        if not t.empty:
            t = t[pd.to_datetime(t["exit_time"]).dt.normalize().isin(test_set)]
            oos.append(t)
        folds.append({
            "treino": f"{days[tr][0]:%d/%m/%y}-{days[tr][-1]:%d/%m/%y}",
            "teste": f"{days[te][0]:%d/%m/%y}-{days[te][-1]:%d/%m/%y}",
            "params": _display(combos[best]),
            "treino_R$": round(float(score[best]), 2),
            "teste_R$": round(float(pnl[best, te].sum()), 2),
            "teste_ops": int(count[best, te].sum()),
        })
        start += test_days

    oos_trades = pd.concat(oos, ignore_index=True) if oos else pd.DataFrame()
    full = pd.DataFrame({
        "params": [str(_display(c)) for c in combos],
        "liquido": pnl.sum(axis=1).round(2),
        "operacoes": count.sum(axis=1),
    }).sort_values("liquido", ascending=False, ignore_index=True)

    return WalkForwardResult(
        label=cand.label, root=inst.root, oos_trades=oos_trades,
        oos_metrics=summarize(oos_trades), folds=pd.DataFrame(folds), full_period=full,
    )
