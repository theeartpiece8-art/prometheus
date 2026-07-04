"""
Import every ORM model here so that `Base.metadata` is fully populated
before Alembic autogeneration or `Base.metadata.create_all()` (used by the
test suite) runs. This is the standard SQLAlchemy pattern for avoiding
"table not found" / missing-relationship errors caused by import order.
"""
from app.infrastructure.models.backtest import Backtest
from app.infrastructure.models.broker_account import BrokerAccount
from app.infrastructure.models.equity_history import EquityHistory
from app.infrastructure.models.notification import Notification
from app.infrastructure.models.order import Order
from app.infrastructure.models.paper_trading_session import PaperTradingSession, PaperTradingSessionItem
from app.infrastructure.models.performance_report import PerformanceReport
from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.models.position import Position
from app.infrastructure.models.risk_event import RiskEvent
from app.infrastructure.models.settings import UserSettings
from app.infrastructure.models.signal import Signal
from app.infrastructure.models.strategy import Strategy
from app.infrastructure.models.system_log import SystemLog
from app.infrastructure.models.trade import Trade
from app.infrastructure.models.user import User
from app.infrastructure.models.watchlist import Watchlist

__all__ = [
    "Backtest",
    "BrokerAccount",
    "EquityHistory",
    "Notification",
    "Order",
    "PaperTradingSession",
    "PaperTradingSessionItem",
    "PerformanceReport",
    "Portfolio",
    "Position",
    "RiskEvent",
    "UserSettings",
    "Signal",
    "Strategy",
    "SystemLog",
    "Trade",
    "User",
    "Watchlist",
]
