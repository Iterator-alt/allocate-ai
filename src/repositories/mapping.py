"""Mapping tables repository."""

from typing import List, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import IndustryMap, BrandMap
from src.repositories.base import BaseRepository


class IndustryMapRepository(BaseRepository[IndustryMap]):
    """Repository for industry mapping data."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, IndustryMap)

    async def get_by_wirtschaftsgruppe(
        self, wirtschaftsgruppe: str
    ) -> Optional[IndustryMap]:
        """Get mapping for a specific Wirtschaftsgruppe."""
        query = select(IndustryMap).where(
            and_(
                IndustryMap.wirtschaftsgruppe == wirtschaftsgruppe,
                IndustryMap.is_active == True,
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_sector_label(self, wirtschaftsgruppe: str) -> Optional[str]:
        """Get the sector label for a Wirtschaftsgruppe."""
        mapping = await self.get_by_wirtschaftsgruppe(wirtschaftsgruppe)
        return mapping.sector_label if mapping else None

    async def get_by_sector_label(self, sector_label: str) -> List[IndustryMap]:
        """Get all mappings for a sector label."""
        query = select(IndustryMap).where(
            and_(
                IndustryMap.sector_label == sector_label,
                IndustryMap.is_active == True,
            )
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_all_active(self) -> List[IndustryMap]:
        """Get all active industry mappings."""
        query = (
            select(IndustryMap)
            .where(IndustryMap.is_active == True)
            .order_by(IndustryMap.wirtschaftsgruppe)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_all_wirtschaftsgruppen(self) -> List[str]:
        """Get all distinct Wirtschaftsgruppen with mappings."""
        query = (
            select(IndustryMap.wirtschaftsgruppe)
            .where(IndustryMap.is_active == True)
            .distinct()
            .order_by(IndustryMap.wirtschaftsgruppe)
        )
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]

    async def get_all_sector_labels(self) -> List[str]:
        """Get all distinct sector labels."""
        query = (
            select(IndustryMap.sector_label)
            .where(IndustryMap.is_active == True)
            .distinct()
            .order_by(IndustryMap.sector_label)
        )
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]


class BrandMapRepository(BaseRepository[BrandMap]):
    """Repository for brand mapping data."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, BrandMap)

    async def get_by_nielsen_brand(
        self,
        nielsen_brand: str,
        wirtschaftsgruppe: Optional[str] = None,
    ) -> Optional[BrandMap]:
        """Get mapping for a Nielsen brand name."""
        conditions = [
            BrandMap.nielsen_brand == nielsen_brand,
            BrandMap.is_active == True,
        ]

        if wirtschaftsgruppe:
            conditions.append(BrandMap.wirtschaftsgruppe == wirtschaftsgruppe)

        query = select(BrandMap).where(and_(*conditions))

        # If multiple mappings, prefer the one with highest confidence
        query = query.order_by(BrandMap.confidence.desc().nullslast())

        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_yougov_label(
        self,
        nielsen_brand: str,
        wirtschaftsgruppe: Optional[str] = None,
    ) -> Optional[str]:
        """Get the YouGov label for a Nielsen brand."""
        mapping = await self.get_by_nielsen_brand(nielsen_brand, wirtschaftsgruppe)
        return mapping.yougov_brand_label if mapping else None

    async def get_by_yougov_label(self, yougov_brand_label: str) -> List[BrandMap]:
        """Get all Nielsen brands that map to a YouGov label."""
        query = select(BrandMap).where(
            and_(
                BrandMap.yougov_brand_label == yougov_brand_label,
                BrandMap.is_active == True,
            )
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_all_active(self) -> List[BrandMap]:
        """Get all active brand mappings."""
        query = (
            select(BrandMap)
            .where(BrandMap.is_active == True)
            .order_by(BrandMap.nielsen_brand)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_mappings_by_wirtschaftsgruppe(
        self, wirtschaftsgruppe: str
    ) -> List[BrandMap]:
        """Get all brand mappings for a specific industry."""
        query = (
            select(BrandMap)
            .where(
                and_(
                    BrandMap.wirtschaftsgruppe == wirtschaftsgruppe,
                    BrandMap.is_active == True,
                )
            )
            .order_by(BrandMap.nielsen_brand)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def search_nielsen_brands(
        self, search_term: str, limit: int = 20
    ) -> List[BrandMap]:
        """Search for Nielsen brands by partial match."""
        query = (
            select(BrandMap)
            .where(
                and_(
                    BrandMap.nielsen_brand.ilike(f"%{search_term}%"),
                    BrandMap.is_active == True,
                )
            )
            .order_by(BrandMap.nielsen_brand)
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_unmapped_nielsen_brands(
        self, nielsen_brands: List[str]
    ) -> List[str]:
        """Find which Nielsen brands don't have mappings."""
        query = (
            select(BrandMap.nielsen_brand)
            .where(
                and_(
                    BrandMap.nielsen_brand.in_(nielsen_brands),
                    BrandMap.is_active == True,
                )
            )
        )
        result = await self.session.execute(query)
        mapped_brands = {row[0] for row in result.all()}
        return [b for b in nielsen_brands if b not in mapped_brands]
