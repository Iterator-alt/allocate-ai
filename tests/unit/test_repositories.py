"""Tests for repository classes."""

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import NielsenSpend, YouGovKPI, IndustryMap, BrandMap
from src.repositories import (
    NielsenRepository,
    YouGovRepository,
    IndustryMapRepository,
    BrandMapRepository,
)


class TestNielsenRepository:
    """Tests for NielsenRepository."""

    @pytest_asyncio.fixture
    async def repo(self, db_session: AsyncSession) -> NielsenRepository:
        return NielsenRepository(db_session)

    @pytest_asyncio.fixture
    async def sample_data(self, db_session: AsyncSession):
        """Create sample Nielsen data."""
        records = [
            NielsenSpend(
                brand_name="BMW",
                wirtschaftsgruppe="Automotive",
                year=2023,
                month=1,
                channel="TV",
                spend_eur=Decimal("100000.00"),
            ),
            NielsenSpend(
                brand_name="BMW",
                wirtschaftsgruppe="Automotive",
                year=2023,
                month=1,
                channel="Digital",
                spend_eur=Decimal("50000.00"),
            ),
            NielsenSpend(
                brand_name="Mercedes",
                wirtschaftsgruppe="Automotive",
                year=2023,
                month=1,
                channel="TV",
                spend_eur=Decimal("120000.00"),
            ),
            NielsenSpend(
                brand_name="BMW",
                wirtschaftsgruppe="Automotive",
                year=2023,
                month=2,
                channel="TV",
                spend_eur=Decimal("110000.00"),
            ),
        ]
        for record in records:
            db_session.add(record)
        await db_session.commit()
        return records

    async def test_get_by_brand(
        self, repo: NielsenRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting spend data by brand."""
        results = await repo.get_by_brand("BMW")
        assert len(results) == 3
        assert all(r.brand_name == "BMW" for r in results)

    async def test_get_by_brand_with_year(
        self, repo: NielsenRepository, sample_data, db_session: AsyncSession
    ):
        """Test filtering by year."""
        results = await repo.get_by_brand("BMW", year=2023)
        assert len(results) == 3

    async def test_get_by_brand_with_month(
        self, repo: NielsenRepository, sample_data, db_session: AsyncSession
    ):
        """Test filtering by month."""
        results = await repo.get_by_brand("BMW", year=2023, month=1)
        assert len(results) == 2

    async def test_get_brands_in_industry(
        self, repo: NielsenRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting brands in an industry."""
        brands = await repo.get_brands_in_industry("Automotive")
        assert set(brands) == {"BMW", "Mercedes"}

    async def test_get_spend_by_channel(
        self, repo: NielsenRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting spend breakdown by channel."""
        spend = await repo.get_spend_by_channel("BMW", 2023, month=1)
        assert spend["TV"] == Decimal("100000.00")
        assert spend["Digital"] == Decimal("50000.00")

    async def test_get_total_spend(
        self, repo: NielsenRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting total spend."""
        total = await repo.get_total_spend("BMW", 2023, month=1)
        assert total == Decimal("150000.00")

    async def test_get_channels(
        self, repo: NielsenRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting distinct channels."""
        channels = await repo.get_channels()
        assert set(channels) == {"TV", "Digital"}


class TestYouGovRepository:
    """Tests for YouGovRepository."""

    @pytest_asyncio.fixture
    async def repo(self, db_session: AsyncSession) -> YouGovRepository:
        return YouGovRepository(db_session)

    @pytest_asyncio.fixture
    async def sample_data(self, db_session: AsyncSession):
        """Create sample YouGov data."""
        records = [
            YouGovKPI(
                brand_label="BMW",
                sector="Automotive",
                year=2023,
                month=1,
                adaware=Decimal("45.5"),
                aided=Decimal("78.2"),
                consider=Decimal("32.1"),
            ),
            YouGovKPI(
                brand_label="BMW",
                sector="Automotive",
                year=2023,
                month=2,
                adaware=Decimal("46.0"),
                aided=Decimal("79.0"),
                consider=Decimal("33.0"),
            ),
            YouGovKPI(
                brand_label="Mercedes",
                sector="Automotive",
                year=2023,
                month=1,
                adaware=Decimal("50.0"),
                aided=Decimal("82.0"),
                consider=Decimal("35.0"),
            ),
        ]
        for record in records:
            db_session.add(record)
        await db_session.commit()
        return records

    async def test_get_by_brand(
        self, repo: YouGovRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting KPI data by brand."""
        results = await repo.get_by_brand("BMW")
        assert len(results) == 2
        assert all(r.brand_label == "BMW" for r in results)

    async def test_get_brands_in_sector(
        self, repo: YouGovRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting brands in a sector."""
        brands = await repo.get_brands_in_sector("Automotive")
        assert set(brands) == {"BMW", "Mercedes"}

    async def test_get_kpi_time_series(
        self, repo: YouGovRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting KPI time series."""
        series = await repo.get_kpi_time_series("BMW", "adaware", year=2023)
        assert len(series) == 2
        assert series[0]["value"] == Decimal("45.5")
        assert series[1]["value"] == Decimal("46.0")

    async def test_get_latest_kpi(
        self, repo: YouGovRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting latest KPI value."""
        latest = await repo.get_latest_kpi("BMW", "adaware")
        assert latest["year"] == 2023
        assert latest["month"] == 2
        assert latest["value"] == Decimal("46.0")

    async def test_get_sector_average(
        self, repo: YouGovRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting sector average."""
        avg = await repo.get_sector_average("Automotive", "adaware", 2023, month=1)
        # (45.5 + 50.0) / 2 = 47.75
        assert float(avg) == pytest.approx(47.75, rel=0.01)

    async def test_invalid_kpi_name(
        self, repo: YouGovRepository, sample_data, db_session: AsyncSession
    ):
        """Test that invalid KPI name raises error."""
        with pytest.raises(ValueError):
            await repo.get_kpi_time_series("BMW", "invalid_kpi")


class TestIndustryMapRepository:
    """Tests for IndustryMapRepository."""

    @pytest_asyncio.fixture
    async def repo(self, db_session: AsyncSession) -> IndustryMapRepository:
        return IndustryMapRepository(db_session)

    @pytest_asyncio.fixture
    async def sample_data(self, db_session: AsyncSession):
        """Create sample industry mappings."""
        records = [
            IndustryMap(
                wirtschaftsgruppe="PKW",
                sector_label="Automotive",
                description="Personal vehicles",
                is_active=True,
            ),
            IndustryMap(
                wirtschaftsgruppe="Motorräder",
                sector_label="Automotive",
                description="Motorcycles",
                is_active=True,
            ),
            IndustryMap(
                wirtschaftsgruppe="Old Industry",
                sector_label="Deprecated",
                is_active=False,
            ),
        ]
        for record in records:
            db_session.add(record)
        await db_session.commit()
        return records

    async def test_get_by_wirtschaftsgruppe(
        self, repo: IndustryMapRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting mapping by Wirtschaftsgruppe."""
        mapping = await repo.get_by_wirtschaftsgruppe("PKW")
        assert mapping is not None
        assert mapping.sector_label == "Automotive"

    async def test_get_sector_label(
        self, repo: IndustryMapRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting sector label."""
        label = await repo.get_sector_label("PKW")
        assert label == "Automotive"

    async def test_inactive_not_returned(
        self, repo: IndustryMapRepository, sample_data, db_session: AsyncSession
    ):
        """Test that inactive mappings are not returned."""
        mapping = await repo.get_by_wirtschaftsgruppe("Old Industry")
        assert mapping is None

    async def test_get_all_active(
        self, repo: IndustryMapRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting all active mappings."""
        mappings = await repo.get_all_active()
        assert len(mappings) == 2
        assert all(m.is_active for m in mappings)


class TestBrandMapRepository:
    """Tests for BrandMapRepository."""

    @pytest_asyncio.fixture
    async def repo(self, db_session: AsyncSession) -> BrandMapRepository:
        return BrandMapRepository(db_session)

    @pytest_asyncio.fixture
    async def sample_data(self, db_session: AsyncSession):
        """Create sample brand mappings."""
        records = [
            BrandMap(
                nielsen_brand="BMW AG",
                yougov_brand_label="BMW",
                wirtschaftsgruppe="Automotive",
                confidence=0.95,
                is_active=True,
            ),
            BrandMap(
                nielsen_brand="Mercedes-Benz",
                yougov_brand_label="Mercedes",
                wirtschaftsgruppe="Automotive",
                confidence=0.90,
                is_active=True,
            ),
            BrandMap(
                nielsen_brand="Old Brand",
                yougov_brand_label="Deprecated",
                is_active=False,
            ),
        ]
        for record in records:
            db_session.add(record)
        await db_session.commit()
        return records

    async def test_get_by_nielsen_brand(
        self, repo: BrandMapRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting mapping by Nielsen brand."""
        mapping = await repo.get_by_nielsen_brand("BMW AG")
        assert mapping is not None
        assert mapping.yougov_brand_label == "BMW"

    async def test_get_yougov_label(
        self, repo: BrandMapRepository, sample_data, db_session: AsyncSession
    ):
        """Test getting YouGov label."""
        label = await repo.get_yougov_label("BMW AG")
        assert label == "BMW"

    async def test_inactive_not_returned(
        self, repo: BrandMapRepository, sample_data, db_session: AsyncSession
    ):
        """Test that inactive mappings are not returned."""
        mapping = await repo.get_by_nielsen_brand("Old Brand")
        assert mapping is None

    async def test_search_nielsen_brands(
        self, repo: BrandMapRepository, sample_data, db_session: AsyncSession
    ):
        """Test searching Nielsen brands."""
        results = await repo.search_nielsen_brands("BMW")
        assert len(results) == 1
        assert results[0].nielsen_brand == "BMW AG"

    async def test_get_unmapped_brands(
        self, repo: BrandMapRepository, sample_data, db_session: AsyncSession
    ):
        """Test finding unmapped brands."""
        unmapped = await repo.get_unmapped_nielsen_brands(
            ["BMW AG", "Unknown Brand", "Another Unknown"]
        )
        assert set(unmapped) == {"Unknown Brand", "Another Unknown"}
