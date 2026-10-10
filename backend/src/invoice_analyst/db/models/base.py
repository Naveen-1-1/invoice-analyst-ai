"""Declarative base and shared column types."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Any

from sqlalchemy import CHAR, BigInteger, CheckConstraint, DateTime, Identity, MetaData, Numeric, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


# Primary key, money, quantities, currency codes, timestamps
PK = Annotated[int, mapped_column(BigInteger, Identity(), primary_key=True)]
Money = Annotated[Decimal, mapped_column(Numeric(18, 4))]
Qty = Annotated[Decimal, mapped_column(Numeric(18, 6))]
Ccy = Annotated[str, mapped_column(CHAR(3))]
Created = Annotated[dt.datetime, mapped_column(DateTime(timezone=True), server_default=func.now())]


def one_of(column: str, values: tuple[str, ...], name: str | None = None) -> CheckConstraint:
    """CHECK that a text column holds one of `values` (used instead of Postgres enum types)."""
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name or column)
