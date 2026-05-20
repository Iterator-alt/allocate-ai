"""Mapping tables: Industry and brand mappings."""

from typing import Optional

from sqlalchemy import String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin


class IndustryMap(Base, TimestampMixin):
    """Industry mapping from Nielsen Wirtschaftsgruppe to YouGov sector labels.

    Maps German industry classifications (Wirtschaftsgruppe) from Nielsen data
    to the sector labels used in YouGov data, enabling cross-dataset queries.
    """

    __tablename__ = "industry_map"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Nielsen classification (German)
    wirtschaftsgruppe: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    # YouGov sector label
    sector_label: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Optional description or notes
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Flag for active/deprecated mappings
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class BrandMap(Base, TimestampMixin):
    """Brand mapping from Nielsen brand names to YouGov brand labels.

    Maps brand names as they appear in Nielsen data to the corresponding
    brand labels in YouGov data. Handles naming variations and aliases.
    """

    __tablename__ = "brand_map"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Nielsen brand name
    nielsen_brand: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # YouGov brand label
    yougov_brand_label: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Industry context (optional, for disambiguation)
    wirtschaftsgruppe: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Confidence score for the mapping (0-1)
    confidence: Mapped[Optional[float]] = mapped_column(nullable=True)

    # Flag for active/deprecated mappings
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Notes about the mapping
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
