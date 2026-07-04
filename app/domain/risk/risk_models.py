"""
Domain-level data structures for the Risk Management Engine.

These are deliberately plain dataclasses with no dependency on SQLAlchemy,
FastAPI, or any infrastructure concern — per 12_Coding_Standards.md's Clean
Architecture rule, the Domain layer must not depend on Infrastructure. The
Application layer (risk_service.py) is responsible for translating ORM rows
into these structures and back.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class RiskCheckResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class RiskCheckOutcome:
    rule: str
    result: RiskCheckResult
    detail: str


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str | None
    checks: list[RiskCheckOutcome] = field(default_factory=list)
    approved_position_size: Decimal | None = None


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str  # "buy" | "sell"
    order_type: str  # "market" | "limit" | "stop" | "stop_limit"
    requested_price: Decimal | None
    stop_loss: Decimal | None
    take_profit: Decimal | None
    quantity: Decimal | None  # explicit size; if None, risk engine derives it from stop_loss
    strategy_enabled: bool = True


@dataclass(frozen=True)
class AccountState:
    """A point-in-time snapshot of everything the Risk Engine needs to know
    about the account/portfolio placing the order. Built fresh for every
    single order — never cached, per 07_Risk_Management_Engine.md's
    requirement that risk decisions reflect current state."""

    equity: Decimal
    balance: Decimal
    open_positions_count: int
    positions_for_symbol_count: int
    current_daily_loss: Decimal  # positive number = amount lost today (realized + unrealized)
    current_drawdown_pct: Decimal  # positive number = % down from equity high-water mark
    current_exposure_by_symbol: dict[str, Decimal]
    current_portfolio_exposure: Decimal
    kill_switch_active: bool


@dataclass(frozen=True)
class RiskSettings:
    """User-configurable limits, per the "User Risk Settings" section of
    07_Risk_Management_Engine.md. Populated from UserSettings.risk_settings."""

    risk_per_trade_pct: Decimal = Decimal("1.0")
    max_daily_loss_pct: Decimal = Decimal("3.0")
    max_drawdown_pct: Decimal = Decimal("10.0")
    max_open_positions: int = 10
    max_positions_per_symbol: int = 2
    max_portfolio_exposure_pct: Decimal = Decimal("50.0")
    max_symbol_exposure_pct: Decimal = Decimal("20.0")
    max_leverage: Decimal = Decimal("10.0")
    min_account_balance: Decimal = Decimal("0")
    allowed_symbols: list[str] | None = None
