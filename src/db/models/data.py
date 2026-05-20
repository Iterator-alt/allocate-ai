"""Data tables: Nielsen spend and YouGov KPI metrics."""

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin


class NielsenSpend(Base, TimestampMixin):
    """Nielsen advertising spend data by brand, channel, and month.

    Contains historical advertising expenditure data from Nielsen,
    broken down by brand, advertising channel, and time period.
    """

    __tablename__ = "nielsen_spend"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Brand identification
    brand_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    wirtschaftsgruppe: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Time period
    year: Mapped[int] = mapped_column(nullable=False, index=True)
    month: Mapped[int] = mapped_column(nullable=False)  # 1-12

    # Advertising channel
    channel: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Spend amount (in EUR)
    spend_eur: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # Original data reference
    source_file: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_nielsen_brand_year_month", "brand_name", "year", "month"),
        Index("ix_nielsen_wirtschaftsgruppe", "wirtschaftsgruppe"),
    )


class YouGovKPI(Base, TimestampMixin):
    """YouGov brand KPI metrics.

    Contains brand perception metrics from YouGov surveys:
    - adaware: Ad awareness (has the consumer seen ads for this brand)
    - aided: Aided brand awareness (recognizes brand when prompted)
    - consider: Purchase consideration (would consider buying)
    """

    __tablename__ = "yougov_kpi"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Brand identification
    brand_label: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sector: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Time period
    year: Mapped[int] = mapped_column(nullable=False, index=True)
    month: Mapped[int] = mapped_column(nullable=False)  # 1-12

    # KPI metrics (percentage values 0-100)
    adaware: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    aided: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    consider: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)

    # Original data reference
    source_file: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_yougov_brand_year_month", "brand_label", "year", "month"),
        Index("ix_yougov_sector", "sector"),
    )
