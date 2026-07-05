"""
Position Synchronization Engine (Sprint 4 continuation, Module 5).

Compares the broker's positions (source of truth for LIVE state -- the
broker's book is what's real when money is involved) against the local
database, detects every class of drift, and reconciles with a full audit
trail. Never overwrites blindly: every change records previous state,
new state, and reason, both as a structured log line and as a RiskEvent
row (the platform's existing immutable audit vehicle -- reusing it keeps
"one audit trail" true rather than inventing a parallel events table).

Drift classes detected (per the Module 5 spec):
- orphan_broker_position: broker has it, local doesn't (e.g. a fill that
  happened while we were down, or a position opened outside the platform)
- orphan_local_position: local open position the broker no longer has
  (e.g. server-side SL/TP hit while disconnected)
- quantity_mismatch, average_price_mismatch, direction_mismatch

Reconciliation policy (auto mode):
- orphan_broker -> create the local position from broker data
- orphan_local  -> close the local position at the broker's current tick
  (clearly audited as a reconciliation close, NOT a normal trade close --
  the true exit price at the broker is unknowable after the fact, and the
  audit record says so)
- quantity/avg-price mismatch -> local updated to broker values
- direction_mismatch -> local closed (reconciliation close) and recreated
  from broker data

Idempotency guarantee: a second sync immediately after a successful sync
finds zero discrepancies and changes nothing -- verified by test. Dry-run
mode performs full detection and reporting with zero writes.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.broker.broker_interface import BrokerAdapter
from app.domain.broker.broker_models import BrokerPosition
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.models.enums import PositionStatus, RiskEventSeverity
from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.models.position import Position
from app.infrastructure.models.risk_event import RiskEvent
from app.infrastructure.repositories.position_repository import PositionRepository

logger = get_logger("position_sync")

# Numeric(20,8) is the storage precision for quantities/prices; two values
# that agree at 8 decimal places ARE equal as far as the system can know.
_PRECISION = Decimal("0.00000001")


@dataclass(frozen=True)
class SyncDiscrepancy:
    kind: str  # orphan_broker_position | orphan_local_position | quantity_mismatch | average_price_mismatch | direction_mismatch
    symbol: str
    detail: str
    local_value: str | None = None
    broker_value: str | None = None
    resolved: bool = False
    resolution: str | None = None


@dataclass
class PositionSyncReport:
    correlation_id: uuid.UUID = field(default_factory=uuid.uuid4)
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    dry_run: bool = False
    broker_positions_seen: int = 0
    local_positions_seen: int = 0
    discrepancies: list[SyncDiscrepancy] = field(default_factory=list)

    @property
    def in_sync(self) -> bool:
        return not self.discrepancies


class PositionSyncEngine:
    def __init__(self, db: Session, broker: BrokerAdapter) -> None:
        self.db = db
        self.broker = broker
        self.positions = PositionRepository(db)

    def sync(self, portfolio: Portfolio, *, dry_run: bool = False) -> PositionSyncReport:
        report = PositionSyncReport(dry_run=dry_run)

        broker_positions = {p.symbol: p for p in self.broker.get_positions()}
        local_positions = {p.symbol: p for p in self.positions.list_open_for_portfolio(portfolio.id)}
        report.broker_positions_seen = len(broker_positions)
        report.local_positions_seen = len(local_positions)

        # --- orphan broker positions (broker has, local doesn't) -------
        for symbol, bpos in broker_positions.items():
            if symbol not in local_positions:
                self._handle(
                    report, portfolio, dry_run,
                    SyncDiscrepancy(
                        kind="orphan_broker_position", symbol=symbol,
                        detail=f"Broker holds {bpos.direction.value} {bpos.quantity} {symbol} @ {bpos.average_price}; no open local position.",
                        local_value=None,
                        broker_value=f"{bpos.direction.value} {bpos.quantity} @ {bpos.average_price}",
                    ),
                    fix=lambda bpos=bpos: self._create_local_from_broker(portfolio, bpos),
                    resolution="Local position created from broker state.",
                )

        # --- orphan local positions (local has, broker doesn't) --------
        for symbol, lpos in local_positions.items():
            if symbol not in broker_positions:
                self._handle(
                    report, portfolio, dry_run,
                    SyncDiscrepancy(
                        kind="orphan_local_position", symbol=symbol,
                        detail=f"Local open {lpos.direction} {lpos.quantity} {symbol}; broker holds no such position.",
                        local_value=f"{lpos.direction} {lpos.quantity} @ {lpos.average_price}",
                        broker_value=None,
                    ),
                    fix=lambda lpos=lpos: self._reconciliation_close(portfolio, lpos),
                    resolution="Local position closed at broker tick (reconciliation close; true broker exit price unknowable).",
                )

        # --- field-level drift on positions both sides hold ------------
        for symbol in set(broker_positions) & set(local_positions):
            bpos, lpos = broker_positions[symbol], local_positions[symbol]

            if bpos.direction.value != lpos.direction:
                self._handle(
                    report, portfolio, dry_run,
                    SyncDiscrepancy(
                        kind="direction_mismatch", symbol=symbol,
                        detail=f"Local is {lpos.direction}, broker is {bpos.direction.value}.",
                        local_value=lpos.direction, broker_value=bpos.direction.value,
                    ),
                    fix=lambda p=portfolio, l=lpos, b=bpos: (self._reconciliation_close(p, l), self._create_local_from_broker(p, b)),
                    resolution="Local closed (reconciliation) and recreated from broker state.",
                )
                continue  # quantity/price comparisons are meaningless across a direction flip

            if lpos.quantity.quantize(_PRECISION) != bpos.quantity.quantize(_PRECISION):
                self._handle(
                    report, portfolio, dry_run,
                    SyncDiscrepancy(
                        kind="quantity_mismatch", symbol=symbol,
                        detail=f"Local quantity {lpos.quantity} != broker quantity {bpos.quantity}.",
                        local_value=str(lpos.quantity), broker_value=str(bpos.quantity),
                    ),
                    fix=lambda l=lpos, b=bpos: self._update_local_field(l, "quantity", b.quantity),
                    resolution="Local quantity updated to broker value.",
                )

            if lpos.average_price.quantize(_PRECISION) != bpos.average_price.quantize(_PRECISION):
                self._handle(
                    report, portfolio, dry_run,
                    SyncDiscrepancy(
                        kind="average_price_mismatch", symbol=symbol,
                        detail=f"Local average price {lpos.average_price} != broker {bpos.average_price}.",
                        local_value=str(lpos.average_price), broker_value=str(bpos.average_price),
                    ),
                    fix=lambda l=lpos, b=bpos: self._update_local_field(l, "average_price", b.average_price),
                    resolution="Local average price updated to broker value.",
                )

        if report.discrepancies:
            # Committed in BOTH modes: the audit rows (RiskEvents) must
            # persist even for dry-run observations -- per the spec, every
            # mismatch generates an audit record. In dry-run no fixes were
            # applied, so this persists only the audit trail.
            self.db.commit()

        logger.info(
            "position_sync.completed",
            extra={
                "correlation_id": str(report.correlation_id), "portfolio_id": str(portfolio.id),
                "broker": self.broker.broker_name, "dry_run": dry_run,
                "broker_positions": report.broker_positions_seen, "local_positions": report.local_positions_seen,
                "discrepancies": len(report.discrepancies),
            },
        )
        return report

    # ------------------------------------------------------------------

    def _handle(self, report, portfolio, dry_run, discrepancy: SyncDiscrepancy, fix, resolution: str) -> None:
        """Log + audit every discrepancy; apply the fix unless dry_run."""
        logger.warning(
            "position_sync.discrepancy",
            extra={
                "correlation_id": str(report.correlation_id), "portfolio_id": str(portfolio.id),
                "broker": self.broker.broker_name, "kind": discrepancy.kind, "symbol": discrepancy.symbol,
                "previous_state": discrepancy.local_value, "new_state": discrepancy.broker_value,
                "detail": discrepancy.detail, "dry_run": dry_run,
            },
        )
        resolved = False
        applied_resolution = None
        if not dry_run:
            fix()
            resolved = True
            applied_resolution = resolution

        # Audit row -- one per discrepancy, resolved or observed-only.
        self.db.add(
            RiskEvent(
                portfolio_id=portfolio.id,
                event_type="position_sync_discrepancy",
                description=(
                    f"[{discrepancy.kind}] {discrepancy.symbol}: {discrepancy.detail} "
                    f"local={discrepancy.local_value} broker={discrepancy.broker_value} "
                    f"correlation={report.correlation_id} "
                    + (f"RESOLVED: {applied_resolution}" if resolved else "DRY-RUN: no action taken.")
                ),
                severity=RiskEventSeverity.HIGH.value,
                action_taken="auto_reconciled" if resolved else "dry_run_observed",
            )
        )
        report.discrepancies.append(
            SyncDiscrepancy(
                kind=discrepancy.kind, symbol=discrepancy.symbol, detail=discrepancy.detail,
                local_value=discrepancy.local_value, broker_value=discrepancy.broker_value,
                resolved=resolved, resolution=applied_resolution,
            )
        )

    def _create_local_from_broker(self, portfolio: Portfolio, bpos: BrokerPosition) -> None:
        self.db.add(
            Position(
                portfolio_id=portfolio.id, symbol=bpos.symbol, direction=bpos.direction.value,
                quantity=bpos.quantity, average_price=bpos.average_price,
                current_price=bpos.current_price or bpos.average_price,
                stop_loss=bpos.stop_loss, take_profit=bpos.take_profit,
                opened_at=dt.datetime.now(dt.timezone.utc), status=PositionStatus.OPEN.value,
            )
        )
        self.db.flush()

    def _reconciliation_close(self, portfolio: Portfolio, lpos: Position) -> None:
        """Close a local position the broker no longer has. The genuine
        exit price at the broker is unknowable after the fact; the
        broker's current tick is the best available estimate and the
        audit record says exactly that. Realized P&L is applied to the
        portfolio balance from that estimate."""
        try:
            tick = self.broker.get_tick(lpos.symbol)
            close_price = tick.bid if lpos.direction == "long" else tick.ask
        except Exception:  # tick unavailable: close at last known local price
            close_price = lpos.current_price or lpos.average_price

        if lpos.direction == "long":
            realized = (close_price - lpos.average_price) * lpos.quantity
        else:
            realized = (lpos.average_price - close_price) * lpos.quantity

        lpos.realized_pnl += realized
        portfolio.balance += realized
        lpos.current_price = close_price
        lpos.quantity = Decimal("0")
        lpos.status = PositionStatus.CLOSED.value
        lpos.closed_at = dt.datetime.now(dt.timezone.utc)
        self.db.flush()

    def _update_local_field(self, lpos: Position, fieldname: str, value: Decimal) -> None:
        setattr(lpos, fieldname, value)
        self.db.flush()
