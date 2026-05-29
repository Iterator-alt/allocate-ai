"""Stage 2 (Part 1): Data Filtering Service.

This module builds the data context for LLM prompts:
1. Relationship table joining Nielsen spend and YouGov KPI uplift per brand per channel
2. Industry benchmarks from aggregated data
3. Warnings for brands missing data
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories import NielsenRepository, YouGovRepository

logger = logging.getLogger(__name__)


@dataclass
class BrandChannelRelationship:
    """Single row in the relationship table: brand + channel + spend + KPI uplift."""

    brand: str
    channel: str
    total_spend_eur: float
    kpi_earliest: Optional[float]
    kpi_latest: Optional[float]
    kpi_uplift_pp: Optional[float]  # percentage points, can be negative
    period_start: Optional[str]  # ISO date string
    period_end: Optional[str]  # ISO date string


@dataclass
class KPIUplift:
    """KPI uplift calculation for a brand."""

    brand: str
    kpi_name: str
    earliest_value: Optional[float]
    latest_value: Optional[float]
    uplift_pp: Optional[float]  # percentage points (latest - earliest)
    earliest_date: Optional[str]
    latest_date: Optional[str]


@dataclass
class IndustryBenchmark:
    """Industry-level benchmark data."""

    sector: str
    avg_spend_by_channel: Dict[str, Decimal]
    avg_kpi_value: Optional[Decimal]
    top_channels: List[str]  # Ordered by spend


@dataclass
class RelationshipTableResult:
    """Complete relationship table data for LLM prompt."""

    relationship_table: List[BrandChannelRelationship]
    kpi_uplifts: List[KPIUplift]  # One per brand
    industry_benchmark: Optional[IndustryBenchmark]
    all_channels: List[str]
    total_market_spend: float
    kpi_name: str
    warnings: List[str]


# Keep legacy dataclasses for backward compatibility
@dataclass
class ChannelSpend:
    """Spend data for a single channel."""

    channel: str
    total_spend_eur: Decimal
    percentage_of_total: float
    monthly_spend: Dict[int, Decimal] = field(default_factory=dict)


@dataclass
class CompetitorSpendProfile:
    """Nielsen spend profile for a competitor."""

    nielsen_brand: str
    total_spend_eur: Decimal
    channel_breakdown: List[ChannelSpend]
    months_with_data: int
    year: int


@dataclass
class CompetitorKPIProfile:
    """YouGov KPI profile for a competitor."""

    yougov_brand_label: str
    kpi_name: str
    time_series: List[Dict[str, Any]]  # [{year, month, value}, ...]
    latest_value: Optional[Decimal]
    average_value: Optional[Decimal]
    trend: Optional[str]  # "increasing", "decreasing", "stable" - DEPRECATED, use kpi_uplift_pp


@dataclass
class DataFilteringResult:
    """Complete filtered data for LLM prompt."""

    competitor_spend_profiles: List[CompetitorSpendProfile]
    competitor_kpi_profiles: List[CompetitorKPIProfile]
    industry_benchmark: Optional[IndustryBenchmark]
    all_channels: List[str]
    total_market_spend: Decimal
    year: int
    kpi_name: str
    # New: relationship table data
    relationship_table: Optional[List[BrandChannelRelationship]] = None
    kpi_uplifts: Optional[List[KPIUplift]] = None
    warnings: List[str] = field(default_factory=list)


class DataFilteringService:
    """Filters and transforms raw data for LLM consumption.

    Builds structured data matrices from Nielsen and YouGov data
    for the confirmed competitor set.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.nielsen_repo = NielsenRepository(session)
        self.yougov_repo = YouGovRepository(session)

    async def build_relationship_table(
        self,
        yougov_brands: List[str],
        nielsen_brands: List[str],
        kpi_name: str = "adaware",
    ) -> RelationshipTableResult:
        """Build the relationship table joining spend and KPI uplift per brand per channel.

        Args:
            yougov_brands: List of YouGov brand labels
            nielsen_brands: List of Nielsen brand names (should match yougov_brands)
            kpi_name: KPI metric (adaware, aided, consider)

        Returns:
            RelationshipTableResult with relationship table and metadata
        """
        relationship_rows: List[BrandChannelRelationship] = []
        kpi_uplifts: List[KPIUplift] = []
        warnings: List[str] = []
        total_spend = 0.0

        # Build a mapping from YouGov brand to Nielsen brand
        # For now, assume they're passed in parallel order or we try to match by name
        brand_pairs = self._match_brand_pairs(yougov_brands, nielsen_brands)

        for yougov_brand, nielsen_brand in brand_pairs:
            # Step 1: Calculate KPI uplift for this brand
            kpi_uplift = await self._calculate_kpi_uplift(yougov_brand, kpi_name)

            if kpi_uplift:
                kpi_uplifts.append(kpi_uplift)

            # Step 2: Check if we have Nielsen data for this brand
            if not nielsen_brand:
                warnings.append(f"Brand '{yougov_brand}' has YouGov data but no matching Nielsen brand - excluded from spend analysis")
                continue

            # Step 3: Get spend by channel for this Nielsen brand
            # Use filter_valid_only=False to get all years, we'll aggregate
            channel_spend = await self.nielsen_repo.get_spend_by_channel(
                nielsen_brand, year=None, filter_valid_only=True
            )

            if not channel_spend:
                warnings.append(f"Brand '{nielsen_brand}' has no Nielsen spend data - excluded from relationship table")
                continue

            # Step 4: Create one row per channel for this brand
            for channel, spend_eur in channel_spend.items():
                spend_float = float(spend_eur)
                total_spend += spend_float

                relationship_rows.append(BrandChannelRelationship(
                    brand=yougov_brand,  # Use YouGov name as canonical
                    channel=channel,
                    total_spend_eur=spend_float,
                    kpi_earliest=kpi_uplift.earliest_value if kpi_uplift else None,
                    kpi_latest=kpi_uplift.latest_value if kpi_uplift else None,
                    kpi_uplift_pp=kpi_uplift.uplift_pp if kpi_uplift else None,
                    period_start=kpi_uplift.earliest_date if kpi_uplift else None,
                    period_end=kpi_uplift.latest_date if kpi_uplift else None,
                ))

        # Sort by total spend descending
        relationship_rows.sort(key=lambda r: r.total_spend_eur, reverse=True)

        # Get all available channels
        all_channels = await self.nielsen_repo.get_channels()

        # Get industry benchmark (simplified)
        benchmark = None  # Will be built separately if needed

        # DEBUG: Write KPI uplift debug info to file
        try:
            from src.config import get_settings
            if get_settings().stage1_debug_mode:
                import os
                import json
                debug_dir = "debug_output/kpi_debug"
                os.makedirs(debug_dir, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                debug_data = {
                    "timestamp": timestamp,
                    "kpi_name": kpi_name,
                    "brand_pairs": [(yg, nb) for yg, nb in brand_pairs],
                    "kpi_uplifts": [
                        {
                            "brand": kpi.brand,
                            "kpi_name": kpi.kpi_name,
                            "earliest_value": kpi.earliest_value,
                            "latest_value": kpi.latest_value,
                            "uplift_pp": kpi.uplift_pp,
                            "earliest_date": kpi.earliest_date,
                            "latest_date": kpi.latest_date,
                        }
                        for kpi in kpi_uplifts
                    ],
                    "warnings": warnings,
                }
                with open(f"{debug_dir}/{timestamp}_kpi_uplift.json", "w") as f:
                    json.dump(debug_data, f, indent=2)
                logger.info(f"[KPI_DEBUG] Wrote debug file: {debug_dir}/{timestamp}_kpi_uplift.json")
        except Exception as e:
            logger.warning(f"[KPI_DEBUG] Failed to write debug file: {e}")

        return RelationshipTableResult(
            relationship_table=relationship_rows,
            kpi_uplifts=kpi_uplifts,
            industry_benchmark=benchmark,
            all_channels=all_channels,
            total_market_spend=total_spend,
            kpi_name=kpi_name,
            warnings=warnings,
        )

    async def _calculate_kpi_uplift(
        self,
        brand_label: str,
        kpi_name: str,
    ) -> Optional[KPIUplift]:
        """Calculate KPI uplift as (latest - earliest) for a brand.

        Returns actual numeric uplift in percentage points.
        Negative values are valid (e.g., sector-wide awareness decline).
        """
        # Get full time series (all valid years)
        time_series = await self.yougov_repo.get_kpi_time_series(
            brand_label, kpi_name, year=None, filter_valid_only=True
        )

        logger.info(f"[KPI_DEBUG] Brand: {brand_label}, KPI: {kpi_name}, Time series points: {len(time_series) if time_series else 0}")

        if not time_series or len(time_series) < 2:
            logger.warning(f"[KPI_DEBUG] Brand {brand_label}: Insufficient time series data")
            return None

        # Filter out None values
        valid_points = [ts for ts in time_series if ts["value"] is not None]

        if len(valid_points) < 2:
            logger.warning(f"[KPI_DEBUG] Brand {brand_label}: Insufficient valid points after filtering")
            return None

        # Time series is already ordered by date (ascending)
        earliest = valid_points[0]
        latest = valid_points[-1]

        earliest_value = float(earliest["value"])
        latest_value = float(latest["value"])
        uplift_pp = round(latest_value - earliest_value, 2)

        # Format dates
        earliest_date = f"{earliest['year']}-{earliest['month']:02d}-01"
        latest_date = f"{latest['year']}-{latest['month']:02d}-01"

        logger.info(f"[KPI_DEBUG] Brand: {brand_label} | KPI: {kpi_name} | Earliest: {earliest_value:.2f} ({earliest_date}) | Latest: {latest_value:.2f} ({latest_date}) | Uplift: {uplift_pp:+.2f}pp")

        return KPIUplift(
            brand=brand_label,
            kpi_name=kpi_name,
            earliest_value=round(earliest_value, 2),
            latest_value=round(latest_value, 2),
            uplift_pp=uplift_pp,
            earliest_date=earliest_date,
            latest_date=latest_date,
        )

    def _match_brand_pairs(
        self,
        yougov_brands: List[str],
        nielsen_brands: List[str],
    ) -> List[Tuple[str, Optional[str]]]:
        """Match YouGov brands to Nielsen brands.

        The lists are assumed to come pre-matched from Stage 1 (same order),
        but we also do fuzzy matching as fallback.

        Returns list of (yougov_brand, nielsen_brand or None) tuples.
        """
        pairs = []
        nielsen_lower_map = {b.lower(): b for b in nielsen_brands if b}
        nielsen_used = set()  # Track which Nielsen brands have been matched

        for i, yg_brand in enumerate(yougov_brands):
            yg_lower = yg_brand.lower()

            # Strategy 1: If lists are parallel and same length, try positional match
            if i < len(nielsen_brands) and nielsen_brands[i]:
                pairs.append((yg_brand, nielsen_brands[i]))
                nielsen_used.add(nielsen_brands[i].lower())
                continue

            # Strategy 2: Try exact case-insensitive match
            if yg_lower in nielsen_lower_map and yg_lower not in nielsen_used:
                pairs.append((yg_brand, nielsen_lower_map[yg_lower]))
                nielsen_used.add(yg_lower)
                continue

            # Strategy 3: Try fuzzy matching (substring, word match)
            matched = None
            yg_words = set(yg_lower.split())

            for nielsen_lower, nielsen_original in nielsen_lower_map.items():
                if nielsen_lower in nielsen_used:
                    continue

                # Check if Nielsen name is contained in YouGov name or vice versa
                if nielsen_lower in yg_lower or yg_lower in nielsen_lower:
                    matched = nielsen_original
                    nielsen_used.add(nielsen_lower)
                    break

                # Check if any word matches (e.g., "EHRMANN" matches "Ehrmann Almighurt")
                nielsen_words = set(nielsen_lower.split())
                if yg_words & nielsen_words:  # Intersection of word sets
                    matched = nielsen_original
                    nielsen_used.add(nielsen_lower)
                    break

                # Check if Nielsen name is a prefix of any word in YouGov name
                for word in yg_words:
                    if word.startswith(nielsen_lower) or nielsen_lower.startswith(word):
                        matched = nielsen_original
                        nielsen_used.add(nielsen_lower)
                        break
                if matched:
                    break

            pairs.append((yg_brand, matched))

        return pairs

    def format_relationship_table_for_prompt(self, result: RelationshipTableResult) -> str:
        """Format the relationship table as a compact text block for the LLM prompt.

        Creates a structured, data-dense representation showing the actual
        spend-to-KPI relationships that the LLM should use for allocation decisions.
        """
        lines = []

        # Header
        lines.append(f"=== COMPETITOR SPEND & KPI DATA ===")
        lines.append(f"KPI Metric: {result.kpi_name}")
        lines.append(f"Total Market Spend: €{result.total_market_spend:,.0f}")
        lines.append("")

        # KPI Uplift Summary (one row per brand)
        lines.append("--- KPI UPLIFT BY BRAND ---")
        lines.append("(Negative values indicate sector-wide decline, which is normal)")
        lines.append("")
        for kpi in result.kpi_uplifts:
            uplift_str = f"{kpi.uplift_pp:+.2f}pp" if kpi.uplift_pp is not None else "N/A"
            lines.append(
                f"{kpi.brand}: {kpi.earliest_value:.1f} → {kpi.latest_value:.1f} ({uplift_str}) "
                f"[{kpi.earliest_date} to {kpi.latest_date}]"
            )
        lines.append("")

        # Relationship Table (brand x channel x spend)
        lines.append("--- SPEND BY BRAND BY CHANNEL ---")
        lines.append("Format: Brand | Channel | Spend (EUR) | KPI Uplift")
        lines.append("")

        # Group by brand for readability
        current_brand = None
        for row in result.relationship_table:
            if row.brand != current_brand:
                if current_brand is not None:
                    lines.append("")  # Blank line between brands
                current_brand = row.brand
                lines.append(f"** {row.brand} **")

            uplift_str = f"{row.kpi_uplift_pp:+.2f}pp" if row.kpi_uplift_pp is not None else "N/A"
            lines.append(f"  {row.channel}: €{row.total_spend_eur:,.0f} | {uplift_str}")

        lines.append("")

        # Warnings
        if result.warnings:
            lines.append("--- DATA WARNINGS ---")
            for warning in result.warnings:
                lines.append(f"⚠ {warning}")

        return "\n".join(lines)

    # =========================================================================
    # Legacy methods for backward compatibility
    # =========================================================================

    async def build_data_context(
        self,
        nielsen_brands: List[str],
        yougov_brands: List[str],
        wirtschaftsgruppe: str,
        kpi_name: str = "adaware",
        year: Optional[int] = None,
    ) -> DataFilteringResult:
        """Build complete data context for LLM prompt.

        Args:
            nielsen_brands: List of Nielsen brand names
            yougov_brands: List of YouGov brand labels
            wirtschaftsgruppe: Industry classification
            kpi_name: KPI metric (adaware, aided, consider)
            year: Year to analyze (defaults to most recent)

        Returns:
            DataFilteringResult with all filtered data
        """
        # Determine year if not specified
        if year is None:
            min_year, max_year = await self.nielsen_repo.get_year_range()
            year = max_year if max_year else datetime.now().year - 1

        # Build competitor spend profiles (legacy)
        spend_profiles = await self._build_spend_profiles(nielsen_brands, year)

        # Build competitor KPI profiles (legacy)
        kpi_profiles = await self._build_kpi_profiles(yougov_brands, kpi_name, year)

        # Build industry benchmark
        benchmark = await self._build_industry_benchmark(
            wirtschaftsgruppe, kpi_name, year
        )

        # Get all available channels
        all_channels = await self.nielsen_repo.get_channels()

        # Calculate total market spend
        total_market_spend = sum(
            profile.total_spend_eur for profile in spend_profiles
        )

        # NEW: Also build relationship table
        relationship_result = await self.build_relationship_table(
            yougov_brands=yougov_brands,
            nielsen_brands=nielsen_brands,
            kpi_name=kpi_name,
        )

        return DataFilteringResult(
            competitor_spend_profiles=spend_profiles,
            competitor_kpi_profiles=kpi_profiles,
            industry_benchmark=benchmark,
            all_channels=all_channels,
            total_market_spend=total_market_spend,
            year=year,
            kpi_name=kpi_name,
            relationship_table=relationship_result.relationship_table,
            kpi_uplifts=relationship_result.kpi_uplifts,
            warnings=relationship_result.warnings,
        )

    async def _build_spend_profiles(
        self,
        nielsen_brands: List[str],
        year: int,
    ) -> List[CompetitorSpendProfile]:
        """Build spend profiles for each Nielsen brand."""
        profiles = []

        for brand_name in nielsen_brands:
            # Get spend by channel
            channel_spend = await self.nielsen_repo.get_spend_by_channel(
                brand_name, year
            )

            if not channel_spend:
                continue

            total_spend = sum(channel_spend.values())

            # Get spend matrix for monthly breakdown
            spend_matrix = await self.nielsen_repo.get_spend_matrix([brand_name], year)

            # Build monthly spend by channel
            channel_monthly: Dict[str, Dict[int, Decimal]] = {}
            for record in spend_matrix:
                channel = record["channel"]
                month = record["month"]
                spend = record["spend"]

                if channel not in channel_monthly:
                    channel_monthly[channel] = {}
                channel_monthly[channel][month] = spend

            # Build channel breakdown
            channel_breakdown = []
            for channel, spend in sorted(
                channel_spend.items(), key=lambda x: x[1], reverse=True
            ):
                percentage = (
                    float(spend / total_spend * 100) if total_spend > 0 else 0.0
                )
                channel_breakdown.append(
                    ChannelSpend(
                        channel=channel,
                        total_spend_eur=spend,
                        percentage_of_total=round(percentage, 2),
                        monthly_spend=channel_monthly.get(channel, {}),
                    )
                )

            # Count months with data
            months_with_data = len(
                set(record["month"] for record in spend_matrix)
            )

            profiles.append(
                CompetitorSpendProfile(
                    nielsen_brand=brand_name,
                    total_spend_eur=total_spend,
                    channel_breakdown=channel_breakdown,
                    months_with_data=months_with_data,
                    year=year,
                )
            )

        # Sort by total spend descending
        profiles.sort(key=lambda p: p.total_spend_eur, reverse=True)

        return profiles

    async def _build_kpi_profiles(
        self,
        yougov_brands: List[str],
        kpi_name: str,
        year: int,
    ) -> List[CompetitorKPIProfile]:
        """Build KPI profiles for each YouGov brand."""
        profiles = []

        for brand_label in yougov_brands:
            # Get KPI time series
            time_series = await self.yougov_repo.get_kpi_time_series(
                brand_label, kpi_name, year
            )

            if not time_series:
                continue

            # Get latest value
            latest = await self.yougov_repo.get_latest_kpi(brand_label, kpi_name)
            latest_value = latest["value"] if latest else None

            # Calculate average
            values = [
                ts["value"]
                for ts in time_series
                if ts["value"] is not None
            ]
            average_value = (
                Decimal(sum(values)) / len(values)
                if values
                else None
            )

            # Calculate KPI uplift (replaces old trend)
            kpi_uplift = await self._calculate_kpi_uplift(brand_label, kpi_name)

            # Map uplift to trend label for backward compatibility
            trend = None
            if kpi_uplift and kpi_uplift.uplift_pp is not None:
                if kpi_uplift.uplift_pp > 2:
                    trend = "increasing"
                elif kpi_uplift.uplift_pp < -2:
                    trend = "decreasing"
                else:
                    trend = "stable"

            profiles.append(
                CompetitorKPIProfile(
                    yougov_brand_label=brand_label,
                    kpi_name=kpi_name,
                    time_series=time_series,
                    latest_value=latest_value,
                    average_value=average_value,
                    trend=trend,
                )
            )

        # Sort by latest KPI value descending
        profiles.sort(
            key=lambda p: p.latest_value or Decimal("0"),
            reverse=True,
        )

        return profiles

    async def _build_industry_benchmark(
        self,
        wirtschaftsgruppe: str,
        kpi_name: str,
        year: int,
    ) -> Optional[IndustryBenchmark]:
        """Build industry benchmark from all competitors."""
        # Get all spend data for industry
        industry_data = await self.nielsen_repo.get_by_wirtschaftsgruppe(
            wirtschaftsgruppe, year
        )

        if not industry_data:
            return None

        # Aggregate spend by channel (mediengruppe)
        channel_totals: Dict[str, Decimal] = {}
        for record in industry_data:
            channel = record.mediengruppe
            if not channel:
                continue
            if channel not in channel_totals:
                channel_totals[channel] = Decimal("0")
            channel_totals[channel] += Decimal(str((record.teuro or 0) * 1000))

        # Sort channels by total spend
        sorted_channels = sorted(
            channel_totals.items(), key=lambda x: x[1], reverse=True
        )
        top_channels = [ch for ch, _ in sorted_channels[:5]]

        # Get sector for KPI average
        # PRISMA-ONLY MODE: Skip industry_map table lookup, use wirtschaftsgruppe as sector
        sector_label = wirtschaftsgruppe  # Use industry name directly

        avg_kpi = None
        if sector_label:
            avg_kpi = await self.yougov_repo.get_sector_average(
                sector_label, kpi_name, year
            )

        return IndustryBenchmark(
            sector=sector_label or wirtschaftsgruppe,
            avg_spend_by_channel=channel_totals,
            avg_kpi_value=avg_kpi,
            top_channels=top_channels,
        )

    async def get_channel_allocation_patterns(
        self,
        nielsen_brands: List[str],
        year: int,
    ) -> Dict[str, Dict[str, float]]:
        """Get channel allocation patterns for competitors.

        Returns a dict of brand -> channel -> percentage.
        Useful for showing common allocation patterns.
        """
        patterns = {}

        for brand_name in nielsen_brands:
            channel_spend = await self.nielsen_repo.get_spend_by_channel(
                brand_name, year
            )

            if not channel_spend:
                continue

            total = sum(channel_spend.values())
            if total > 0:
                patterns[brand_name] = {
                    channel: round(float(spend / total * 100), 2)
                    for channel, spend in channel_spend.items()
                }

        return patterns

    def format_for_prompt(self, result: DataFilteringResult) -> str:
        """Format filtered data as a text block for the LLM prompt.

        NOW USES THE RELATIONSHIP TABLE FORMAT instead of separate profiles.
        """
        # If we have relationship table data, use the new format
        if result.relationship_table and result.kpi_uplifts:
            return self._format_relationship_table_prompt(result)

        # Fallback to legacy format
        return self._format_legacy_prompt(result)

    def _format_relationship_table_prompt(self, result: DataFilteringResult) -> str:
        """Format using the new relationship table structure."""
        lines = []

        # Header
        lines.append(f"=== COMPETITOR SPEND & KPI DATA ({result.year}) ===")
        lines.append(f"KPI Metric: {result.kpi_name}")
        lines.append(f"Total Market Spend: €{float(result.total_market_spend):,.0f}")
        lines.append("")

        # KPI Uplift Summary (one row per brand)
        lines.append("--- KPI UPLIFT BY BRAND ---")
        lines.append("(Negative values indicate sector-wide decline, which is normal)")
        lines.append("")
        for kpi in result.kpi_uplifts:
            if kpi.earliest_value is not None and kpi.latest_value is not None:
                uplift_str = f"{kpi.uplift_pp:+.2f}pp" if kpi.uplift_pp is not None else "N/A"
                lines.append(
                    f"{kpi.brand}: {kpi.earliest_value:.1f} → {kpi.latest_value:.1f} ({uplift_str}) "
                    f"[{kpi.earliest_date} to {kpi.latest_date}]"
                )
        lines.append("")

        # Relationship Table (brand x channel x spend)
        lines.append("--- SPEND BY BRAND BY CHANNEL ---")
        lines.append("")

        # Group by brand for readability
        current_brand = None
        for row in result.relationship_table:
            if row.brand != current_brand:
                if current_brand is not None:
                    lines.append("")  # Blank line between brands
                current_brand = row.brand
                # Find this brand's KPI uplift
                brand_kpi = next((k for k in result.kpi_uplifts if k.brand == row.brand), None)
                if brand_kpi and brand_kpi.uplift_pp is not None:
                    lines.append(f"** {row.brand} ** (KPI uplift: {brand_kpi.uplift_pp:+.2f}pp)")
                else:
                    lines.append(f"** {row.brand} **")

            lines.append(f"  {row.channel}: €{row.total_spend_eur:,.0f}")

        lines.append("")

        # Industry benchmark
        if result.industry_benchmark:
            lines.append("--- INDUSTRY BENCHMARK ---")
            lines.append(f"Sector: {result.industry_benchmark.sector}")
            lines.append(f"Top Channels: {', '.join(result.industry_benchmark.top_channels)}")
            if result.industry_benchmark.avg_kpi_value:
                lines.append(
                    f"Sector Avg {result.kpi_name}: "
                    f"{result.industry_benchmark.avg_kpi_value:.1f}"
                )
            lines.append("")

        # Warnings
        if result.warnings:
            lines.append("--- DATA WARNINGS ---")
            for warning in result.warnings:
                lines.append(f"* {warning}")

        return "\n".join(lines)

    def _format_legacy_prompt(self, result: DataFilteringResult) -> str:
        """Legacy format for backward compatibility."""
        lines = []

        # Header
        lines.append(f"=== COMPETITOR DATA ({result.year}) ===")
        lines.append(f"KPI Metric: {result.kpi_name}")
        lines.append(f"Total Market Spend: €{result.total_market_spend:,.2f}")
        lines.append("")

        # Competitor spend profiles
        lines.append("--- ADVERTISING SPEND BY COMPETITOR ---")
        for profile in result.competitor_spend_profiles:
            lines.append(f"\n{profile.nielsen_brand}:")
            lines.append(f"  Total Spend: €{profile.total_spend_eur:,.2f}")
            lines.append(f"  Data Coverage: {profile.months_with_data} months")
            lines.append("  Channel Breakdown:")
            for channel in profile.channel_breakdown[:5]:  # Top 5 channels
                lines.append(
                    f"    - {channel.channel}: "
                    f"€{channel.total_spend_eur:,.2f} "
                    f"({channel.percentage_of_total:.1f}%)"
                )

        lines.append("")

        # Competitor KPI profiles
        lines.append("--- BRAND KPI METRICS ---")
        for profile in result.competitor_kpi_profiles:
            lines.append(f"\n{profile.yougov_brand_label}:")
            lines.append(f"  Latest {profile.kpi_name}: {profile.latest_value}")
            lines.append(f"  Average: {profile.average_value:.1f}" if profile.average_value else "  Average: N/A")
            lines.append(f"  Trend: {profile.trend or 'Unknown'}")

        lines.append("")

        # Industry benchmark
        if result.industry_benchmark:
            lines.append("--- INDUSTRY BENCHMARK ---")
            lines.append(f"Sector: {result.industry_benchmark.sector}")
            lines.append(f"Top Channels: {', '.join(result.industry_benchmark.top_channels)}")
            if result.industry_benchmark.avg_kpi_value:
                lines.append(
                    f"Sector Avg {result.kpi_name}: "
                    f"{result.industry_benchmark.avg_kpi_value:.1f}"
                )

        return "\n".join(lines)
