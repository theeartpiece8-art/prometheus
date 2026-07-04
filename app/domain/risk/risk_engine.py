"""
The Risk Management Engine.

Per 07_Risk_Management_Engine.md: "The Risk Management Engine is the
highest authority within PROMETHEUS Quant Lab. No order may reach a broker
without explicit approval from this engine. If the Risk Engine rejects an
order, execution must stop immediately."

`RiskEngine.evaluate_order` is a pure function of its three inputs (order,
account, settings) — no I/O, no database access, fully deterministic and
unit-testable in isolation. This is intentional: it is the one piece of the
system every other component (OrderService, future BacktestEngine,
PaperTradingEngine, LiveTradingEngine) must funnel through, so it must be
trivially easy to reason about and test exhaustively.

Checks run in a fixed order and short-circuit on the first failure, mirroring
the "Order Approval Pipeline" diagram in the spec. Every check — pass or
fail — is recorded in the returned RiskDecision.checks list so the decision
is always fully explainable (never a black box).
"""
from __future__ import annotations

from decimal import Decimal

from app.domain.risk.risk_models import (
    AccountState,
    OrderRequest,
    RiskCheckOutcome,
    RiskCheckResult,
    RiskDecision,
    RiskSettings,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def calculate_position_size(
    equity: Decimal,
    risk_pct: Decimal,
    entry_price: Decimal,
    stop_loss_price: Decimal,
) -> Decimal:
    """
    Position size = (Equity * Risk%) / |Entry - StopLoss|

    This is the formula specified under "Position Size Calculation" in
    07_Risk_Management_Engine.md. It ensures that if the stop loss is hit,
    the realized loss is exactly `risk_pct` of equity — never more.
    """
    if equity <= ZERO or risk_pct <= ZERO:
        return ZERO
    risk_amount = equity * (risk_pct / HUNDRED)
    per_unit_risk = abs(entry_price - stop_loss_price)
    if per_unit_risk == ZERO:
        return ZERO
    return (risk_amount / per_unit_risk).quantize(Decimal("0.00000001"))


class RiskEngine:
    """Stateless. Safe to use as a singleton / module-level instance."""

    def evaluate_order(
        self,
        order: OrderRequest,
        account: AccountState,
        settings: RiskSettings,
    ) -> RiskDecision:
        checks: list[RiskCheckOutcome] = []

        def _fail(rule: str, detail: str) -> RiskDecision:
            checks.append(RiskCheckOutcome(rule, RiskCheckResult.FAIL, detail))
            return RiskDecision(approved=False, reason=detail, checks=checks)

        def _pass(rule: str, detail: str = "ok") -> None:
            checks.append(RiskCheckOutcome(rule, RiskCheckResult.PASS, detail))

        # 1. Kill switch — absolute veto, checked first, no exceptions.
        if account.kill_switch_active:
            return _fail("kill_switch", "Kill switch is active. All new orders are blocked until reset.")
        _pass("kill_switch")

        # 2. Strategy validation
        if not order.strategy_enabled:
            return _fail("strategy_validation", "The strategy submitting this order is not enabled.")
        _pass("strategy_validation")

        # 3. Allowed symbols
        if settings.allowed_symbols is not None and order.symbol not in settings.allowed_symbols:
            return _fail("allowed_symbols", f"'{order.symbol}' is not in the account's allowed symbols list.")
        _pass("allowed_symbols")

        # 4. Minimum account balance
        if account.balance < settings.min_account_balance:
            return _fail(
                "minimum_balance",
                f"Account balance {account.balance} is below the configured minimum {settings.min_account_balance}.",
            )
        _pass("minimum_balance")

        # 5. Daily loss limit
        max_daily_loss_amount = account.equity * (settings.max_daily_loss_pct / HUNDRED)
        if account.current_daily_loss >= max_daily_loss_amount:
            return _fail(
                "daily_loss_limit",
                f"Daily loss {account.current_daily_loss} has reached the configured limit "
                f"({settings.max_daily_loss_pct}% of equity = {max_daily_loss_amount}). New trades are blocked today.",
            )
        _pass("daily_loss_limit")

        # 6. Maximum drawdown
        if account.current_drawdown_pct >= settings.max_drawdown_pct:
            return _fail(
                "max_drawdown",
                f"Current drawdown {account.current_drawdown_pct}% has reached the configured maximum "
                f"({settings.max_drawdown_pct}%). New trades are blocked.",
            )
        _pass("max_drawdown")

        # 7. Maximum open positions
        if account.open_positions_count >= settings.max_open_positions:
            return _fail(
                "max_open_positions",
                f"Maximum open positions ({settings.max_open_positions}) reached.",
            )
        _pass("max_open_positions")

        # 8. Maximum positions per symbol
        if account.positions_for_symbol_count >= settings.max_positions_per_symbol:
            return _fail(
                "max_positions_per_symbol",
                f"Maximum open positions for {order.symbol} ({settings.max_positions_per_symbol}) reached.",
            )
        _pass("max_positions_per_symbol")

        # 9. Position sizing — either supplied explicitly or derived from risk%/stop-loss.
        # "Never allow manual position sizing to bypass risk rules": if a stop_loss is
        # present, the risk-derived size is authoritative regardless of what was requested.
        if order.stop_loss is not None and order.requested_price is not None:
            position_size = calculate_position_size(
                equity=account.equity,
                risk_pct=settings.risk_per_trade_pct,
                entry_price=order.requested_price,
                stop_loss_price=order.stop_loss,
            )
        else:
            position_size = order.quantity

        if not position_size or position_size <= ZERO:
            return _fail(
                "position_sizing",
                "Could not determine a valid position size. Provide a stop_loss (for automatic "
                "risk-based sizing) or an explicit quantity.",
            )
        _pass("position_sizing", f"approved size = {position_size}")

        # 10. Symbol exposure
        notional = position_size * (order.requested_price or ZERO)
        current_symbol_exposure = account.current_exposure_by_symbol.get(order.symbol, ZERO)
        projected_symbol_exposure = current_symbol_exposure + notional
        max_symbol_exposure = account.equity * (settings.max_symbol_exposure_pct / HUNDRED)
        if projected_symbol_exposure > max_symbol_exposure:
            return _fail(
                "symbol_exposure",
                f"This order would bring {order.symbol} exposure to {projected_symbol_exposure}, "
                f"exceeding the configured limit of {max_symbol_exposure} "
                f"({settings.max_symbol_exposure_pct}% of equity).",
            )
        _pass("symbol_exposure")

        # 11. Portfolio exposure
        projected_portfolio_exposure = account.current_portfolio_exposure + notional
        max_portfolio_exposure = account.equity * (settings.max_portfolio_exposure_pct / HUNDRED)
        if projected_portfolio_exposure > max_portfolio_exposure:
            return _fail(
                "portfolio_exposure",
                f"This order would bring total portfolio exposure to {projected_portfolio_exposure}, "
                f"exceeding the configured limit of {max_portfolio_exposure} "
                f"({settings.max_portfolio_exposure_pct}% of equity).",
            )
        _pass("portfolio_exposure")

        # 12. Leverage
        if account.equity > ZERO:
            implied_leverage = projected_portfolio_exposure / account.equity
            if implied_leverage > settings.max_leverage:
                return _fail(
                    "max_leverage",
                    f"This order would imply leverage of {implied_leverage:.2f}x, exceeding the "
                    f"configured maximum of {settings.max_leverage}x.",
                )
        _pass("max_leverage")

        return RiskDecision(
            approved=True,
            reason=None,
            checks=checks,
            approved_position_size=position_size,
        )


risk_engine = RiskEngine()
