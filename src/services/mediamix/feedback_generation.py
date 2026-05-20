"""Stage 4: Feedback Generation Service.

Analyzes allocation results and generates feedback cards including
warnings (yellow), alerts (red), summaries, and recommendations.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, List, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.run import RunStatus, Run
from src.repositories.run import RunRepository, ChatHistoryRepository, AllocationResultRepository
from src.services.mediamix.output_parsing import ParsedAllocationResult, ChannelAllocation

logger = logging.getLogger(__name__)


# Thresholds for feedback generation
LOW_CONFIDENCE_THRESHOLD = Decimal("0.6")  # Below this triggers warning
VERY_LOW_CONFIDENCE_THRESHOLD = Decimal("0.4")  # Below this triggers alert
HIGH_CONCENTRATION_THRESHOLD = Decimal("50")  # Single channel > 50% triggers warning
VERY_HIGH_CONCENTRATION_THRESHOLD = Decimal("70")  # Single channel > 70% triggers alert
MIN_DIVERSIFICATION_CHANNELS = 3  # Less than this triggers warning
LOW_ALLOCATION_THRESHOLD = Decimal("5")  # Allocations below 5% trigger info


@dataclass
class FeedbackMessage:
    """A feedback message to be displayed to the user."""

    message_type: str  # warning, alert, summary, recommendation, info
    severity: str  # info, warning, error
    title: str
    content: str
    extra_data: Optional[Dict[str, Any]] = None


@dataclass
class FeedbackGenerationResult:
    """Result of feedback generation."""

    messages: List[FeedbackMessage] = field(default_factory=list)
    has_warnings: bool = False
    has_alerts: bool = False
    summary_generated: bool = False


class FeedbackGenerationService:
    """Service for generating feedback cards based on allocation results.

    Implements Stage 4 of the Mediamix Engine pipeline:
    1. Analyze allocation quality and confidence
    2. Generate warnings for low data confidence (yellow cards)
    3. Generate alerts for critical issues (red cards)
    4. Generate summary of the allocation strategy
    5. Generate recommendations for improvement
    """

    def __init__(self, session: AsyncSession):
        """Initialize the service.

        Args:
            session: Database session for persistence
        """
        self.session = session
        self.run_repo = RunRepository(session)
        self.chat_repo = ChatHistoryRepository(session)
        self.result_repo = AllocationResultRepository(session)

    async def generate_and_store(
        self,
        run_id: int,
        parsed_result: ParsedAllocationResult,
        run: Optional[Run] = None,
        competitor_data: Optional[Dict[str, Any]] = None,
    ) -> FeedbackGenerationResult:
        """Generate feedback and store in database.

        Args:
            run_id: ID of the run
            parsed_result: Parsed allocation result from Stage 3
            run: Optional run object (fetched if not provided)
            competitor_data: Optional data about competitors for gap analysis

        Returns:
            FeedbackGenerationResult with all generated messages
        """
        # Update run status to FEEDBACK
        await self.run_repo.update_status(run_id, RunStatus.FEEDBACK)

        # Get run if not provided
        if not run:
            run = await self.run_repo.get(run_id)

        # Generate all feedback
        result = self.generate_feedback(parsed_result, run, competitor_data)

        # Store each message
        for message in result.messages:
            if message.message_type == "warning":
                await self.chat_repo.add_warning(
                    run_id=run_id,
                    title=message.title,
                    content=message.content,
                    extra_data=message.extra_data,
                )
            elif message.message_type == "alert":
                await self.chat_repo.add_alert(
                    run_id=run_id,
                    title=message.title,
                    content=message.content,
                    extra_data=message.extra_data,
                )
            elif message.message_type == "summary":
                await self.chat_repo.add_summary(
                    run_id=run_id,
                    title=message.title,
                    content=message.content,
                    extra_data=message.extra_data,
                )
            else:
                # info, recommendation
                await self.chat_repo.create_message(
                    run_id=run_id,
                    message_type=message.message_type,
                    severity=message.severity,
                    title=message.title,
                    content=message.content,
                    extra_data=message.extra_data,
                )

        # Update run status to COMPLETED
        await self.run_repo.update_status(run_id, RunStatus.COMPLETED)

        return result

    def generate_feedback(
        self,
        parsed_result: ParsedAllocationResult,
        run: Optional[Run] = None,
        competitor_data: Optional[Dict[str, Any]] = None,
    ) -> FeedbackGenerationResult:
        """Generate feedback without storing.

        Args:
            parsed_result: Parsed allocation result
            run: Optional run object for context
            competitor_data: Optional competitor data for analysis

        Returns:
            FeedbackGenerationResult with all generated messages
        """
        messages: List[FeedbackMessage] = []

        # 1. Check validation issues from parsing
        messages.extend(self._generate_validation_feedback(parsed_result))

        # 2. Check confidence level
        messages.extend(self._generate_confidence_feedback(parsed_result))

        # 3. Check allocation concentration
        messages.extend(self._generate_concentration_feedback(parsed_result))

        # 4. Check diversification
        messages.extend(self._generate_diversification_feedback(parsed_result))

        # 5. Check for competitor gaps (if data available)
        if competitor_data:
            messages.extend(self._generate_competitor_gap_feedback(
                parsed_result, competitor_data
            ))

        # 6. Generate recommendations
        messages.extend(self._generate_recommendations(parsed_result, run))

        # 7. Generate summary (always last)
        summary = self._generate_summary(parsed_result, run)
        if summary:
            messages.append(summary)

        # Calculate flags
        has_warnings = any(m.severity == "warning" for m in messages)
        has_alerts = any(m.severity == "error" for m in messages)
        summary_generated = any(m.message_type == "summary" for m in messages)

        return FeedbackGenerationResult(
            messages=messages,
            has_warnings=has_warnings,
            has_alerts=has_alerts,
            summary_generated=summary_generated,
        )

    def _generate_validation_feedback(
        self, parsed: ParsedAllocationResult
    ) -> List[FeedbackMessage]:
        """Generate feedback from validation issues."""
        messages: List[FeedbackMessage] = []

        for issue in parsed.validation_issues:
            if issue.severity == "error":
                messages.append(FeedbackMessage(
                    message_type="alert",
                    severity="error",
                    title="Validation Error",
                    content=issue.message,
                    extra_data={"field": issue.field},
                ))
            else:
                messages.append(FeedbackMessage(
                    message_type="warning",
                    severity="warning",
                    title="Validation Warning",
                    content=issue.message,
                    extra_data={"field": issue.field},
                ))

        # Add warnings from LLM response
        for warning in parsed.warnings:
            messages.append(FeedbackMessage(
                message_type="warning",
                severity="warning",
                title="Data Quality Notice",
                content=warning,
            ))

        return messages

    def _generate_confidence_feedback(
        self, parsed: ParsedAllocationResult
    ) -> List[FeedbackMessage]:
        """Generate feedback based on confidence score."""
        messages: List[FeedbackMessage] = []

        if parsed.confidence is None:
            messages.append(FeedbackMessage(
                message_type="info",
                severity="info",
                title="Confidence Not Available",
                content=(
                    "The model did not provide a confidence score for this allocation. "
                    "Consider reviewing the rationale for each channel."
                ),
            ))
        elif parsed.confidence < VERY_LOW_CONFIDENCE_THRESHOLD:
            messages.append(FeedbackMessage(
                message_type="alert",
                severity="error",
                title="Very Low Confidence",
                content=(
                    f"Model confidence is very low ({float(parsed.confidence):.0%}). "
                    "This may indicate insufficient data or unusual market conditions. "
                    "We strongly recommend manual review before acting on these allocations."
                ),
                extra_data={"confidence": float(parsed.confidence)},
            ))
        elif parsed.confidence < LOW_CONFIDENCE_THRESHOLD:
            messages.append(FeedbackMessage(
                message_type="warning",
                severity="warning",
                title="Low Confidence",
                content=(
                    f"Model confidence is below optimal ({float(parsed.confidence):.0%}). "
                    "Consider reviewing the allocation rationale and available data."
                ),
                extra_data={"confidence": float(parsed.confidence)},
            ))

        return messages

    def _generate_concentration_feedback(
        self, parsed: ParsedAllocationResult
    ) -> List[FeedbackMessage]:
        """Generate feedback about allocation concentration."""
        messages: List[FeedbackMessage] = []

        if not parsed.allocations:
            return messages

        # Find highest allocation
        max_alloc = max(parsed.allocations, key=lambda a: a.percentage)

        if max_alloc.percentage > VERY_HIGH_CONCENTRATION_THRESHOLD:
            messages.append(FeedbackMessage(
                message_type="alert",
                severity="error",
                title="High Concentration Risk",
                content=(
                    f"{max_alloc.channel} receives {float(max_alloc.percentage):.1f}% "
                    f"of the budget. This high concentration may expose you to "
                    f"channel-specific risks. Consider diversification."
                ),
                extra_data={
                    "channel": max_alloc.channel,
                    "percentage": float(max_alloc.percentage),
                },
            ))
        elif max_alloc.percentage > HIGH_CONCENTRATION_THRESHOLD:
            messages.append(FeedbackMessage(
                message_type="warning",
                severity="warning",
                title="Concentrated Allocation",
                content=(
                    f"{max_alloc.channel} receives {float(max_alloc.percentage):.1f}% "
                    f"of the budget. This is higher than typical diversified strategies."
                ),
                extra_data={
                    "channel": max_alloc.channel,
                    "percentage": float(max_alloc.percentage),
                },
            ))

        return messages

    def _generate_diversification_feedback(
        self, parsed: ParsedAllocationResult
    ) -> List[FeedbackMessage]:
        """Generate feedback about diversification."""
        messages: List[FeedbackMessage] = []

        if not parsed.allocations:
            return messages

        # Count significant allocations (> low threshold)
        significant_allocations = [
            a for a in parsed.allocations
            if a.percentage >= LOW_ALLOCATION_THRESHOLD
        ]

        if len(significant_allocations) < MIN_DIVERSIFICATION_CHANNELS:
            messages.append(FeedbackMessage(
                message_type="warning",
                severity="warning",
                title="Limited Diversification",
                content=(
                    f"Only {len(significant_allocations)} channels have significant "
                    f"allocations (≥{float(LOW_ALLOCATION_THRESHOLD)}%). "
                    f"Consider spreading budget across more channels for better reach."
                ),
                extra_data={"significant_channels": len(significant_allocations)},
            ))

        # Note very small allocations
        small_allocations = [
            a for a in parsed.allocations
            if 0 < a.percentage < LOW_ALLOCATION_THRESHOLD
        ]
        if small_allocations:
            channel_names = [a.channel for a in small_allocations]
            messages.append(FeedbackMessage(
                message_type="info",
                severity="info",
                title="Small Allocations",
                content=(
                    f"The following channels have allocations below "
                    f"{float(LOW_ALLOCATION_THRESHOLD)}%: {', '.join(channel_names)}. "
                    f"Consider whether these small investments will be effective."
                ),
                extra_data={"channels": channel_names},
            ))

        return messages

    def _generate_competitor_gap_feedback(
        self,
        parsed: ParsedAllocationResult,
        competitor_data: Dict[str, Any],
    ) -> List[FeedbackMessage]:
        """Generate feedback about gaps compared to competitors."""
        messages: List[FeedbackMessage] = []

        competitor_channels = competitor_data.get("channels_used", set())
        if not competitor_channels:
            return messages

        allocated_channels = {a.channel for a in parsed.allocations if a.percentage > 0}

        # Find channels competitors use but we don't
        missing_channels = competitor_channels - allocated_channels
        if missing_channels:
            messages.append(FeedbackMessage(
                message_type="warning",
                severity="warning",
                title="Competitor Channel Gap",
                content=(
                    f"Competitors are active in channels not in your allocation: "
                    f"{', '.join(missing_channels)}. Consider whether these "
                    f"channels could improve your reach."
                ),
                extra_data={"missing_channels": list(missing_channels)},
            ))

        # Find channels we use but competitors don't (opportunity)
        unique_channels = allocated_channels - competitor_channels
        if unique_channels:
            messages.append(FeedbackMessage(
                message_type="info",
                severity="info",
                title="Unique Channel Opportunity",
                content=(
                    f"You're allocating to channels competitors aren't using: "
                    f"{', '.join(unique_channels)}. This could be a differentiation "
                    f"opportunity or require validation."
                ),
                extra_data={"unique_channels": list(unique_channels)},
            ))

        return messages

    def _generate_recommendations(
        self,
        parsed: ParsedAllocationResult,
        run: Optional[Run],
    ) -> List[FeedbackMessage]:
        """Generate actionable recommendations."""
        messages: List[FeedbackMessage] = []

        if not parsed.allocations:
            return messages

        # Recommend based on KPI if available
        if run and run.brand_kpi:
            kpi_recommendations = self._get_kpi_recommendations(
                run.brand_kpi, parsed.allocations
            )
            messages.extend(kpi_recommendations)

        return messages

    def _get_kpi_recommendations(
        self,
        brand_kpi: str,
        allocations: List[ChannelAllocation],
    ) -> List[FeedbackMessage]:
        """Generate KPI-specific recommendations."""
        messages: List[FeedbackMessage] = []

        # Channel effectiveness by KPI (simplified heuristics)
        kpi_channel_effectiveness = {
            "adaware": ["TV", "Video", "Social Media", "OOH"],
            "aided": ["TV", "Search", "Display", "Social Media"],
            "consider": ["Search", "Social Media", "Influencer", "Native"],
        }

        effective_channels = kpi_channel_effectiveness.get(brand_kpi.lower(), [])
        if not effective_channels:
            return messages

        allocated_channels = {a.channel for a in allocations if a.percentage > 0}
        effective_allocated = allocated_channels.intersection(set(effective_channels))

        if not effective_allocated:
            messages.append(FeedbackMessage(
                message_type="recommendation",
                severity="info",
                title=f"KPI Optimization Opportunity",
                content=(
                    f"For improving {brand_kpi}, consider allocating to channels "
                    f"typically effective for this metric: {', '.join(effective_channels[:3])}."
                ),
                extra_data={
                    "kpi": brand_kpi,
                    "recommended_channels": effective_channels[:3],
                },
            ))
        elif len(effective_allocated) < 2:
            missing = set(effective_channels[:3]) - effective_allocated
            if missing:
                messages.append(FeedbackMessage(
                    message_type="recommendation",
                    severity="info",
                    title=f"Consider Additional Channels for {brand_kpi.upper()}",
                    content=(
                        f"To maximize {brand_kpi}, you might also consider: "
                        f"{', '.join(missing)}."
                    ),
                    extra_data={
                        "kpi": brand_kpi,
                        "suggested_channels": list(missing),
                    },
                ))

        return messages

    def _generate_summary(
        self,
        parsed: ParsedAllocationResult,
        run: Optional[Run],
    ) -> Optional[FeedbackMessage]:
        """Generate a summary message."""
        if not parsed.allocations:
            return FeedbackMessage(
                message_type="summary",
                severity="info",
                title="Allocation Summary",
                content="No valid allocations were generated. Please review the errors above.",
            )

        # Build summary text
        sorted_allocs = sorted(
            parsed.allocations,
            key=lambda a: a.percentage,
            reverse=True
        )

        top_channels = sorted_allocs[:3]
        top_desc = ", ".join(
            f"{a.channel} ({float(a.percentage):.1f}%)"
            for a in top_channels
        )

        # Use LLM summary if available
        if parsed.summary:
            content = parsed.summary
        else:
            customer = run.customer_name if run else "your brand"
            kpi = run.brand_kpi if run else "brand awareness"

            content = (
                f"Based on competitor analysis and market data, "
                f"the recommended allocation for {customer} focuses on {top_desc}. "
            )

            if parsed.confidence:
                content += f"Model confidence: {float(parsed.confidence):.0%}. "

            content += (
                f"This allocation is optimized for {kpi} performance "
                f"based on industry benchmarks."
            )

        return FeedbackMessage(
            message_type="summary",
            severity="info",
            title="Allocation Strategy Summary",
            content=content,
            extra_data={
                "top_channels": [
                    {"channel": a.channel, "percentage": float(a.percentage)}
                    for a in top_channels
                ],
                "total_channels": len(parsed.allocations),
                "confidence": float(parsed.confidence) if parsed.confidence else None,
            },
        )
