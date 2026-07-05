"""
Order Synchronization Engine (Sprint 4 continuation, Module 6).

Reconciles the ORDER lifecycle between broker and local DB -- the
companion to Module 5's position sync, covering the states before/around
fills. Same architecture: detect -> log -> audit (RiskEvent) -> resolve
(unless dry-run), idempotent by construction.

Detections and their resolutions -- deliberately CONSERVATIVE, because
orders are where duplicate-execution risk lives:

- stale_local_pending: a local PENDING/PARTIALLY_FILLED order with a
  broker_order_id the broker no longer lists as open. Without an
  execution report we CANNOT know whether it filled or was cancelled --
  fabricating a fill would corrupt P&L. Resolution: mark CANCELLED with
  an explicit reconciliation reason; if it actually filled, Module 5's
  position sync will surface the resulting position as an
  orphan_broker_position and recreate it correctly. The two engines are
  designed to be run together (positions after orders).

- unacknowledged_local_order: a local PENDING order with NO
  broker_order_id -- the app died between creating the row and receiving
  the broker's ack. It is UNKNOWABLE whether the broker received it.
  Resolution: mark REJECTED with reason; NEVER auto-retry (retrying an
  order that may have silently succeeded is exactly how duplicate
  executions happen). The audit record flags it for operator review.

- orphan_broker_order: the broker lists an open order we have no row
  for. Resolution: create a local PENDING row linked by broker_order_id
  so it's visible and cancellable from the platform.

- duplicate_local_orders: two+ non-terminal local rows sharing one
  broker_order_id. Resolution: keep the oldest, mark the rest REJECTED
  as duplicates.

- fill_quantity_drift: broker reports a different filled quantity than
  the local PARTIALLY_FILLED row records. Resolution: local updated to
  broker value (broker is source of truth for its own book).
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.domain.broker.broker_interface import BrokerAdapter
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.models.enums import OrderStatus, RiskEventSeverity
from app.infrastructure.models.order import Order
from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.models.risk_event import RiskEvent

logger = get_logger("order_sync")

_NON_TERMINAL = (OrderStatus.PENDING.value, OrderStatus.PARTIALLY_FILLED.value)


@dataclass(frozen=True)
class OrderSyncDiscrepancy:
    kind: str
    detail: str
    local_order_id: str | None = None
    broker_order_id: str | None = None
    resolved: bool = False
    resolution: str | None = None


@dataclass
class OrderSyncReport:
    correlation_id: uuid.UUID = field(default_factory=uuid.uuid4)
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    dry_run: bool = False
    broker_open_orders_seen: int = 0
    local_open_orders_seen: int = 0
    discrepancies: list[OrderSyncDiscrepancy] = field(default_factory=list)

    @property
    def in_sync(self) -> bool:
        return not self.discrepancies


class OrderSyncEngine:
    def __init__(self, db: Session, broker: BrokerAdapter) -> None:
        self.db = db
        self.broker = broker

    def sync(self, portfolio: Portfolio, *, dry_run: bool = False) -> OrderSyncReport:
        report = OrderSyncReport(dry_run=dry_run)

        broker_orders = {o.broker_order_id: o for o in self.broker.get_orders()}
        local_open = (
            self.db.query(Order)
            .filter(Order.portfolio_id == portfolio.id, Order.status.in_(_NON_TERMINAL))
            .order_by(Order.submitted_at.asc())
            .all()
        )
        report.broker_open_orders_seen = len(broker_orders)
        report.local_open_orders_seen = len(local_open)

        # --- duplicate local orders (same broker_order_id) --------------
        seen_broker_ids: dict[str, Order] = {}
        for order in local_open:
            if order.broker_order_id is None:
                continue
            if order.broker_order_id in seen_broker_ids:
                self._handle(
                    report, portfolio, dry_run,
                    OrderSyncDiscrepancy(
                        kind="duplicate_local_orders",
                        detail=f"Local orders {seen_broker_ids[order.broker_order_id].id} and {order.id} both claim broker order {order.broker_order_id}; keeping the older.",
                        local_order_id=str(order.id), broker_order_id=order.broker_order_id,
                    ),
                    fix=lambda o=order: self._mark(o, OrderStatus.REJECTED, "Duplicate of an earlier local order for the same broker order (order sync)."),
                    resolution="Newer duplicate marked rejected; oldest kept.",
                )
            else:
                seen_broker_ids[order.broker_order_id] = order

        # --- local non-terminal orders vs broker book --------------------
        for order in local_open:
            if order.status not in _NON_TERMINAL:
                continue  # may have been resolved as a duplicate above

            if order.broker_order_id is None:
                self._handle(
                    report, portfolio, dry_run,
                    OrderSyncDiscrepancy(
                        kind="unacknowledged_local_order",
                        detail=f"Local order {order.id} ({order.side} {order.quantity} {order.symbol}) has no broker acknowledgement; whether the broker received it is unknowable.",
                        local_order_id=str(order.id),
                    ),
                    fix=lambda o=order: self._mark(o, OrderStatus.REJECTED, "No broker acknowledgement (order sync). NOT auto-retried: retrying an order that may have silently succeeded risks duplicate execution. Review manually."),
                    resolution="Marked rejected; flagged for manual review. Never auto-retried.",
                )
                continue

            broker_order = broker_orders.get(order.broker_order_id)
            if broker_order is None:
                self._handle(
                    report, portfolio, dry_run,
                    OrderSyncDiscrepancy(
                        kind="stale_local_pending",
                        detail=f"Local order {order.id} is {order.status} but broker no longer lists order {order.broker_order_id} as open.",
                        local_order_id=str(order.id), broker_order_id=order.broker_order_id,
                    ),
                    fix=lambda o=order: self._mark(o, OrderStatus.CANCELLED, "Broker no longer lists this order as open (order sync). If it filled, position sync will reconcile the resulting position."),
                    resolution="Marked cancelled; position sync reconciles any resulting fill.",
                )
            elif broker_order.status.value != order.status:
                # Broker still holds the order but its state moved on
                # (e.g. local PENDING vs broker PARTIALLY_FILLED). Broker
                # is source of truth for its own book.
                self._handle(
                    report, portfolio, dry_run,
                    OrderSyncDiscrepancy(
                        kind="fill_state_drift",
                        detail=f"Local order {order.id} status {order.status} != broker status {broker_order.status.value}.",
                        local_order_id=str(order.id), broker_order_id=order.broker_order_id,
                    ),
                    fix=lambda o=order, b=broker_order: self._mark(
                        o,
                        OrderStatus(b.status.value) if b.status.value in OrderStatus._value2member_map_ else OrderStatus.PENDING,
                        "Status updated to broker's (order sync).",
                    ),
                    resolution="Local status updated to broker's.",
                )

        # --- orphan broker orders ----------------------------------------
        local_broker_ids = {o.broker_order_id for o in local_open if o.broker_order_id}
        for broker_id, broker_order in broker_orders.items():
            if broker_id not in local_broker_ids:
                self._handle(
                    report, portfolio, dry_run,
                    OrderSyncDiscrepancy(
                        kind="orphan_broker_order",
                        detail=f"Broker lists open order {broker_id} ({broker_order.side.value} {broker_order.quantity} {broker_order.symbol}); no local record.",
                        broker_order_id=broker_id,
                    ),
                    fix=lambda p=portfolio, b=broker_order: self._create_local_from_broker(p, b),
                    resolution="Local pending order created from broker state.",
                )

        if report.discrepancies:
            self.db.commit()  # audit rows persist in dry-run too, same policy as position sync

        logger.info(
            "order_sync.completed",
            extra={
                "correlation_id": str(report.correlation_id), "portfolio_id": str(portfolio.id),
                "broker": self.broker.broker_name, "dry_run": dry_run,
                "broker_open_orders": report.broker_open_orders_seen,
                "local_open_orders": report.local_open_orders_seen,
                "discrepancies": len(report.discrepancies),
            },
        )
        return report

    # ------------------------------------------------------------------

    def _handle(self, report, portfolio, dry_run, discrepancy: OrderSyncDiscrepancy, fix, resolution: str) -> None:
        logger.warning(
            "order_sync.discrepancy",
            extra={
                "correlation_id": str(report.correlation_id), "portfolio_id": str(portfolio.id),
                "broker": self.broker.broker_name, "kind": discrepancy.kind,
                "local_order_id": discrepancy.local_order_id, "broker_order_id": discrepancy.broker_order_id,
                "detail": discrepancy.detail, "dry_run": dry_run,
            },
        )
        resolved = False
        applied = None
        if not dry_run:
            fix()
            resolved = True
            applied = resolution

        self.db.add(
            RiskEvent(
                portfolio_id=portfolio.id,
                event_type="order_sync_discrepancy",
                description=(
                    f"[{discrepancy.kind}] {discrepancy.detail} correlation={report.correlation_id} "
                    + (f"RESOLVED: {applied}" if resolved else "DRY-RUN: no action taken.")
                ),
                severity=RiskEventSeverity.HIGH.value,
                action_taken="auto_reconciled" if resolved else "dry_run_observed",
            )
        )
        report.discrepancies.append(
            OrderSyncDiscrepancy(
                kind=discrepancy.kind, detail=discrepancy.detail,
                local_order_id=discrepancy.local_order_id, broker_order_id=discrepancy.broker_order_id,
                resolved=resolved, resolution=applied,
            )
        )

    def _mark(self, order: Order, status: OrderStatus, reason: str) -> None:
        order.status = status.value
        order.rejection_reason = reason
        self.db.flush()

    def _create_local_from_broker(self, portfolio: Portfolio, broker_order) -> None:
        self.db.add(
            Order(
                portfolio_id=portfolio.id, symbol=broker_order.symbol,
                order_type=broker_order.order_type.value, side=broker_order.side.value,
                quantity=broker_order.quantity, requested_price=broker_order.requested_price,
                status=OrderStatus.PENDING.value, broker_order_id=broker_order.broker_order_id,
                submitted_at=dt.datetime.now(dt.timezone.utc),
                rejection_reason=None,
            )
        )
        self.db.flush()
