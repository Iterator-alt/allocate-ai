"""Tests for output parsing service."""

from decimal import Decimal

import pytest

from src.services.llm_gateway.client import LLMResponse
from src.services.mediamix.output_parsing import (
    OutputParsingService,
    ParsedAllocationResult,
    ChannelAllocation,
    ValidationIssue,
    VALID_CHANNELS,
)


class TestOutputParsingService:
    """Tests for OutputParsingService."""

    def create_llm_response(
        self,
        content: str = "",
        parsed_json: dict = None,
    ) -> LLMResponse:
        """Helper to create LLMResponse for testing."""
        return LLMResponse(
            content=content,
            parsed_json=parsed_json,
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=200,
            total_tokens=300,
            latency_ms=1000,
            finish_reason="stop",
        )

    def test_parse_valid_response(self):
        """Test parsing a valid LLM response."""
        response = self.create_llm_response(
            content='{"allocations": [...]}',
            parsed_json={
                "allocations": [
                    {"channel": "TV", "percentage": 40.0, "rationale": "High reach"},
                    {"channel": "Digital", "percentage": 35.0, "rationale": "Cost efficient"},
                    {"channel": "Print", "percentage": 25.0, "rationale": "Brand building"},
                ],
                "total_percentage": 100.0,
                "summary": "A balanced media mix",
                "confidence": 0.85,
                "warnings": [],
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert result.is_valid
        assert len(result.allocations) == 3
        assert result.total_percentage == Decimal("100")
        assert result.summary == "A balanced media mix"
        assert result.confidence == Decimal("0.85")
        assert not result.validation_issues

    def test_parse_response_with_amounts(self):
        """Test parsing with budget amounts."""
        response = self.create_llm_response(
            parsed_json={
                "allocations": [
                    {"channel": "TV", "percentage": 50.0, "amount": 500000},
                    {"channel": "Radio", "percentage": 50.0, "amount": 500000},
                ],
                "summary": "Split between TV and Radio",
                "confidence": 0.9,
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response, total_budget=Decimal("1000000"))

        assert result.is_valid
        assert result.allocations[0].amount == Decimal("500000")
        assert result.allocations[1].amount == Decimal("500000")

    def test_parse_calculates_amounts_from_budget(self):
        """Test that amounts are calculated from budget if not provided."""
        response = self.create_llm_response(
            parsed_json={
                "allocations": [
                    {"channel": "TV", "percentage": 60.0},
                    {"channel": "Digital", "percentage": 40.0},
                ],
                "confidence": 0.8,
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response, total_budget=Decimal("100000"))

        assert result.is_valid
        assert result.allocations[0].amount == Decimal("60000")
        assert result.allocations[1].amount == Decimal("40000")

    def test_parse_empty_response(self):
        """Test handling empty response."""
        response = self.create_llm_response(content="", parsed_json=None)

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert not result.is_valid
        assert any("Empty response" in i.message for i in result.validation_issues)

    def test_parse_invalid_json(self):
        """Test handling non-JSON response."""
        response = self.create_llm_response(
            content="This is not JSON",
            parsed_json=None,
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert not result.is_valid
        assert any("not valid JSON" in i.message for i in result.validation_issues)

    def test_parse_missing_allocations(self):
        """Test handling response without allocations."""
        response = self.create_llm_response(
            parsed_json={
                "summary": "No allocations provided",
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert not result.is_valid
        assert any("No allocations" in i.message for i in result.validation_issues)

    def test_parse_percentages_not_100(self):
        """Test handling percentages that don't sum to 100%."""
        response = self.create_llm_response(
            parsed_json={
                "allocations": [
                    {"channel": "TV", "percentage": 40.0},
                    {"channel": "Digital", "percentage": 30.0},
                ],
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert not result.is_valid
        assert any("sum to" in i.message.lower() for i in result.validation_issues)

    def test_parse_small_percentage_adjustment(self):
        """Test small percentage adjustments (within tolerance)."""
        response = self.create_llm_response(
            parsed_json={
                "allocations": [
                    {"channel": "TV", "percentage": 50.5},
                    {"channel": "Digital", "percentage": 49.3},  # 99.8% total
                ],
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert result.is_valid
        assert result.total_percentage == Decimal("100")
        assert len(result.warnings) > 0  # Should have adjustment warning

    def test_parse_invalid_percentage_value(self):
        """Test handling invalid percentage values."""
        response = self.create_llm_response(
            parsed_json={
                "allocations": [
                    {"channel": "TV", "percentage": "invalid"},
                    {"channel": "Digital", "percentage": 50.0},
                ],
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert not result.is_valid
        assert any("Invalid percentage" in i.message for i in result.validation_issues)

    def test_parse_negative_percentage(self):
        """Test handling negative percentages."""
        response = self.create_llm_response(
            parsed_json={
                "allocations": [
                    {"channel": "TV", "percentage": -10.0},
                    {"channel": "Digital", "percentage": 110.0},
                ],
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert not result.is_valid
        assert any("out of range" in i.message for i in result.validation_issues)

    def test_parse_unknown_channel_warning(self):
        """Test that unknown channels produce a warning."""
        response = self.create_llm_response(
            parsed_json={
                "allocations": [
                    {"channel": "TV", "percentage": 50.0},
                    {"channel": "Unknown Channel", "percentage": 50.0},
                ],
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        # Should still be valid, just with a warning
        assert result.is_valid
        warnings = [i for i in result.validation_issues if i.severity == "warning"]
        assert any("Unknown channel" in w.message for w in warnings)

    def test_parse_case_insensitive_channel(self):
        """Test case-insensitive channel matching."""
        response = self.create_llm_response(
            parsed_json={
                "allocations": [
                    {"channel": "tv", "percentage": 50.0},
                    {"channel": "DIGITAL", "percentage": 50.0},
                ],
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert result.is_valid
        # Should normalize to canonical names
        assert result.allocations[0].channel == "TV"
        assert result.allocations[1].channel == "Digital"

    def test_parse_missing_channel_name(self):
        """Test handling allocation without channel name."""
        response = self.create_llm_response(
            parsed_json={
                "allocations": [
                    {"percentage": 50.0},
                    {"channel": "Digital", "percentage": 50.0},
                ],
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert not result.is_valid
        assert any("Channel name is required" in i.message for i in result.validation_issues)

    def test_parse_confidence_out_of_range(self):
        """Test handling confidence out of range."""
        response = self.create_llm_response(
            parsed_json={
                "allocations": [
                    {"channel": "TV", "percentage": 100.0},
                ],
                "confidence": 1.5,  # Out of range
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert result.is_valid  # Still valid, confidence is clamped
        assert result.confidence == Decimal("1")  # Clamped to max

    def test_parse_llm_warnings_extracted(self):
        """Test that warnings from LLM response are extracted."""
        response = self.create_llm_response(
            parsed_json={
                "allocations": [
                    {"channel": "TV", "percentage": 100.0},
                ],
                "warnings": [
                    "Limited data for this industry",
                    "Some channels had missing data",
                ],
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert result.is_valid
        assert "Limited data for this industry" in result.warnings
        assert "Some channels had missing data" in result.warnings

    def test_to_storage_format(self):
        """Test conversion to storage format."""
        result = ParsedAllocationResult(
            is_valid=True,
            allocations=[
                ChannelAllocation(
                    channel="TV",
                    percentage=Decimal("60"),
                    amount=Decimal("600000"),
                    rationale="High reach",
                ),
                ChannelAllocation(
                    channel="Digital",
                    percentage=Decimal("40"),
                    amount=Decimal("400000"),
                    rationale="Cost efficient",
                ),
            ],
            total_percentage=Decimal("100"),
            summary="Test summary",
            confidence=Decimal("0.85"),
        )

        storage = result.to_storage_format()

        assert "channels" in storage
        assert len(storage["channels"]) == 2
        assert storage["channels"][0]["name"] == "TV"
        assert storage["channels"][0]["percentage"] == 60.0
        assert storage["channels"][0]["amount"] == 600000.0
        assert storage["total_percentage"] == 100.0

    def test_validate_against_request_missing_channels(self):
        """Test validation against requested channels."""
        result = ParsedAllocationResult(
            is_valid=True,
            allocations=[
                ChannelAllocation(channel="TV", percentage=Decimal("50")),
                ChannelAllocation(channel="Digital", percentage=Decimal("50")),
            ],
            total_percentage=Decimal("100"),
        )

        service = OutputParsingService.__new__(OutputParsingService)
        issues = service.validate_against_request(
            result,
            requested_channels=["TV", "Radio", "Print"],
        )

        # Should have warnings for Radio and Print not being allocated
        assert any("Radio" in i.message for i in issues)
        assert any("Print" in i.message for i in issues)

    def test_validate_against_request_budget_mismatch(self):
        """Test validation when amounts don't match budget."""
        result = ParsedAllocationResult(
            is_valid=True,
            allocations=[
                ChannelAllocation(channel="TV", percentage=Decimal("50"), amount=Decimal("600000")),
                ChannelAllocation(channel="Digital", percentage=Decimal("50"), amount=Decimal("600000")),
            ],
            total_percentage=Decimal("100"),
        )

        service = OutputParsingService.__new__(OutputParsingService)
        issues = service.validate_against_request(
            result,
            total_budget=Decimal("1000000"),  # 1M but allocations sum to 1.2M
        )

        assert any("differs from budget" in i.message for i in issues)

    def test_parse_alternative_field_names(self):
        """Test parsing with alternative field names."""
        response = self.create_llm_response(
            parsed_json={
                "allocations": [
                    {"name": "TV", "share_pct": 50.0, "budget_gross_eur": 500000, "reasoning": "Good ROI"},
                    {"name": "Digital", "share_pct": 50.0, "budget_gross_eur": 500000, "reasoning": "Scalable"},
                ],
            },
        )

        service = OutputParsingService.__new__(OutputParsingService)
        result = service.parse_response(response)

        assert result.is_valid
        assert result.allocations[0].channel == "TV"
        assert result.allocations[0].percentage == Decimal("50")
        assert result.allocations[0].amount == Decimal("500000")
        assert result.allocations[0].rationale == "Good ROI"
