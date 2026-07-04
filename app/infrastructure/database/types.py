"""
Cross-dialect UUID column type.

04_Database_Design.md requires UUID primary keys everywhere. In production
this compiles to native PostgreSQL UUID. In the automated test suite
(SQLite, per 13_Testing_Strategy.md's requirement for fast, isolated,
dependency-free unit/integration tests) it transparently falls back to a
32-char hex CHAR column. Application code always works with `uuid.UUID`
objects regardless of backend.

This is the standard SQLAlchemy "backend-agnostic GUID type" recipe.
"""
import uuid

from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import CHAR, TypeDecorator


class GUID(TypeDecorator):
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return str(value)
        if not isinstance(value, uuid.UUID):
            return "%.32x" % uuid.UUID(str(value)).int
        return "%.32x" % value.int

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
