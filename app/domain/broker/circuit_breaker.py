"""
Circuit Breaker for broker connectivity, per Sprint 4 plan module 9 and
10_Live_Trading_Engine.md's Kill Switch triggers ("Broker Disconnect,
Critical API Failure, Repeated Order Rejections").

Pure domain logic -- no DB, no network, no framework imports. The
decision of WHEN to trip lives here; WHAT happens on a trip (triggering
the existing RiskService kill switch, recording the error on the broker
account) is the application layer's job, supplied as a callback to the
MonitoredBrokerAdapter wrapper that feeds this breaker.

Design decision, stated explicitly: tripping is AUTOMATIC, resetting is
MANUAL. The Sprint 4 plan says "automatically stop live trading if..." --
stopping is automatic. But 07_Risk_Management_Engine.md's philosophy
("Require manual review before restarting", and the kill switch's own
manual reset) means a tripped breaker must never quietly re-arm itself
and resume live trading. reset() exists for the operator, not for a
timer. (The classic HALF_OPEN self-probing state is deliberately absent
for this reason.)
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum


class BreakerState(str, Enum):
    CLOSED = "closed"  # normal operation
    OPEN = "open"      # tripped: live order flow must stop


@dataclass(frozen=True)
class CircuitBreakerConfig:
    max_consecutive_failures: int = 3
    """Consecutive broker-call failures (connection errors, unexpected
    exceptions -- NOT business rejections like insufficient margin, which
    are the broker working correctly) before tripping."""
    max_latency_ms: float = 5000.0
    """A single broker call slower than this counts as a failure --
    'execution latency exceeds safe limits' (Sprint 4 plan)."""
    max_latency_violations: int = 3
    """Consecutive latency violations before tripping (a single slow call
    trips nothing on its own; a pattern does)."""


@dataclass
class CircuitBreaker:
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    consecutive_latency_violations: int = 0
    tripped_at: dt.datetime | None = None
    trip_reason: str | None = None

    @property
    def is_open(self) -> bool:
        return self.state == BreakerState.OPEN

    def record_success(self, latency_ms: float | None = None) -> None:
        """A successful call resets the failure streak. Latency is still
        checked: a call can succeed AND be dangerously slow."""
        self.consecutive_failures = 0
        if latency_ms is not None and latency_ms > self.config.max_latency_ms:
            self.consecutive_latency_violations += 1
            if self.consecutive_latency_violations >= self.config.max_latency_violations:
                self._trip(
                    f"Execution latency exceeded safe limits: {self.consecutive_latency_violations} consecutive "
                    f"calls over {self.config.max_latency_ms}ms (last: {latency_ms:.0f}ms)."
                )
        else:
            self.consecutive_latency_violations = 0

    def record_failure(self, reason: str) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.config.max_consecutive_failures:
            self._trip(
                f"{self.consecutive_failures} consecutive broker call failures. Last: {reason}"
            )

    def record_disconnect(self, reason: str = "Broker disconnected.") -> None:
        """An observed disconnect trips IMMEDIATELY -- no counting. Per
        10_Live_Trading_Engine.md's kill switch triggers, a broker
        disconnect during live trading is not a condition to tolerate
        three times."""
        self._trip(reason)

    def _trip(self, reason: str) -> None:
        if self.state == BreakerState.OPEN:
            return  # already tripped; keep the ORIGINAL reason and timestamp
        self.state = BreakerState.OPEN
        self.tripped_at = dt.datetime.now(dt.timezone.utc)
        self.trip_reason = reason

    def reset(self) -> None:
        """Operator-initiated only. Never called from any automatic path."""
        self.state = BreakerState.CLOSED
        self.consecutive_failures = 0
        self.consecutive_latency_violations = 0
        self.tripped_at = None
        self.trip_reason = None
