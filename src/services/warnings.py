"""Structured warnings for allocation results and chat cards.

Warnings are enriched with color, title, and description for frontend display.
Severity is determined by backend rules, not LLM.
"""

from dataclasses import dataclass
from typing import Literal, List, Dict, Any, Optional


@dataclass
class StructuredWarning:
    """A warning with color coding and structured content."""
    color: Literal["red", "yellow"]
    title: str
    description: str

    def to_dict(self) -> dict:
        return {
            "color": self.color,
            "title": self.title,
            "description": self.description,
        }


# =============================================================================
# Warning Generators — Yellow (informational)
# =============================================================================

def warning_competitor_excluded_no_nielsen(competitor_name: str) -> StructuredWarning:
    """Competitor excluded from spend analysis due to missing Nielsen data."""
    return StructuredWarning(
        color="yellow",
        title="Limited Data",
        description=f"{competitor_name} excluded from spend analysis due to missing Nielsen data.",
    )


def warning_limited_channel_data(channel: str) -> StructuredWarning:
    """Limited historical spend data for a channel."""
    return StructuredWarning(
        color="yellow",
        title="Limited Channel Data",
        description=f"Limited historical spend data for {channel}. Allocation based on available benchmarks.",
    )


def warning_sector_kpi_decline(kpi: str, change_pp: float) -> StructuredWarning:
    """Sector-wide KPI has declined recently."""
    return StructuredWarning(
        color="yellow",
        title="Sector KPI Decline",
        description=f"Sector-wide {kpi} has declined by {abs(change_pp):.1f}pp recently. Consider adjusting expectations.",
    )


def warning_limited_competitor_data(count: int) -> StructuredWarning:
    """Only limited competitor data available."""
    return StructuredWarning(
        color="yellow",
        title="Limited Competitor Data",
        description=f"Only {count} competitor(s) with complete data. Allocation based on available benchmarks.",
    )


def warning_generic(message: str) -> StructuredWarning:
    """Convert any string warning to structured format (default yellow)."""
    return StructuredWarning(
        color="yellow",
        title="Notice",
        description=message,
    )


# =============================================================================
# Warning Generators — Red (critical)
# =============================================================================

def warning_brand_not_found(brand_name: str, source: str) -> StructuredWarning:
    """Brand not found in data source."""
    return StructuredWarning(
        color="red",
        title="Brand Not Found",
        description=f"Could not find '{brand_name}' in {source}. Results may be incomplete.",
    )


def warning_no_competitor_data() -> StructuredWarning:
    """No competitor data found at all."""
    return StructuredWarning(
        color="red",
        title="No Competitor Data",
        description="No competitor data found. Allocation is based on general industry benchmarks only.",
    )


def warning_budget_exceeds_historical(budget: float, historical: float) -> StructuredWarning:
    """Budget exceeds historical spend by more than 10x."""
    ratio = budget / historical if historical > 0 else float('inf')
    return StructuredWarning(
        color="red",
        title="Budget Exceeds Historical",
        description=f"Budget (EUR {budget:,.0f}) exceeds historical spend by {ratio:.1f}x. Projections may be less reliable.",
    )


def warning_no_nielsen_data_for_brand(brand_name: str) -> StructuredWarning:
    """No Nielsen spend data found for the customer brand."""
    return StructuredWarning(
        color="red",
        title="No Spend Data",
        description=f"No Nielsen spend data found for '{brand_name}'. Allocation based on competitor benchmarks only.",
    )


# =============================================================================
# Warning Builder — analyze data and generate appropriate warnings
# =============================================================================

def build_warnings_from_context(
    parsed_allocation: Dict[str, Any],
    total_budget: Optional[float],
    competitor_data: Optional[List[Dict[str, Any]]] = None,
    historical_spend: Optional[float] = None,
    excluded_competitors: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Build structured warnings from allocation context.

    Args:
        parsed_allocation: Raw LLM allocation response
        total_budget: User's total budget
        competitor_data: List of competitor info dicts
        historical_spend: Historical total spend for comparison
        excluded_competitors: List of competitor names excluded due to missing data

    Returns:
        List of structured warning dicts with color, title, description
    """
    warnings: List[StructuredWarning] = []

    # 1. Check for budget exceeding historical (red if >10x)
    if total_budget and historical_spend and historical_spend > 0:
        ratio = total_budget / historical_spend
        if ratio > 10:
            warnings.append(warning_budget_exceeds_historical(total_budget, historical_spend))

    # 2. Check for excluded competitors (yellow each)
    if excluded_competitors:
        for comp_name in excluded_competitors:
            warnings.append(warning_competitor_excluded_no_nielsen(comp_name))

    # 3. Check for no competitor data at all (red)
    if competitor_data is not None and len(competitor_data) == 0:
        warnings.append(warning_no_competitor_data())
    elif competitor_data is not None and len(competitor_data) < 2:
        # Only 1 competitor with data
        warnings.append(warning_limited_competitor_data(len(competitor_data)))

    # 4. Convert any LLM string warnings to structured format (yellow)
    llm_warnings = parsed_allocation.get("warnings", [])
    for llm_warning in llm_warnings:
        if isinstance(llm_warning, str):
            warnings.append(warning_generic(llm_warning))
        elif isinstance(llm_warning, dict):
            # Already structured, pass through
            pass

    return [w.to_dict() for w in warnings]
