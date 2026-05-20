"""Tests for feedback generation service."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.services.mediamix.output_parsing import (
    ParsedAllocationResult,
    ChannelAllocation,
    ValidationIssue,
)
from src.services.mediamix.feedback_generation import (
    FeedbackGenerationService,
    FeedbackGenerationResult,
    FeedbackMessage,
    LOW_CONFIDENCE_THRESHOLD,
    VERY_LOW_CONFIDENCE_THRESHOLD,
    HIGH_CONCENTRATION_THRESHOLD,
    VERY_HIGH_CONCENTRATION_THRESHOLD,
)


class TestFeedbackGenerationService:
    """Tests for FeedbackGenerationService."""

    def create_parsed_result(
        self,
        allocations: list = None,
        confidence: Decimal = "default",
        warnings: list = None,
        validation_issues: list = None,
    ) -> ParsedAllocationResult:
        """Helper to create ParsedAllocationResult for testing."""
        if allocations is None:
            allocations = [
                ChannelAllocation(channel="TV", percentage=Decimal("40")),
                ChannelAllocation(channel="Digital", percentage=Decimal("35")),
                ChannelAllocation(channel="Print", percentage=Decimal("25")),
            ]
        # Use sentinel value to distinguish between "not provided" and "explicitly None"
        if confidence == "default":
            confidence = Decimal("0.85")
        return ParsedAllocationResult(
            is_valid=True,
            allocations=allocations,
            total_percentage=Decimal("100"),
            summary="Test allocation",
            confidence=confidence,
            warnings=warnings or [],
            validation_issues=validation_issues or [],
        )

    def create_mock_run(
        self,
        customer_name: str = "Test Brand",
        brand_kpi: str = "adaware",
    ):
        """Helper to create mock Run object."""
        run = MagicMock()
        run.customer_name = customer_name
        run.brand_kpi = brand_kpi
        return run

    def test_generate_feedback_basic(self):
        """Test basic feedback generation."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result()
        run = self.create_mock_run()

        feedback = service.generate_feedback(result, run)

        assert isinstance(feedback, FeedbackGenerationResult)
        assert len(feedback.messages) > 0
        # Should have a summary
        assert any(m.message_type == "summary" for m in feedback.messages)

    def test_generate_validation_feedback(self):
        """Test feedback from validation issues."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(
            validation_issues=[
                ValidationIssue(field="test", message="Test warning", severity="warning"),
                ValidationIssue(field="test", message="Test error", severity="error"),
            ],
        )

        feedback = service.generate_feedback(result)

        # Should have warning and alert messages
        warnings = [m for m in feedback.messages if m.message_type == "warning"]
        alerts = [m for m in feedback.messages if m.message_type == "alert"]

        assert len(warnings) >= 1
        assert len(alerts) >= 1
        assert feedback.has_warnings
        assert feedback.has_alerts

    def test_generate_low_confidence_warning(self):
        """Test warning generated for low confidence."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(
            confidence=Decimal("0.55"),  # Below LOW_CONFIDENCE_THRESHOLD (0.6)
        )

        feedback = service.generate_feedback(result)

        # Should have a low confidence warning
        confidence_msgs = [
            m for m in feedback.messages
            if "confidence" in m.title.lower()
        ]
        assert len(confidence_msgs) >= 1
        assert confidence_msgs[0].severity == "warning"

    def test_generate_very_low_confidence_alert(self):
        """Test alert generated for very low confidence."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(
            confidence=Decimal("0.35"),  # Below VERY_LOW_CONFIDENCE_THRESHOLD (0.4)
        )

        feedback = service.generate_feedback(result)

        # Should have a very low confidence alert
        confidence_msgs = [
            m for m in feedback.messages
            if "confidence" in m.title.lower()
        ]
        assert len(confidence_msgs) >= 1
        assert confidence_msgs[0].severity == "error"
        assert feedback.has_alerts

    def test_generate_no_confidence_info(self):
        """Test info message when confidence is not available."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(confidence=None)

        feedback = service.generate_feedback(result)

        # Should have info about missing confidence
        info_msgs = [
            m for m in feedback.messages
            if "confidence" in m.title.lower() and m.message_type == "info"
        ]
        assert len(info_msgs) >= 1

    def test_generate_high_concentration_warning(self):
        """Test warning for high channel concentration."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(
            allocations=[
                ChannelAllocation(channel="TV", percentage=Decimal("60")),  # Above 50% threshold
                ChannelAllocation(channel="Digital", percentage=Decimal("40")),
            ],
        )

        feedback = service.generate_feedback(result)

        # Should have concentration warning (title is "Concentrated Allocation")
        concentration_msgs = [
            m for m in feedback.messages
            if "concentrated" in m.title.lower()
        ]
        assert len(concentration_msgs) >= 1
        assert concentration_msgs[0].severity == "warning"

    def test_generate_very_high_concentration_alert(self):
        """Test alert for very high channel concentration."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(
            allocations=[
                ChannelAllocation(channel="TV", percentage=Decimal("75")),  # Above 70%
                ChannelAllocation(channel="Digital", percentage=Decimal("25")),
            ],
        )

        feedback = service.generate_feedback(result)

        # Should have concentration alert
        concentration_msgs = [
            m for m in feedback.messages
            if "concentration" in m.title.lower() or "concentrated" in m.title.lower()
        ]
        assert len(concentration_msgs) >= 1
        assert concentration_msgs[0].severity == "error"
        assert feedback.has_alerts

    def test_generate_limited_diversification_warning(self):
        """Test warning for limited diversification."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(
            allocations=[
                ChannelAllocation(channel="TV", percentage=Decimal("50")),
                ChannelAllocation(channel="Digital", percentage=Decimal("48")),
                ChannelAllocation(channel="Print", percentage=Decimal("2")),  # Too small
            ],
        )

        feedback = service.generate_feedback(result)

        # Should have diversification warning (only 2 significant channels)
        diversification_msgs = [
            m for m in feedback.messages
            if "diversification" in m.title.lower()
        ]
        assert len(diversification_msgs) >= 1

    def test_generate_small_allocation_info(self):
        """Test info message for small allocations."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(
            allocations=[
                ChannelAllocation(channel="TV", percentage=Decimal("50")),
                ChannelAllocation(channel="Digital", percentage=Decimal("45")),
                ChannelAllocation(channel="Print", percentage=Decimal("3")),
                ChannelAllocation(channel="Radio", percentage=Decimal("2")),
            ],
        )

        feedback = service.generate_feedback(result)

        # Should have info about small allocations
        small_msgs = [
            m for m in feedback.messages
            if "small" in m.title.lower()
        ]
        assert len(small_msgs) >= 1

    def test_generate_competitor_gap_warning(self):
        """Test warning for competitor channel gaps."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(
            allocations=[
                ChannelAllocation(channel="TV", percentage=Decimal("50")),
                ChannelAllocation(channel="Digital", percentage=Decimal("50")),
            ],
        )

        competitor_data = {
            "channels_used": {"TV", "Digital", "Radio", "Print"},
        }

        feedback = service.generate_feedback(result, competitor_data=competitor_data)

        # Should have competitor gap warning
        gap_msgs = [
            m for m in feedback.messages
            if "competitor" in m.title.lower() and "gap" in m.title.lower()
        ]
        assert len(gap_msgs) >= 1
        assert "Radio" in gap_msgs[0].content or "Print" in gap_msgs[0].content

    def test_generate_unique_channel_info(self):
        """Test info for channels unique to us."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(
            allocations=[
                ChannelAllocation(channel="TV", percentage=Decimal("40")),
                ChannelAllocation(channel="Podcast", percentage=Decimal("30")),
                ChannelAllocation(channel="Influencer", percentage=Decimal("30")),
            ],
        )

        competitor_data = {
            "channels_used": {"TV"},  # Competitors only use TV
        }

        feedback = service.generate_feedback(result, competitor_data=competitor_data)

        # Should have unique channel opportunity info
        unique_msgs = [
            m for m in feedback.messages
            if "unique" in m.title.lower()
        ]
        assert len(unique_msgs) >= 1

    def test_generate_kpi_recommendations(self):
        """Test KPI-specific recommendations."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(
            allocations=[
                ChannelAllocation(channel="Print", percentage=Decimal("50")),
                ChannelAllocation(channel="Direct Mail", percentage=Decimal("50")),
            ],
        )
        run = self.create_mock_run(brand_kpi="adaware")

        feedback = service.generate_feedback(result, run)

        # Should recommend channels good for adaware (TV, Video, Social Media, OOH)
        recommendations = [
            m for m in feedback.messages
            if m.message_type == "recommendation"
        ]
        assert len(recommendations) >= 1

    def test_generate_summary_with_llm_summary(self):
        """Test that LLM summary is used if available."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = ParsedAllocationResult(
            is_valid=True,
            allocations=[
                ChannelAllocation(channel="TV", percentage=Decimal("100")),
            ],
            total_percentage=Decimal("100"),
            summary="Custom LLM summary here",
            confidence=Decimal("0.9"),
        )

        feedback = service.generate_feedback(result)

        summary_msgs = [m for m in feedback.messages if m.message_type == "summary"]
        assert len(summary_msgs) == 1
        assert "Custom LLM summary here" in summary_msgs[0].content

    def test_generate_summary_without_llm_summary(self):
        """Test summary generation when LLM doesn't provide one."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = ParsedAllocationResult(
            is_valid=True,
            allocations=[
                ChannelAllocation(channel="TV", percentage=Decimal("60")),
                ChannelAllocation(channel="Digital", percentage=Decimal("40")),
            ],
            total_percentage=Decimal("100"),
            summary=None,  # No LLM summary
            confidence=Decimal("0.8"),
        )
        run = self.create_mock_run()

        feedback = service.generate_feedback(result, run)

        summary_msgs = [m for m in feedback.messages if m.message_type == "summary"]
        assert len(summary_msgs) == 1
        assert "TV" in summary_msgs[0].content
        assert feedback.summary_generated

    def test_generate_feedback_empty_allocations(self):
        """Test feedback when no valid allocations."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = ParsedAllocationResult(
            is_valid=False,
            allocations=[],
            total_percentage=Decimal("0"),
        )

        feedback = service.generate_feedback(result)

        # Should have a summary indicating no allocations
        summary_msgs = [m for m in feedback.messages if m.message_type == "summary"]
        assert len(summary_msgs) == 1
        assert "no valid allocations" in summary_msgs[0].content.lower()

    def test_warnings_from_llm_response(self):
        """Test that warnings from parsed result are included."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(
            warnings=["Limited data for automotive industry", "Q4 data incomplete"],
        )

        feedback = service.generate_feedback(result)

        warning_msgs = [
            m for m in feedback.messages
            if m.message_type == "warning" and "data" in m.title.lower()
        ]
        assert len(warning_msgs) >= 2

    def test_feedback_message_extra_data(self):
        """Test that extra_data is populated correctly."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)
        result = self.create_parsed_result(
            allocations=[
                ChannelAllocation(channel="TV", percentage=Decimal("75")),
                ChannelAllocation(channel="Digital", percentage=Decimal("25")),
            ],
            confidence=Decimal("0.35"),
        )

        feedback = service.generate_feedback(result)

        # Check concentration alert has extra_data
        concentration_msgs = [
            m for m in feedback.messages
            if "concentration" in m.title.lower() or "concentrated" in m.title.lower()
        ]
        assert concentration_msgs[0].extra_data is not None
        assert concentration_msgs[0].extra_data.get("channel") == "TV"
        assert concentration_msgs[0].extra_data.get("percentage") == 75.0

        # Check confidence alert has extra_data
        confidence_msgs = [
            m for m in feedback.messages
            if "confidence" in m.title.lower()
        ]
        assert confidence_msgs[0].extra_data is not None
        assert confidence_msgs[0].extra_data.get("confidence") == 0.35

    def test_feedback_flags(self):
        """Test has_warnings and has_alerts flags."""
        service = FeedbackGenerationService.__new__(FeedbackGenerationService)

        # Result with no issues
        good_result = self.create_parsed_result(
            confidence=Decimal("0.9"),
            allocations=[
                ChannelAllocation(channel="TV", percentage=Decimal("35")),
                ChannelAllocation(channel="Digital", percentage=Decimal("35")),
                ChannelAllocation(channel="Print", percentage=Decimal("30")),
            ],
        )
        good_feedback = service.generate_feedback(good_result)
        assert not good_feedback.has_alerts  # Good confidence, no concentration

        # Result with warning
        warning_result = self.create_parsed_result(confidence=Decimal("0.55"))
        warning_feedback = service.generate_feedback(warning_result)
        assert warning_feedback.has_warnings

        # Result with alert
        alert_result = self.create_parsed_result(confidence=Decimal("0.3"))
        alert_feedback = service.generate_feedback(alert_result)
        assert alert_feedback.has_alerts
