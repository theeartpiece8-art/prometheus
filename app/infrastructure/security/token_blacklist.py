"""
Refresh-token / session revocation store.

Backs `POST /api/v1/auth/logout` ("Invalidate current session") and refresh
rotation. Redis is the production backend (03_System_Architecture.md lists
Redis for "Realtime sessions"); an in-memory fallback is used automatically
when Redis is unreachable (e.g. local dev without `docker compose up`, or
the automated test suite) so the app degrades gracefully rather than
hard-failing auth for an unrelated infra dependency.
"""
import datetime as dt
import logging
from abc import ABC, abstractmethod

from app.config import get_settings

logger = logging.getLogger("prometheus.security")
settings = get_settings()


class TokenBlacklist(ABC):
    @abstractmethod
    def revoke(self, jti: str, ttl_seconds: int) -> None: ...

    @abstractmethod
    def is_revoked(self, jti: str) -> bool: ...


class InMemoryTokenBlacklist(TokenBlacklist):
    """Process-local fallback. Not shared across workers — fine for
    Sprint 1 / single-process dev and for tests, not for multi-worker
    production (use Redis there)."""

    def __init__(self) -> None:
        self._store: dict[str, dt.datetime] = {}

    def revoke(self, jti: str, ttl_seconds: int) -> None:
        self._store[jti] = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl_seconds)

    def is_revoked(self, jti: str) -> bool:
        expiry = self._store.get(jti)
        if expiry is None:
            return False
        if expiry < dt.datetime.now(dt.timezone.utc):
            del self._store[jti]
            return False
        return True


class RedisTokenBlacklist(TokenBlacklist):
    def __init__(self, redis_url: str) -> None:
        import redis as redis_lib

        self._redis = redis_lib.from_url(redis_url, decode_responses=True)
        self._redis.ping()  # fail fast if unreachable, caller falls back

    def revoke(self, jti: str, ttl_seconds: int) -> None:
        self._redis.setex(f"revoked_jti:{jti}", ttl_seconds, "1")

    def is_revoked(self, jti: str) -> bool:
        return self._redis.exists(f"revoked_jti:{jti}") == 1


_blacklist_instance: TokenBlacklist | None = None


def get_token_blacklist() -> TokenBlacklist:
    """FastAPI dependency. Lazily initializes and caches a singleton,
    trying Redis first (if enabled) and falling back to in-memory."""
    global _blacklist_instance
    if _blacklist_instance is not None:
        return _blacklist_instance

    if settings.REDIS_ENABLED and not settings.is_testing:
        try:
            _blacklist_instance = RedisTokenBlacklist(settings.REDIS_URL)
            logger.info("Token blacklist backend: Redis")
            return _blacklist_instance
        except Exception as exc:  # noqa: BLE001 — intentionally broad: any Redis
            # failure must not prevent the API from starting.
            logger.warning("Redis unavailable (%s); falling back to in-memory token blacklist.", exc)

    _blacklist_instance = InMemoryTokenBlacklist()
    logger.info("Token blacklist backend: in-memory")
    return _blacklist_instance


def reset_token_blacklist_for_tests() -> None:
    global _blacklist_instance
    _blacklist_instance = InMemoryTokenBlacklist()
