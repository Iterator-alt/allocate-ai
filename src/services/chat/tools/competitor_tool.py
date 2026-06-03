"""Competitor management tool for chat agent.

Tool 1: Add/remove competitors from the analysis.
- Searches YouGov and Nielsen databases for brand existence
- Updates ProjectVersionAiRun.confirmedCompetitors array
- Records changes for rerun validation

PRISMA-ONLY MODE: Uses PrismaProjectVersionAiRun instead of Python Run table.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.db.models.prisma_tables import PrismaProjectVersionAiRun
from src.db.models.data import YouGov, Nielsen
from src.services.chat.tools.context_loader import ChatContext

logger = logging.getLogger(__name__)


@dataclass
class CompetitorResult:
    """Result of competitor management operation."""

    success: bool
    action: str  # "add" or "remove"
    brand: str
    message: str
    warning: Optional[str] = None
    updated_competitors: Optional[List[str]] = None
    change_record: Optional[Dict[str, Any]] = None


def strip_umlauts(text: str) -> str:
    """Convert German umlauts to ASCII equivalents.

    Handles both single-char (ü→u) and German convention (ü→ue) forms.
    Returns the single-char version for consistency.
    """
    # First normalize ue/oe/ae back to umlauts, then strip
    text = text.replace('ue', 'ü').replace('oe', 'ö').replace('ae', 'ä')
    replacements = {
        'ü': 'u', 'Ü': 'U',
        'ö': 'o', 'Ö': 'O',
        'ä': 'a', 'Ä': 'A',
        'ß': 'ss',
    }
    for umlaut, replacement in replacements.items():
        text = text.replace(umlaut, replacement)
    return text


class CompetitorManagementTool:
    """Manages competitor additions and removals.

    ADD Flow:
    1. Check if brand in current set → "Already in set"
    2. Search YouGov for brand
    3. Search Nielsen for brand
    4. Results:
       - Both found → Add silently, confirm
       - YouGov only → Add with warning (partial data)
       - Neither found → "Not found in database"
    5. Update ProjectVersionAiRun.confirmedCompetitors array
    6. Record change for rerun validation

    REMOVE Flow:
    1. Check if brand exists (fuzzy match)
    2. Remove from list → Confirm
    3. Update database
    4. Record change for rerun validation

    PRISMA-ONLY MODE: Uses PrismaProjectVersionAiRun instead of Run table.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_competitor(
        self,
        run_id: int,
        brand: str,
        context: ChatContext,
    ) -> CompetitorResult:
        """Add a competitor to the analysis.

        Args:
            run_id: externalRunId from ProjectVersionAiRun
            brand: Brand name to add
            context: Current chat context

        Returns:
            CompetitorResult with outcome
        """
        brand_normalized = brand.strip()

        # Check if already in set (case-insensitive)
        current_lower = [c.lower() for c in context.current_competitors]
        if brand_normalized.lower() in current_lower:
            return CompetitorResult(
                success=False,
                action="add",
                brand=brand_normalized,
                message=f"'{brand_normalized}' is already in the competitor set.",
            )

        # Search YouGov for the brand
        yougov_match = await self._search_yougov_brand(brand_normalized, context.industry)

        # Search Nielsen for the brand
        nielsen_match = await self._search_nielsen_brand(brand_normalized, context.industry)

        # Determine outcome
        warning = None
        if not yougov_match and not nielsen_match:
            return CompetitorResult(
                success=False,
                action="add",
                brand=brand_normalized,
                message=f"'{brand_normalized}' was not found in the database. Please check the spelling or try a different brand name.",
            )

        if yougov_match and not nielsen_match:
            warning = f"Note: '{brand_normalized}' was found in YouGov but not in Nielsen. Spend data will be limited."

        if not yougov_match and nielsen_match:
            warning = f"Note: '{brand_normalized}' was found in Nielsen but not in YouGov. KPI data will be limited."

        # Use the matched name from YouGov if available, otherwise Nielsen
        canonical_brand = yougov_match or nielsen_match or brand_normalized

        # Update the ProjectVersionAiRun's confirmedCompetitors
        new_competitors = context.current_competitors + [canonical_brand]
        await self._update_competitors(run_id, new_competitors)

        # Create change record
        change_record = {
            "type": "competitor_add",
            "brand": canonical_brand,
        }

        message = f"Added '{canonical_brand}' to competitors."
        if warning:
            message = f"{message} {warning}"

        return CompetitorResult(
            success=True,
            action="add",
            brand=canonical_brand,
            message=message,
            warning=warning,
            updated_competitors=new_competitors,
            change_record=change_record,
        )

    async def remove_competitor(
        self,
        run_id: int,
        brand: str,
        context: ChatContext,
    ) -> CompetitorResult:
        """Remove a competitor from the analysis.

        Args:
            run_id: externalRunId from ProjectVersionAiRun
            brand: Brand name to remove
            context: Current chat context

        Returns:
            CompetitorResult with outcome
        """
        brand_normalized = brand.strip()

        # Find matching brand (fuzzy match)
        matched_brand = self._find_matching_brand(brand_normalized, context.current_competitors)

        if not matched_brand:
            return CompetitorResult(
                success=False,
                action="remove",
                brand=brand_normalized,
                message=f"'{brand_normalized}' is not in the current competitor set.",
            )

        # Remove the brand
        new_competitors = [c for c in context.current_competitors if c != matched_brand]
        await self._update_competitors(run_id, new_competitors)

        # Create change record
        change_record = {
            "type": "competitor_remove",
            "brand": matched_brand,
        }

        return CompetitorResult(
            success=True,
            action="remove",
            brand=matched_brand,
            message=f"Removed '{matched_brand}' from competitors.",
            updated_competitors=new_competitors,
            change_record=change_record,
        )

    async def _search_yougov_brand(
        self,
        brand: str,
        industry: Optional[str] = None,
    ) -> Optional[str]:
        """Search for a brand in YouGov data.

        Returns the canonical brand_label if found, None otherwise.
        """
        brand_lower = brand.lower()
        brand_ascii = strip_umlauts(brand_lower)

        # Strategy 1: Exact match (case-insensitive)
        query = (
            select(YouGov.brand_label)
            .where(func.lower(YouGov.brand_label) == brand_lower)
            .distinct()
            .limit(1)
        )
        result = await self.session.execute(query)
        row = result.first()
        if row:
            return row[0]

        # Strategy 2: LIKE match
        query = (
            select(YouGov.brand_label)
            .where(func.lower(YouGov.brand_label).like(f"%{brand_lower}%"))
            .distinct()
            .limit(1)
        )
        result = await self.session.execute(query)
        row = result.first()
        if row:
            return row[0]

        # Strategy 3: ASCII-normalized match (for umlauts)
        # This requires fetching candidates and comparing in Python
        query = (
            select(YouGov.brand_label)
            .distinct()
            .limit(500)  # Limit to avoid huge queries
        )
        result = await self.session.execute(query)
        for row in result.all():
            candidate = row[0]
            candidate_ascii = strip_umlauts(candidate.lower())
            if candidate_ascii == brand_ascii or brand_ascii in candidate_ascii:
                return candidate

        return None

    async def _search_nielsen_brand(
        self,
        brand: str,
        industry: Optional[str] = None,
    ) -> Optional[str]:
        """Search for a brand in Nielsen data.

        Returns the canonical marke if found, None otherwise.
        """
        brand_lower = brand.lower()
        brand_ascii = strip_umlauts(brand_lower)

        # Strategy 1: Exact match (case-insensitive)
        query = (
            select(Nielsen.marke)
            .where(func.lower(Nielsen.marke) == brand_lower)
            .distinct()
            .limit(1)
        )
        result = await self.session.execute(query)
        row = result.first()
        if row:
            return row[0]

        # Strategy 2: LIKE match
        query = (
            select(Nielsen.marke)
            .where(func.lower(Nielsen.marke).like(f"%{brand_lower}%"))
            .distinct()
            .limit(1)
        )
        result = await self.session.execute(query)
        row = result.first()
        if row:
            return row[0]

        # Strategy 3: ASCII-normalized match
        query = (
            select(Nielsen.marke)
            .distinct()
            .limit(500)
        )
        result = await self.session.execute(query)
        for row in result.all():
            candidate = row[0]
            if candidate:
                candidate_ascii = strip_umlauts(candidate.lower())
                if candidate_ascii == brand_ascii or brand_ascii in candidate_ascii:
                    return candidate

        return None

    def _find_matching_brand(
        self,
        brand: str,
        competitors: List[str],
    ) -> Optional[str]:
        """Find a matching brand in the competitor list using fuzzy matching.

        Returns the exact brand name from the list if found.
        """
        brand_lower = brand.lower()
        brand_ascii = strip_umlauts(brand_lower)

        for comp in competitors:
            comp_lower = comp.lower()
            comp_ascii = strip_umlauts(comp_lower)

            # Exact match
            if comp_lower == brand_lower:
                return comp

            # ASCII-normalized match
            if comp_ascii == brand_ascii:
                return comp

            # Partial match
            if brand_lower in comp_lower or comp_lower in brand_lower:
                return comp

        return None

    async def _update_competitors(
        self,
        run_id: int,
        competitors: List[str],
    ) -> None:
        """Update the confirmedCompetitors array in ProjectVersionAiRun.

        Args:
            run_id: externalRunId
            competitors: New list of competitors

        Raises:
            ValueError: If the run is not found
            Exception: If database update fails
        """
        try:
            query = select(PrismaProjectVersionAiRun).where(
                PrismaProjectVersionAiRun.externalRunId == run_id
            )
            result = await self.session.execute(query)
            ai_run = result.scalar_one_or_none()

            if not ai_run:
                raise ValueError(f"ProjectVersionAiRun with externalRunId {run_id} not found")

            # Validate competitors list
            if competitors is None:
                competitors = []

            # Filter out empty strings and None values
            competitors = [c for c in competitors if c and isinstance(c, str)]

            # Update confirmedCompetitors array - MUST use flag_modified for ARRAY columns
            ai_run.confirmedCompetitors = competitors
            flag_modified(ai_run, 'confirmedCompetitors')

            await self.session.flush()
            logger.info(f"Updated competitors for run {run_id}: {len(competitors)} competitors")

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error updating competitors for run {run_id}: {e}")
            raise Exception(f"Failed to update competitors: {str(e)}")
