"""Stage 3: Output Parsing Service.

Parses and validates LLM responses, extracting allocation data
and ensuring it meets all validation requirements.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional, List, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.run import RunStatus
from src.repositories.run import RunRepository, AllocationResultRepository
from src.services.llm_gateway.client import LLMResponse

logger = logging.getLogger(__name__)


# Validation constants
PERCENTAGE_TOLERANCE = 0.01  # 1% tolerance for sum validation
MIN_CHANNELS = 1
MAX_CHANNELS = 20
VALID_CHANNELS = {
    "TV", "Digital", "Print", "Radio", "OOH", "Cinema",
    "Social Media", "Search", "Display", "Video", "Audio",
    "Programmatic", "Native", "Influencer", "Sponsorship",
    "Direct Mail", "Email", "Mobile", "Connected TV", "Podcast",
}


@dataclass
class ChannelAllocation:
    """Parsed allocation for a single channel."""

    channel: str
    percentage: Decimal
    amount: Optional[Decimal] = None
    rationale: Optional[str] = None


@dataclass
class ValidationIssue:
    """A single validation issue."""

    field: str
    message: str
    severity: str = "error"  # error, warning


@dataclass
class ParsedAllocationResult:
    """Result of parsing LLM output."""

    is_valid: bool
    allocations: List[ChannelAllocation]
    total_percentage: Decimal
    summary: Optional[str] = None
    confidence: Optional[Decimal] = None
    warnings: List[str] = field(default_factory=list)
    validation_issues: List[ValidationIssue] = field(default_factory=list)
    raw_response: Optional[str] = None

    def to_storage_format(self) -> Dict[str, Any]:
        """Convert to format for database storage."""
        return {
            "channels": [
                {
                    "name": a.channel,
                    "percentage": float(a.percentage),
                    "amount": float(a.amount) if a.amount else None,
                    "rationale": a.rationale,
                }
                for a in self.allocations
            ],
            "total_percentage": float(self.total_percentage),
        }


class OutputParsingService:
    """Service for parsing and validating LLM allocation output.

    Implements Stage 3 of the Mediamix Engine pipeline:
    1. Parse JSON response from LLM
    2. Validate schema and data types
    3. Ensure allocations sum to 100%
    4. Extract confidence and warnings
    5. Persist results to database
    """

    def __init__(self, session: AsyncSession):
        """Initialize the service.

        Args:
            session: Database session for persistence
        """
        self.session = session
        self.run_repo = RunRepository(session)
        self.result_repo = AllocationResultRepository(session)

    async def parse_and_store(
        self,
        run_id: int,
        llm_response: LLMResponse,
        total_budget: Optional[Decimal] = None,
    ) -> ParsedAllocationResult:
        """Parse LLM response and store results.

        Args:
            run_id: ID of the run
            llm_response: Response from LLM client
            total_budget: Optional total budget for amount calculation

        Returns:
            ParsedAllocationResult with validation status
        """
        # Update run status to PARSING
        await self.run_repo.update_status(run_id, RunStatus.PARSING)

        # Parse the response
        parsed = self.parse_response(llm_response, total_budget)

        # Store the result
        await self.result_repo.create_result(
            run_id=run_id,
            allocations=parsed.to_storage_format(),
            summary=parsed.summary,
            confidence_score=float(parsed.confidence) if parsed.confidence else None,
            raw_response=parsed.raw_response,
            is_valid=parsed.is_valid,
            validation_errors={
                "issues": [
                    {"field": i.field, "message": i.message, "severity": i.severity}
                    for i in parsed.validation_issues
                ]
            } if parsed.validation_issues else None,
        )

        return parsed

    def parse_response(
        self,
        llm_response: LLMResponse,
        total_budget: Optional[Decimal] = None,
    ) -> ParsedAllocationResult:
        """Parse LLM response without storing.

        Args:
            llm_response: Response from LLM client
            total_budget: Optional total budget for amount calculation

        Returns:
            ParsedAllocationResult with validation status
        """
        issues: List[ValidationIssue] = []
        warnings: List[str] = []
        allocations: List[ChannelAllocation] = []
        summary: Optional[str] = None
        confidence: Optional[Decimal] = None
        total_percentage = Decimal("0")

        # Check if we have parsed JSON
        if not llm_response.parsed_json:
            # Try to extract from raw content
            if not llm_response.content:
                issues.append(ValidationIssue(
                    field="response",
                    message="Empty response from LLM",
                    severity="error"
                ))
                return ParsedAllocationResult(
                    is_valid=False,
                    allocations=[],
                    total_percentage=Decimal("0"),
                    validation_issues=issues,
                    raw_response=llm_response.content,
                )

            issues.append(ValidationIssue(
                field="response",
                message="Response is not valid JSON",
                severity="error"
            ))
            return ParsedAllocationResult(
                is_valid=False,
                allocations=[],
                total_percentage=Decimal("0"),
                validation_issues=issues,
                raw_response=llm_response.content,
            )

        data = llm_response.parsed_json

        # Extract allocations
        raw_allocations = data.get("allocations", [])
        if not raw_allocations:
            issues.append(ValidationIssue(
                field="allocations",
                message="No allocations found in response",
                severity="error"
            ))
        elif not isinstance(raw_allocations, list):
            issues.append(ValidationIssue(
                field="allocations",
                message="Allocations must be a list",
                severity="error"
            ))
            raw_allocations = []

        # Parse each allocation
        for idx, alloc in enumerate(raw_allocations):
            parsed_alloc, alloc_issues = self._parse_single_allocation(
                alloc, idx, total_budget
            )
            if parsed_alloc:
                allocations.append(parsed_alloc)
                total_percentage += parsed_alloc.percentage
            issues.extend(alloc_issues)

        # Validate allocation count
        if len(allocations) < MIN_CHANNELS:
            issues.append(ValidationIssue(
                field="allocations",
                message=f"At least {MIN_CHANNELS} channel allocation required",
                severity="error"
            ))
        elif len(allocations) > MAX_CHANNELS:
            issues.append(ValidationIssue(
                field="allocations",
                message=f"Maximum {MAX_CHANNELS} channel allocations allowed",
                severity="warning"
            ))

        # Validate percentages sum to 100%
        percentage_diff = abs(total_percentage - Decimal("100"))
        if percentage_diff > Decimal(str(PERCENTAGE_TOLERANCE * 100)):
            issues.append(ValidationIssue(
                field="total_percentage",
                message=f"Allocations sum to {total_percentage}%, expected 100%",
                severity="error"
            ))
        elif percentage_diff > Decimal("0"):
            # Small difference - normalize
            if allocations:
                adjustment = (Decimal("100") - total_percentage) / len(allocations)
                for alloc in allocations:
                    alloc.percentage += adjustment
                total_percentage = Decimal("100")
                warnings.append(
                    f"Allocations adjusted by {float(adjustment):.2f}% each to sum to 100%"
                )

        # Extract summary
        summary = data.get("summary")
        if not summary:
            warnings.append("No summary provided in response")

        # Extract confidence
        raw_confidence = data.get("confidence")
        if raw_confidence is not None:
            try:
                confidence = Decimal(str(raw_confidence))
                if confidence < 0 or confidence > 1:
                    issues.append(ValidationIssue(
                        field="confidence",
                        message=f"Confidence {confidence} out of range [0, 1]",
                        severity="warning"
                    ))
                    confidence = max(Decimal("0"), min(Decimal("1"), confidence))
            except (InvalidOperation, ValueError):
                issues.append(ValidationIssue(
                    field="confidence",
                    message=f"Invalid confidence value: {raw_confidence}",
                    severity="warning"
                ))

        # Extract warnings from response
        response_warnings = data.get("warnings", [])
        if isinstance(response_warnings, list):
            warnings.extend(str(w) for w in response_warnings if w)

        # Determine overall validity
        has_errors = any(i.severity == "error" for i in issues)
        is_valid = not has_errors and len(allocations) >= MIN_CHANNELS

        return ParsedAllocationResult(
            is_valid=is_valid,
            allocations=allocations,
            total_percentage=total_percentage,
            summary=summary,
            confidence=confidence,
            warnings=warnings,
            validation_issues=issues,
            raw_response=llm_response.content,
        )

    def _parse_single_allocation(
        self,
        alloc: Any,
        index: int,
        total_budget: Optional[Decimal] = None,
    ) -> tuple[Optional[ChannelAllocation], List[ValidationIssue]]:
        """Parse a single allocation entry.

        Args:
            alloc: Raw allocation data
            index: Index in the allocations list
            total_budget: Optional total budget for amount calculation

        Returns:
            Tuple of (parsed allocation, list of issues)
        """
        issues: List[ValidationIssue] = []

        if not isinstance(alloc, dict):
            issues.append(ValidationIssue(
                field=f"allocations[{index}]",
                message="Allocation must be an object",
                severity="error"
            ))
            return None, issues

        # Extract channel name
        channel = alloc.get("channel") or alloc.get("name")
        if not channel:
            issues.append(ValidationIssue(
                field=f"allocations[{index}].channel",
                message="Channel name is required",
                severity="error"
            ))
            return None, issues

        channel = str(channel).strip()

        # Validate channel name (warning only - don't reject unknown channels)
        if channel not in VALID_CHANNELS:
            # Check for case-insensitive match
            matched = next(
                (c for c in VALID_CHANNELS if c.lower() == channel.lower()),
                None
            )
            if matched:
                channel = matched  # Use canonical name
            else:
                issues.append(ValidationIssue(
                    field=f"allocations[{index}].channel",
                    message=f"Unknown channel '{channel}'",
                    severity="warning"
                ))

        # Extract percentage
        percentage = alloc.get("percentage") or alloc.get("share_pct")
        if percentage is None:
            issues.append(ValidationIssue(
                field=f"allocations[{index}].percentage",
                message="Percentage is required",
                severity="error"
            ))
            return None, issues

        try:
            percentage = Decimal(str(percentage))
        except (InvalidOperation, ValueError):
            issues.append(ValidationIssue(
                field=f"allocations[{index}].percentage",
                message=f"Invalid percentage value: {percentage}",
                severity="error"
            ))
            return None, issues

        # Validate percentage range
        if percentage < 0 or percentage > 100:
            issues.append(ValidationIssue(
                field=f"allocations[{index}].percentage",
                message=f"Percentage {percentage} out of range [0, 100]",
                severity="error"
            ))
            return None, issues

        # Calculate amount from percentage when total budget is known.
        # LLM-provided amounts can be inconsistent with the percentage, so they are
        # ignored and only used as a fallback when no total budget is available.
        if total_budget is not None:
            amount = ((total_budget * percentage) / Decimal("100")).quantize(Decimal("0.01"))
        else:
            amount = alloc.get("amount") or alloc.get("budget_gross_eur")
            if amount is not None:
                try:
                    amount = Decimal(str(amount))
                except (InvalidOperation, ValueError):
                    issues.append(ValidationIssue(
                        field=f"allocations[{index}].amount",
                        message=f"Invalid amount value: {amount}",
                        severity="warning"
                    ))
                    amount = None

        # Extract rationale
        rationale = alloc.get("rationale") or alloc.get("reasoning")
        if rationale:
            rationale = str(rationale).strip()

        return ChannelAllocation(
            channel=channel,
            percentage=percentage,
            amount=amount,
            rationale=rationale,
        ), issues

    def validate_against_request(
        self,
        parsed: ParsedAllocationResult,
        requested_channels: Optional[List[str]] = None,
        total_budget: Optional[Decimal] = None,
    ) -> List[ValidationIssue]:
        """Additional validation against the original request.

        Args:
            parsed: Parsed allocation result
            requested_channels: Channels requested in the input
            total_budget: Total budget from request

        Returns:
            List of additional validation issues
        """
        issues: List[ValidationIssue] = []

        # Check if all requested channels are present
        if requested_channels:
            allocated_channels = {a.channel.lower() for a in parsed.allocations}
            for channel in requested_channels:
                if channel.lower() not in allocated_channels:
                    issues.append(ValidationIssue(
                        field="channels",
                        message=f"Requested channel '{channel}' not in allocations",
                        severity="warning"
                    ))

        # Validate amounts match budget
        if total_budget and parsed.allocations:
            total_amount = sum(
                a.amount for a in parsed.allocations
                if a.amount is not None
            )
            if total_amount > 0:
                budget_diff = abs(total_amount - total_budget)
                tolerance = total_budget * Decimal("0.01")  # 1% tolerance
                if budget_diff > tolerance:
                    issues.append(ValidationIssue(
                        field="amounts",
                        message=(
                            f"Total allocated amount ({total_amount}) "
                            f"differs from budget ({total_budget})"
                        ),
                        severity="warning"
                    ))

        return issues
