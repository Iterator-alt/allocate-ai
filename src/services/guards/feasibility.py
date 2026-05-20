"""Guard #2: Data Feasibility Check.

Pre-flight validation that runs before each LLM call to verify:
1. Industry exists in Nielsen/YouGov mapping
2. Requested channels exist in Nielsen data
3. Requested KPI exists in YouGov data

If gaps are found, returns closest matching alternatives as suggestions.
Blocks generation if no viable match exists.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories import (
    IndustryMapRepository,
    NielsenRepository,
    YouGovRepository,
)


@dataclass
class FeasibilityIssue:
    """A single feasibility issue found during validation."""

    field: str  # industry, channel, kpi
    value: str  # The problematic value
    issue_type: str  # not_found, no_data, insufficient_data
    message: str
    suggestions: List[str] = field(default_factory=list)
    is_blocking: bool = False  # If True, generation cannot proceed


@dataclass
class FeasibilityCheckResult:
    """Result of the feasibility check."""

    is_feasible: bool
    issues: List[FeasibilityIssue] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggested_alternatives: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def has_blocking_issues(self) -> bool:
        return any(issue.is_blocking for issue in self.issues)

    @property
    def blocking_issues(self) -> List[FeasibilityIssue]:
        return [issue for issue in self.issues if issue.is_blocking]

    @property
    def non_blocking_issues(self) -> List[FeasibilityIssue]:
        return [issue for issue in self.issues if not issue.is_blocking]


class DataFeasibilityGuard:
    """Guard #2: Validates data availability before LLM call.

    Checks:
    1. Industry mapping exists (Wirtschaftsgruppe → sector_label)
    2. Channels exist in Nielsen data
    3. KPI metric has data in YouGov

    Returns suggestions for closest matches when gaps are found.
    """

    VALID_KPIS = {"adaware", "aided", "consider"}

    def __init__(self, session: AsyncSession):
        self.session = session
        self.industry_repo = IndustryMapRepository(session)
        self.nielsen_repo = NielsenRepository(session)
        self.yougov_repo = YouGovRepository(session)

    async def check_feasibility(
        self,
        industry: str,
        brand_kpi: str,
        channels: Optional[List[str]] = None,
        require_full_data: bool = False,
    ) -> FeasibilityCheckResult:
        """Run all feasibility checks.

        Args:
            industry: Wirtschaftsgruppe to validate
            brand_kpi: KPI metric (adaware, aided, consider)
            channels: Optional list of channels to validate
            require_full_data: If True, require both Nielsen and YouGov data

        Returns:
            FeasibilityCheckResult with any issues found
        """
        issues = []
        warnings = []
        suggestions = {}

        # Check 1: Industry mapping
        industry_result = await self._check_industry(industry)
        if industry_result:
            issues.append(industry_result)
            if industry_result.suggestions:
                suggestions["industry"] = industry_result.suggestions

        # Check 2: KPI validity
        kpi_result = await self._check_kpi(brand_kpi)
        if kpi_result:
            issues.append(kpi_result)
            if kpi_result.suggestions:
                suggestions["kpi"] = kpi_result.suggestions

        # Check 3: Channels (if specified)
        if channels:
            channel_results = await self._check_channels(channels)
            issues.extend(channel_results)
            invalid_channels = [r.value for r in channel_results if r.suggestions]
            if invalid_channels:
                # Get all valid channels as suggestions
                valid = await self.nielsen_repo.get_channels()
                suggestions["channels"] = valid

        # Check 4: Data availability
        if not any(i.is_blocking for i in issues):
            data_warnings = await self._check_data_availability(
                industry, brand_kpi, require_full_data
            )
            warnings.extend(data_warnings)

        is_feasible = not any(issue.is_blocking for issue in issues)

        return FeasibilityCheckResult(
            is_feasible=is_feasible,
            issues=issues,
            warnings=warnings,
            suggested_alternatives=suggestions,
        )

    async def _check_industry(self, industry: str) -> Optional[FeasibilityIssue]:
        """Check if industry exists in mapping table."""
        sector_label = await self.industry_repo.get_sector_label(industry)

        if sector_label:
            return None

        # Find similar industries for suggestions
        all_industries = await self.industry_repo.get_all_wirtschaftsgruppen()
        suggestions = self._find_similar_strings(industry, all_industries, limit=5)

        return FeasibilityIssue(
            field="industry",
            value=industry,
            issue_type="not_found",
            message=f"Industry '{industry}' not found in mapping table",
            suggestions=suggestions,
            is_blocking=True,
        )

    async def _check_kpi(self, kpi: str) -> Optional[FeasibilityIssue]:
        """Check if KPI is valid."""
        kpi_lower = kpi.lower()

        if kpi_lower in self.VALID_KPIS:
            return None

        return FeasibilityIssue(
            field="kpi",
            value=kpi,
            issue_type="not_found",
            message=f"KPI '{kpi}' is not valid. Must be one of: {', '.join(self.VALID_KPIS)}",
            suggestions=list(self.VALID_KPIS),
            is_blocking=True,
        )

    async def _check_channels(self, channels: List[str]) -> List[FeasibilityIssue]:
        """Check if requested channels exist in Nielsen data."""
        available_channels = await self.nielsen_repo.get_channels()
        available_set = {c.lower() for c in available_channels}

        issues = []
        for channel in channels:
            if channel.lower() not in available_set:
                suggestions = self._find_similar_strings(
                    channel, available_channels, limit=3
                )
                issues.append(FeasibilityIssue(
                    field="channel",
                    value=channel,
                    issue_type="not_found",
                    message=f"Channel '{channel}' not found in Nielsen data",
                    suggestions=suggestions,
                    is_blocking=False,  # Can proceed with other channels
                ))

        return issues

    async def _check_data_availability(
        self,
        industry: str,
        brand_kpi: str,
        require_full_data: bool,
    ) -> List[str]:
        """Check data availability and return warnings."""
        warnings = []

        # Get sector label for YouGov check
        sector_label = await self.industry_repo.get_sector_label(industry)
        if not sector_label:
            return warnings

        # Check Nielsen data
        nielsen_brands = await self.nielsen_repo.get_brands_in_industry(industry)
        if not nielsen_brands:
            warnings.append(
                f"No Nielsen spend data found for industry '{industry}'. "
                "Allocation will rely on industry benchmarks."
            )

        # Check YouGov data
        yougov_brands = await self.yougov_repo.get_brands_in_sector(sector_label)
        if not yougov_brands:
            warnings.append(
                f"No YouGov brands found in sector '{sector_label}'. "
                f"KPI '{brand_kpi}' projections may be limited."
            )

        # Check year range
        nielsen_years = await self.nielsen_repo.get_year_range()
        yougov_years = await self.yougov_repo.get_year_range()

        if nielsen_years[0] and nielsen_years[1]:
            if nielsen_years[1] - nielsen_years[0] < 1:
                warnings.append(
                    "Less than 1 year of Nielsen data available. "
                    "Trend analysis may be limited."
                )

        if yougov_years[0] and yougov_years[1]:
            if yougov_years[1] - yougov_years[0] < 1:
                warnings.append(
                    f"Less than 1 year of YouGov {brand_kpi} data available. "
                    "KPI projections may be limited."
                )

        return warnings

    def _find_similar_strings(
        self,
        target: str,
        candidates: List[str],
        limit: int = 5,
    ) -> List[str]:
        """Find similar strings using simple matching.

        Uses substring matching and word overlap for MVP.
        Can be enhanced with fuzzy matching (Levenshtein) later.
        """
        target_lower = target.lower()
        target_words = set(target_lower.split())

        scored = []
        for candidate in candidates:
            candidate_lower = candidate.lower()
            candidate_words = set(candidate_lower.split())

            score = 0

            # Exact match
            if target_lower == candidate_lower:
                score = 100
            # Substring match
            elif target_lower in candidate_lower or candidate_lower in target_lower:
                score = 50
            # Word overlap
            else:
                overlap = len(target_words & candidate_words)
                if overlap > 0:
                    score = overlap * 10

            if score > 0:
                scored.append((candidate, score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        return [s[0] for s in scored[:limit]]

    async def get_available_options(self) -> Dict[str, List[str]]:
        """Get all available options for industries, channels, and KPIs.

        Useful for frontend dropdown population.
        """
        industries = await self.industry_repo.get_all_wirtschaftsgruppen()
        channels = await self.nielsen_repo.get_channels()
        sectors = await self.yougov_repo.get_sectors()

        return {
            "industries": industries,
            "channels": channels,
            "sectors": sectors,
            "kpis": list(self.VALID_KPIS),
        }
