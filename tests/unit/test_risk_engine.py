"""
Unit tests for the Risk Management Engine.

Per 13_Testing_Strategy.md: "Risk Engine Tests (Critical Priority)... Failure
in these tests blocks deployment." These tests run with zero external
dependencies (no DB, no network) and must stay fast and deterministic.
"""
from decimal import Decimal

import pytest

from app.domain.risk.risk_engine import RiskEngine, calculate_position_size
from app.domain.risk.risk_models import AccountState, OrderRequest, RiskSettings


@pytest.fixture
def engine() -> RiskEngine:
    return RiskEngine()


@pytest.fixture
def clean_account() -> AccountState:
    """An account with no risk pressure anywhere — the baseline for
    'everything should pass' tests."""
    return AccountState(
        equity=Decimal("10000"),
        balance=Decimal("10000"),
        open_positions_count=0,
        positions_for_symbol_count=0,
        current_daily_loss=Decimal("0"),
        current_drawdown_pct=Decimal("0"),
        current_exposure_by_symbol={},
        current_portfolio_exposure=Decimal("0"),
        kill_switch_active=False,
    )


@pytest.fixture
def default_settings() -> RiskSettings:
    return RiskSettings(
        risk_per_trade_pct=Decimal("1.0"),
        max_daily_loss_pct=Decimal("3.0"),
        max_drawdown_pct=Decimal("10.0"),
        max_open_positions=10,
        max_positions_per_symbol=2,
        max_portfolio_exposure_pct=Decimal("50.0"),
        max_symbol_exposure_pct=Decimal("20.0"),
        max_leverage=Decimal("10.0"),
        min_account_balance=Decimal("0"),
        allowed_symbols=None,
    )


@pytest.fixture
def basic_order() -> OrderRequest:
    # entry=100, stop=90 -> risk_amount=100 (1% of 10000 equity), per_unit_risk=10,
    # size=10, notional=$1000 -- comfortably inside the 20%/$2000 symbol and
    # 50%/$5000 portfolio exposure limits in `default_settings` below, so this
    # fixture represents a realistic order that a clean account should approve.
    return OrderRequest(
        symbol="AAPL",
        side="buy",
        order_type="market",
        requested_price=Decimal("100"),
        stop_loss=Decimal("90"),
        take_profit=Decimal("120"),
        quantity=None,
        strategy_enabled=True,
    )


# --------------------------------------------------------------------------
# Position sizing
# --------------------------------------------------------------------------

class TestPositionSizing:
    def test_basic_calculation(self):
        # risk_amount = 10000 * 1% = 100 ; per_unit_risk = |100 - 98| = 2 ; size = 50
        size = calculate_position_size(Decimal("10000"), Decimal("1"), Decimal("100"), Decimal("98"))
        assert size == Decimal("50.00000000")

    def test_scales_with_risk_percent(self):
        size_1pct = calculate_position_size(Decimal("10000"), Decimal("1"), Decimal("100"), Decimal("98"))
        size_2pct = calculate_position_size(Decimal("10000"), Decimal("2"), Decimal("100"), Decimal("98"))
        assert size_2pct == size_1pct * 2

    def test_zero_stop_distance_returns_zero(self):
        size = calculate_position_size(Decimal("10000"), Decimal("1"), Decimal("100"), Decimal("100"))
        assert size == Decimal("0")

    def test_zero_equity_returns_zero(self):
        size = calculate_position_size(Decimal("0"), Decimal("1"), Decimal("100"), Decimal("98"))
        assert size == Decimal("0")

    def test_negative_risk_pct_returns_zero(self):
        size = calculate_position_size(Decimal("10000"), Decimal("-1"), Decimal("100"), Decimal("98"))
        assert size == Decimal("0")

    def test_short_side_stop_above_entry(self):
        # Short: stop above entry, distance is still absolute
        size = calculate_position_size(Decimal("10000"), Decimal("1"), Decimal("100"), Decimal("102"))
        assert size == Decimal("50.00000000")


# --------------------------------------------------------------------------
# Order Approval Pipeline — the critical "never bypass" behaviors
# --------------------------------------------------------------------------

class TestRiskEnginePipeline:
    def test_order_approved_when_all_checks_pass(self, engine, basic_order, clean_account, default_settings):
        decision = engine.evaluate_order(basic_order, clean_account, default_settings)
        assert decision.approved is True
        assert decision.reason is None
        assert decision.approved_position_size == Decimal("10.00000000")  # 100 risk / 10 stop distance
        assert all(c.result.value == "pass" for c in decision.checks)

    def test_kill_switch_blocks_everything_even_with_perfect_account(
        self, engine, basic_order, clean_account, default_settings
    ):
        import dataclasses

        account = dataclasses.replace(clean_account, kill_switch_active=True)
        decision = engine.evaluate_order(basic_order, account, default_settings)
        assert decision.approved is False
        assert "kill switch" in decision.reason.lower()
        # Kill switch must be the very first check — nothing else should have run.
        assert len(decision.checks) == 1

    def test_disabled_strategy_is_rejected(self, engine, clean_account, default_settings):
        import dataclasses

        order = OrderRequest(
            symbol="EURUSD", side="buy", order_type="market",
            requested_price=Decimal("1.10"), stop_loss=Decimal("1.09"),
            take_profit=None, quantity=None, strategy_enabled=False,
        )
        decision = engine.evaluate_order(order, clean_account, default_settings)
        assert decision.approved is False
        assert "not enabled" in decision.reason.lower()

    def test_symbol_outside_allowlist_is_rejected(self, engine, basic_order, clean_account, default_settings):
        import dataclasses

        settings = dataclasses.replace(default_settings, allowed_symbols=["BTCUSD", "ETHUSD"])
        decision = engine.evaluate_order(basic_order, clean_account, settings)
        assert decision.approved is False
        assert "allowed symbols" in decision.reason.lower()

    def test_daily_loss_limit_blocks_new_trades(self, engine, basic_order, clean_account, default_settings):
        import dataclasses

        # 3% of 10000 = 300; simulate having already lost 300 today.
        account = dataclasses.replace(clean_account, current_daily_loss=Decimal("300"))
        decision = engine.evaluate_order(basic_order, account, default_settings)
        assert decision.approved is False
        assert "daily loss" in decision.reason.lower()

    def test_daily_loss_just_under_limit_is_allowed(self, engine, basic_order, clean_account, default_settings):
        import dataclasses

        account = dataclasses.replace(clean_account, current_daily_loss=Decimal("299.99"))
        decision = engine.evaluate_order(basic_order, account, default_settings)
        assert decision.approved is True

    def test_max_drawdown_blocks_new_trades(self, engine, basic_order, clean_account, default_settings):
        import dataclasses

        account = dataclasses.replace(clean_account, current_drawdown_pct=Decimal("10.0"))
        decision = engine.evaluate_order(basic_order, account, default_settings)
        assert decision.approved is False
        assert "drawdown" in decision.reason.lower()

    def test_max_open_positions_blocks_new_trades(self, engine, basic_order, clean_account, default_settings):
        import dataclasses

        account = dataclasses.replace(clean_account, open_positions_count=10)
        decision = engine.evaluate_order(basic_order, account, default_settings)
        assert decision.approved is False
        assert "open positions" in decision.reason.lower()

    def test_max_positions_per_symbol_blocks_new_trades(self, engine, basic_order, clean_account, default_settings):
        import dataclasses

        account = dataclasses.replace(clean_account, positions_for_symbol_count=2)
        decision = engine.evaluate_order(basic_order, account, default_settings)
        assert decision.approved is False
        assert "aapl" in decision.reason.lower()

    def test_symbol_exposure_limit_blocks_oversized_trade(self, engine, clean_account, default_settings):
        # Max symbol exposure = 20% of 10000 = 2000. Request a huge quantity
        # with no stop loss (explicit sizing) to blow through that limit.
        order = OrderRequest(
            symbol="EURUSD", side="buy", order_type="market",
            requested_price=Decimal("1.10"), stop_loss=None, take_profit=None,
            quantity=Decimal("5000"), strategy_enabled=True,
        )
        engine_ = RiskEngine()
        decision = engine_.evaluate_order(order, clean_account, default_settings)
        assert decision.approved is False
        assert "exposure" in decision.reason.lower()

    def test_portfolio_exposure_limit_blocks_when_other_positions_already_open(
        self, engine, basic_order, clean_account, default_settings
    ):
        import dataclasses

        # Max portfolio exposure = 50% of 10000 = 5000. Already at 4990 exposure.
        account = dataclasses.replace(clean_account, current_portfolio_exposure=Decimal("4990"))
        decision = engine.evaluate_order(basic_order, account, default_settings)
        assert decision.approved is False
        assert "portfolio exposure" in decision.reason.lower()

    def test_minimum_balance_blocks_new_trades(self, engine, basic_order, clean_account, default_settings):
        import dataclasses

        settings = dataclasses.replace(default_settings, min_account_balance=Decimal("5000"))
        account = dataclasses.replace(clean_account, balance=Decimal("4000"))
        decision = engine.evaluate_order(basic_order, account, settings)
        assert decision.approved is False
        assert "minimum" in decision.reason.lower()

    def test_missing_stop_loss_and_quantity_is_rejected(self, engine, clean_account, default_settings):
        order = OrderRequest(
            symbol="EURUSD", side="buy", order_type="market",
            requested_price=Decimal("1.10"), stop_loss=None, take_profit=None,
            quantity=None, strategy_enabled=True,
        )
        decision = engine.evaluate_order(order, clean_account, default_settings)
        assert decision.approved is False
        assert "position size" in decision.reason.lower()

    def test_manual_quantity_cannot_bypass_risk_based_sizing(self, engine, clean_account, default_settings):
        """Critical safety property: if a stop_loss is provided, the risk-derived
        size is authoritative even if the caller also passed an explicit
        (larger) quantity — 'Never allow manual position sizing to bypass
        risk rules' (07_Risk_Management_Engine.md)."""
        order = OrderRequest(
            symbol="AAPL", side="buy", order_type="market",
            requested_price=Decimal("100"), stop_loss=Decimal("90"), take_profit=None,
            quantity=Decimal("999999"),  # attempted bypass
            strategy_enabled=True,
        )
        decision = engine.evaluate_order(order, clean_account, default_settings)
        assert decision.approved is True
        # risk_amount = 100, per_unit_risk = 10 -> size = 10, NOT 999999
        assert decision.approved_position_size == Decimal("10.00000000")

    def test_decision_is_fully_explainable(self, engine, basic_order, clean_account, default_settings):
        """Every decision must carry a full audit trail of checks performed —
        'Every result must be explainable' (08_Backtesting_Engine.md design
        principle, echoed throughout the risk spec)."""
        decision = engine.evaluate_order(basic_order, clean_account, default_settings)
        rule_names = [c.rule for c in decision.checks]
        assert "kill_switch" in rule_names
        assert "daily_loss_limit" in rule_names
        assert "position_sizing" in rule_names
        assert len(decision.checks) >= 10
