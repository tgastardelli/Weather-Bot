"""Tipos custom: Decimal como TEXT e datetime sempre UTC tz-aware.

SQLite não tem DECIMAL nem timezone — ver skill `python-backend`.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, String, TypeDecorator


class DecimalText(TypeDecorator[Decimal]):
    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Any) -> str | None:
        return None if value is None else str(value)

    def process_result_value(self, value: str | None, dialect: Any) -> Decimal | None:
        return None if value is None else Decimal(value)


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime()
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime naive proibido — use tz-aware UTC")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)
