"""Nielsen spend data repository."""

from decimal import Decimal
from typing import List, Optional, Dict, Any

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import NielsenSpend
from src.repositories.base import BaseRepository


class NielsenRepository(BaseRepository[NielsenSpend]):
    """Repository for Nielsen advertising spend data."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, NielsenSpend)

    async def get_by_brand(
        self,
        brand_name: str,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> List[NielsenSpend]:
        """Get spend data for a specific brand."""
        query = select(NielsenSpend).where(NielsenSpend.brand_name == brand_name)

        if year is not None:
            query = query.where(NielsenSpend.year == year)
        if month is not None:
            query = query.where(NielsenSpend.month == month)

        query = query.order_by(NielsenSpend.year, NielsenSpend.month)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_wirtschaftsgruppe(
        self,
        wirtschaftsgruppe: str,
        year: Optional[int] = None,
        limit: int = 1000,
    ) -> List[NielsenSpend]:
        """Get spend data for an industry classification."""
        query = select(NielsenSpend).where(
            NielsenSpend.wirtschaftsgruppe == wirtschaftsgruppe
        )

        if year is not None:
            query = query.where(NielsenSpend.year == year)

        query = query.order_by(NielsenSpend.brand_name, NielsenSpend.year, NielsenSpend.month)
        query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_brands_in_industry(self, wirtschaftsgruppe: str) -> List[str]:
        """Get distinct brand names in an industry."""
        query = (
            select(NielsenSpend.brand_name)
            .where(NielsenSpend.wirtschaftsgruppe == wirtschaftsgruppe)
            .distinct()
            .order_by(NielsenSpend.brand_name)
        )
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]

    async def get_channels(self) -> List[str]:
        """Get all distinct advertising channels."""
        query = select(NielsenSpend.channel).distinct().order_by(NielsenSpend.channel)
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]

    async def get_spend_by_channel(
        self,
        brand_name: str,
        year: int,
        month: Optional[int] = None,
    ) -> Dict[str, Decimal]:
        """Get spend breakdown by channel for a brand."""
        query = select(
            NielsenSpend.channel,
            func.sum(NielsenSpend.spend_eur).label("total_spend"),
        ).where(
            and_(
                NielsenSpend.brand_name == brand_name,
                NielsenSpend.year == year,
            )
        )

        if month is not None:
            query = query.where(NielsenSpend.month == month)

        query = query.group_by(NielsenSpend.channel)
        result = await self.session.execute(query)

        return {row.channel: row.total_spend for row in result.all()}

    async def get_total_spend(
        self,
        brand_name: str,
        year: int,
        month: Optional[int] = None,
    ) -> Optional[Decimal]:
        """Get total spend for a brand in a time period."""
        query = select(func.sum(NielsenSpend.spend_eur)).where(
            and_(
                NielsenSpend.brand_name == brand_name,
                NielsenSpend.year == year,
            )
        )

        if month is not None:
            query = query.where(NielsenSpend.month == month)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_spend_matrix(
        self,
        brand_names: List[str],
        year: int,
    ) -> List[Dict[str, Any]]:
        """Get spend matrix for multiple brands.

        Returns a list of dicts with brand, channel, and monthly spend data.
        """
        query = (
            select(
                NielsenSpend.brand_name,
                NielsenSpend.channel,
                NielsenSpend.month,
                func.sum(NielsenSpend.spend_eur).label("spend"),
            )
            .where(
                and_(
                    NielsenSpend.brand_name.in_(brand_names),
                    NielsenSpend.year == year,
                )
            )
            .group_by(NielsenSpend.brand_name, NielsenSpend.channel, NielsenSpend.month)
            .order_by(NielsenSpend.brand_name, NielsenSpend.channel, NielsenSpend.month)
        )

        result = await self.session.execute(query)
        return [
            {
                "brand_name": row.brand_name,
                "channel": row.channel,
                "month": row.month,
                "spend": row.spend,
            }
            for row in result.all()
        ]

    async def get_year_range(self) -> tuple[Optional[int], Optional[int]]:
        """Get the range of years available in the data."""
        query = select(func.min(NielsenSpend.year), func.max(NielsenSpend.year))
        result = await self.session.execute(query)
        row = result.one()
        return row[0], row[1]

    async def get_wirtschaftsgruppen(self) -> List[str]:
        """Get all distinct Wirtschaftsgruppen."""
        query = (
            select(NielsenSpend.wirtschaftsgruppe)
            .distinct()
            .order_by(NielsenSpend.wirtschaftsgruppe)
        )
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]
