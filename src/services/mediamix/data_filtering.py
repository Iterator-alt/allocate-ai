"""Stage 2 (Part 1): Data Filtering Service.

This module builds the data context for LLM prompts:
1. Nielsen spend matrix for confirmed competitors
2. YouGov KPI time series for confirmed competitors
3. Channel allocation patterns from historical data
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Dict, Any
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories import NielsenRepository, YouGovRepository


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
    trend: Optional[str]  # "increasing", "decreasing", "stable"


@dataclass
class IndustryBenchmark:
    """Industry-level benchmark data."""

    sector: str
    avg_spend_by_channel: Dict[str, Decimal]
    avg_kpi_value: Optional[Decimal]
    top_channels: List[str]  # Ordered by spend


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


class DataFilteringService:
    """Filters and transforms raw data for LLM consumption.

    Builds structured data matrices from Nielsen and YouGov data
    for the confirmed competitor set.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.nielsen_repo = NielsenRepository(session)
        self.yougov_repo = YouGovRepository(session)

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

        # Build competitor spend profiles
        spend_profiles = await self._build_spend_profiles(nielsen_brands, year)

        # Build competitor KPI profiles
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

        return DataFilteringResult(
            competitor_spend_profiles=spend_profiles,
            competitor_kpi_profiles=kpi_profiles,
            industry_benchmark=benchmark,
            all_channels=all_channels,
            total_market_spend=total_market_spend,
            year=year,
            kpi_name=kpi_name,
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

            # Determine trend
            trend = self._calculate_trend(time_series)

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

    def _calculate_trend(
        self,
        time_series: List[Dict[str, Any]],
        min_points: int = 3,
    ) -> Optional[str]:
        """Calculate trend direction from time series.

        Uses simple linear regression on recent data points.
        """
        if len(time_series) < min_points:
            return None

        # Take last 6 months or available points
        recent = time_series[-6:]
        values = [ts["value"] for ts in recent if ts["value"] is not None]

        if len(values) < min_points:
            return None

        # Simple trend calculation: compare first half average to second half
        mid = len(values) // 2
        first_half_avg = sum(values[:mid]) / mid
        second_half_avg = sum(values[mid:]) / (len(values) - mid)

        diff_percentage = (
            (second_half_avg - first_half_avg) / first_half_avg * 100
            if first_half_avg > 0
            else 0
        )

        if diff_percentage > 5:
            return "increasing"
        elif diff_percentage < -5:
            return "decreasing"
        else:
            return "stable"

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

        # Aggregate spend by channel
        channel_totals: Dict[str, Decimal] = {}
        for record in industry_data:
            channel = record.channel
            if channel not in channel_totals:
                channel_totals[channel] = Decimal("0")
            channel_totals[channel] += record.spend_eur

        # Sort channels by total spend
        sorted_channels = sorted(
            channel_totals.items(), key=lambda x: x[1], reverse=True
        )
        top_channels = [ch for ch, _ in sorted_channels[:5]]

        # Get sector for KPI average
        from src.repositories import IndustryMapRepository

        industry_repo = IndustryMapRepository(self.session)
        sector_label = await industry_repo.get_sector_label(wirtschaftsgruppe)

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

        Creates a structured text representation of the data
        that can be included in the prompt.
        """
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
