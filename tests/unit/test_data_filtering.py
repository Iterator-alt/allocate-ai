"""Unit tests for Data Filtering Service."""

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import NielsenSpend, YouGovKPI, IndustryMap
from src.services.mediamix import DataFilteringService


class TestDataFilteringService:
    """Tests for DataFilteringService."""

    @pytest_asyncio.fixture
    async def service(self, db_session: AsyncSession) -> DataFilteringService:
        return DataFilteringService(db_session)

    @pytest_asyncio.fixture
    async def sample_data(self, db_session: AsyncSession):
        """Create sample Nielsen and YouGov data."""
        # Industry mapping
        db_session.add(IndustryMap(
            wirtschaftsgruppe="PKW",
            sector_label="Automotive",
            is_active=True
        ))

        # Nielsen data - BMW
        for month in range(1, 7):
            db_session.add(NielsenSpend(
                brand_name="BMW AG",
                wirtschaftsgruppe="PKW",
                year=2023,
                month=month,
                channel="TV",
                spend_eur=Decimal("100000") + Decimal(month * 5000)
            ))
            db_session.add(NielsenSpend(
                brand_name="BMW AG",
                wirtschaftsgruppe="PKW",
                year=2023,
                month=month,
                channel="Digital",
                spend_eur=Decimal("50000") + Decimal(month * 2000)
            ))

        # Nielsen data - Mercedes
        for month in range(1, 7):
            db_session.add(NielsenSpend(
                brand_name="Mercedes-Benz",
                wirtschaftsgruppe="PKW",
                year=2023,
                month=month,
                channel="TV",
                spend_eur=Decimal("120000") + Decimal(month * 6000)
            ))
            db_session.add(NielsenSpend(
                brand_name="Mercedes-Benz",
                wirtschaftsgruppe="PKW",
                year=2023,
                month=month,
                channel="Digital",
                spend_eur=Decimal("60000") + Decimal(month * 3000)
            ))

        # YouGov data - significant increase each month for trend detection
        for month in range(1, 7):
            db_session.add(YouGovKPI(
                brand_label="BMW",
                sector="Automotive",
                year=2023,
                month=month,
                adaware=Decimal("40.0") + Decimal(month * 2)  # 42, 44, 46, 48, 50, 52 - clear uptrend
            ))
            db_session.add(YouGovKPI(
                brand_label="Mercedes",
                sector="Automotive",
                year=2023,
                month=month,
                adaware=Decimal("50.0") + Decimal(month * 0.5)
            ))

        await db_session.commit()

    async def test_build_data_context(
        self, service: DataFilteringService, sample_data, db_session: AsyncSession
    ):
        """Test building complete data context."""
        result = await service.build_data_context(
            nielsen_brands=["BMW AG", "Mercedes-Benz"],
            yougov_brands=["BMW", "Mercedes"],
            wirtschaftsgruppe="PKW",
            kpi_name="adaware",
            year=2023,
        )

        assert result.year == 2023
        assert result.kpi_name == "adaware"
        assert len(result.competitor_spend_profiles) == 2
        assert len(result.competitor_kpi_profiles) == 2
        assert result.total_market_spend > 0
        assert len(result.all_channels) >= 2

    async def test_spend_profiles_calculated(
        self, service: DataFilteringService, sample_data, db_session: AsyncSession
    ):
        """Test that spend profiles are correctly calculated."""
        result = await service.build_data_context(
            nielsen_brands=["BMW AG"],
            yougov_brands=[],
            wirtschaftsgruppe="PKW",
            kpi_name="adaware",
            year=2023,
        )

        assert len(result.competitor_spend_profiles) == 1
        profile = result.competitor_spend_profiles[0]

        assert profile.nielsen_brand == "BMW AG"
        assert profile.total_spend_eur > 0
        assert profile.months_with_data == 6
        assert len(profile.channel_breakdown) == 2

        # Check percentages sum to ~100
        total_percentage = sum(ch.percentage_of_total for ch in profile.channel_breakdown)
        assert abs(total_percentage - 100.0) < 0.1

    async def test_kpi_profiles_with_trend(
        self, service: DataFilteringService, sample_data, db_session: AsyncSession
    ):
        """Test KPI profiles include trend calculation."""
        result = await service.build_data_context(
            nielsen_brands=[],
            yougov_brands=["BMW"],
            wirtschaftsgruppe="PKW",
            kpi_name="adaware",
            year=2023,
        )

        assert len(result.competitor_kpi_profiles) == 1
        profile = result.competitor_kpi_profiles[0]

        assert profile.yougov_brand_label == "BMW"
        assert profile.kpi_name == "adaware"
        assert profile.latest_value is not None
        assert profile.average_value is not None
        # Trend should be increasing since values increase each month
        assert profile.trend == "increasing"

    async def test_industry_benchmark(
        self, service: DataFilteringService, sample_data, db_session: AsyncSession
    ):
        """Test industry benchmark calculation."""
        result = await service.build_data_context(
            nielsen_brands=["BMW AG", "Mercedes-Benz"],
            yougov_brands=["BMW", "Mercedes"],
            wirtschaftsgruppe="PKW",
            kpi_name="adaware",
            year=2023,
        )

        assert result.industry_benchmark is not None
        benchmark = result.industry_benchmark

        assert benchmark.sector == "Automotive"
        assert "TV" in benchmark.top_channels
        assert len(benchmark.avg_spend_by_channel) >= 2
        assert benchmark.avg_kpi_value is not None

    async def test_format_for_prompt(
        self, service: DataFilteringService, sample_data, db_session: AsyncSession
    ):
        """Test formatting data for LLM prompt."""
        result = await service.build_data_context(
            nielsen_brands=["BMW AG"],
            yougov_brands=["BMW"],
            wirtschaftsgruppe="PKW",
            kpi_name="adaware",
            year=2023,
        )

        formatted = service.format_for_prompt(result)

        assert "COMPETITOR DATA" in formatted
        assert "BMW AG" in formatted
        assert "adaware" in formatted.lower()
        assert "€" in formatted

    async def test_channel_allocation_patterns(
        self, service: DataFilteringService, sample_data, db_session: AsyncSession
    ):
        """Test getting channel allocation patterns."""
        patterns = await service.get_channel_allocation_patterns(
            nielsen_brands=["BMW AG", "Mercedes-Benz"],
            year=2023,
        )

        assert "BMW AG" in patterns
        assert "Mercedes-Benz" in patterns
        assert "TV" in patterns["BMW AG"]
        assert "Digital" in patterns["BMW AG"]

        # Check percentages are valid
        bmw_total = sum(patterns["BMW AG"].values())
        assert abs(bmw_total - 100.0) < 0.1

    async def test_empty_brands_returns_empty_profiles(
        self, service: DataFilteringService, sample_data, db_session: AsyncSession
    ):
        """Test that empty brand lists return empty profiles."""
        result = await service.build_data_context(
            nielsen_brands=[],
            yougov_brands=[],
            wirtschaftsgruppe="PKW",
            kpi_name="adaware",
            year=2023,
        )

        assert len(result.competitor_spend_profiles) == 0
        assert len(result.competitor_kpi_profiles) == 0
        assert result.total_market_spend == 0
