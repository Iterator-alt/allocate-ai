"""Data tables: Nielsen WizzAd and YouGov BrandIndex.

Schema based on Stage1_SearchFilter_Design.md specification.
These tables are populated from CSV files initially, then from APIs.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, Index, Numeric, String, Text, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin


class YouGov(Base, TimestampMixin):
    """YouGov BrandIndex data.

    PRIMARY data source - search YouGov FIRST, then Nielsen.

    Contains brand perception metrics from YouGov surveys:
    - adaware: Ad awareness
    - aware: Aided brand awareness
    - consider: Purchase consideration

    Schema from design doc:
    | Column | Type | Notes |
    | date | DATE | Parsed from CSV |
    | sector_label | TEXT | Indexed |
    | brand_label | TEXT | Indexed |
    | metric | TEXT | Values: adaware, aware, consider |
    | score | FLOAT | KPI value |
    """

    __tablename__ = "yougov"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Time period
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Brand identification
    sector_label: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    brand_label: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    # KPI metric type and value
    metric: Mapped[str] = mapped_column(Text, nullable=False, index=True)  # adaware, aware, consider
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Additional fields (stored, not used in Stage 1)
    analysis_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    region: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sector_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    brand_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Volume fields (stored, not used in Stage 1)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    positives: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    negatives: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    neutrals: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    positives_neutrals: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    negatives_neutrals: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("idx_yougov_sector", "sector_label"),
        Index("idx_yougov_brand", "brand_label"),
        Index("idx_yougov_metric", "metric"),
        Index("idx_yougov_date", "date"),
        Index("idx_yougov_sector_brand", "sector_label", "brand_label"),
    )


class Nielsen(Base, TimestampMixin):
    """Nielsen WizzAd advertising spend data.

    SECONDARY data source - search Nielsen AFTER YouGov.

    Contains advertising expenditure data broken down by:
    - Wirtschaftsgruppe (industry)
    - Marke (brand)
    - Mediengruppe (media channel)
    - Time period (Jahr/Monat)

    Schema from design doc:
    | Column | Type | Notes |
    | Wirtschaftsgruppe | TEXT | Indexed |
    | Konzern | TEXT | |
    | Firma | TEXT | |
    | Marke | TEXT | Indexed |
    | Produktmarke | TEXT | |
    | Jahr | INT | Indexed |
    | Monat | TEXT | e.g. "Januar", "Februar" |
    | Mediengruppe | TEXT | |
    | TEuro | FLOAT | Gross spend |
    """

    __tablename__ = "nielsen"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Industry classification
    wirtschaftsgruppe: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    # Company hierarchy
    konzern: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    firma: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    marke: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    produktmarke: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Time period
    jahr: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    monat: Mapped[str] = mapped_column(Text, nullable=False)  # German month name

    # Media channel and spend
    mediengruppe: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    teuro: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # Gross spend in thousands EUR

    __table_args__ = (
        Index("idx_nielsen_wg", "wirtschaftsgruppe"),
        Index("idx_nielsen_marke", "marke"),
        Index("idx_nielsen_jahr", "jahr"),
        Index("idx_nielsen_marke_jahr", "marke", "jahr"),
    )


# Keep old models for backward compatibility during migration
# These can be removed after migration is complete

class NielsenSpend(Base, TimestampMixin):
    """DEPRECATED: Use Nielsen table instead.

    Kept for backward compatibility during migration.
    """

    __tablename__ = "nielsen_spend"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    brand_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    wirtschaftsgruppe: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    year: Mapped[int] = mapped_column(nullable=False, index=True)
    month: Mapped[int] = mapped_column(nullable=False)
    channel: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    spend_eur: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    source_file: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_nielsen_brand_year_month", "brand_name", "year", "month"),
        Index("ix_nielsen_wirtschaftsgruppe", "wirtschaftsgruppe"),
    )


class YouGovKPI(Base, TimestampMixin):
    """DEPRECATED: Use YouGov table instead.

    Kept for backward compatibility during migration.
    """

    __tablename__ = "yougov_kpi"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    brand_label: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sector: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    year: Mapped[int] = mapped_column(nullable=False, index=True)
    month: Mapped[int] = mapped_column(nullable=False)
    adaware: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    aided: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    consider: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    source_file: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_yougov_brand_year_month", "brand_label", "year", "month"),
        Index("ix_yougov_sector", "sector"),
    )
