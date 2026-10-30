"""Leitura do arquivo de configuração (TOML) para dataclasses tipadas."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "settings.toml"


def _parse_time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


@dataclass
class SessionConfig:
    start: time = time(9, 5)
    last_entry: time = time(17, 0)
    flatten: time = time(17, 50)


@dataclass
class RiskConfig:
    contracts: int = 1
    max_daily_loss: float = 300.0
    max_trades_per_day: int = 6
    max_consecutive_losses: int = 3


@dataclass
class InstrumentConfig:
    """Uma estratégia operando um contrato. `name` é a chave na configuração
    (ex.: "WIN_REV"); `root` é o contrato (WIN/WDO). Várias entradas podem
    usar o mesmo contrato, cada uma com sua posição simulada."""
    root: str
    name: str = ""
    enabled: bool = True
    data_symbol: str = ""
    contract: str = ""
    stop_points: float = 0.0
    target_points: float = 0.0
    stop_atr: float = 0.0       # > 0: stop = stop_atr x ATR (substitui stop_points)
    target_atr: float = 0.0     # > 0: alvo = target_atr x ATR (substitui target_points)
    atr_period: int = 14
    trail_atr: float = 0.0      # > 0: trailing stop a trail_atr x ATR, ativado ao andar isso a favor
    slippage_ticks: int = 1
    cost_per_contract: float = 0.0
    strategy_name: str = "sma_cross"
    strategy_params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = self.name or self.root


@dataclass
class PortfolioGroup:
    """Estratégias no mesmo contrato operadas como posição líquida: no dia em que uma
    compra e outra vende, nenhuma opera; quando concordam, cada uma usa
    `agree_multiplier` vezes os contratos."""
    name: str
    members: list[str]
    agree_multiplier: float = 2.0


@dataclass
class Config:
    mode: str = "paper"
    timeframe: str = "M5"
    log_dir: str = "logs"
    allow_real_account: bool = False
    magic: int = 260001
    session: SessionConfig = field(default_factory=SessionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    instruments: dict[str, InstrumentConfig] = field(default_factory=dict)
    portfolios: list[PortfolioGroup] = field(default_factory=list)

    def group_of(self, name: str) -> PortfolioGroup | None:
        return next((g for g in self.portfolios if name in g.members), None)


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    general = raw.get("general", {})
    session_raw = raw.get("session", {})
    session = SessionConfig(
        **{k: _parse_time(v) for k, v in session_raw.items()}
    )
    risk = RiskConfig(**raw.get("risk", {}))

    instruments: dict[str, InstrumentConfig] = {}
    for key, inst in raw.get("instruments", {}).items():
        inst = dict(inst)
        strat = dict(inst.pop("strategy", {}))
        strategy_name = strat.pop("name", "sma_cross")
        root = inst.pop("root", key).upper()
        instruments[key] = InstrumentConfig(
            root=root, name=key, strategy_name=strategy_name, strategy_params=strat, **inst
        )

    portfolios = []
    seen: set[str] = set()
    for i, g in enumerate(raw.get("portfolio", [])):
        group = PortfolioGroup(name=g.get("name", f"carteira{i + 1}"), members=list(g["members"]),
                               agree_multiplier=float(g.get("agree_multiplier", 2.0)))
        for m in group.members:
            if m not in instruments:
                raise ValueError(f"portfolio '{group.name}': entrada '{m}' não existe")
            if m in seen:
                raise ValueError(f"'{m}' está em mais de um portfolio")
            seen.add(m)
        if len({instruments[m].root for m in group.members}) != 1:
            raise ValueError(f"portfolio '{group.name}': membros precisam operar o mesmo contrato")
        portfolios.append(group)

    if general.get("mode", "paper") not in ("paper", "mt5"):
        raise ValueError("general.mode deve ser 'paper' ou 'mt5'")

    return Config(
        mode=general.get("mode", "paper"),
        timeframe=general.get("timeframe", "M5"),
        log_dir=general.get("log_dir", "logs"),
        allow_real_account=general.get("allow_real_account", False),
        magic=general.get("magic", 260001),
        session=session,
        risk=risk,
        instruments=instruments,
        portfolios=portfolios,
    )
