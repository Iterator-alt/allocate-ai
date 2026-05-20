"""Tests for date utility functions."""

import pytest

from src.utils.date_utils import parse_german_month, parse_year_month


class TestParseGermanMonth:
    """Tests for parse_german_month function."""

    def test_full_german_months(self):
        """Test parsing full German month names."""
        assert parse_german_month("Januar") == 1
        assert parse_german_month("Februar") == 2
        assert parse_german_month("März") == 3
        assert parse_german_month("April") == 4
        assert parse_german_month("Mai") == 5
        assert parse_german_month("Juni") == 6
        assert parse_german_month("Juli") == 7
        assert parse_german_month("August") == 8
        assert parse_german_month("September") == 9
        assert parse_german_month("Oktober") == 10
        assert parse_german_month("November") == 11
        assert parse_german_month("Dezember") == 12

    def test_case_insensitive(self):
        """Test that parsing is case insensitive."""
        assert parse_german_month("JANUAR") == 1
        assert parse_german_month("januar") == 1
        assert parse_german_month("JaNuAr") == 1

    def test_short_german_months(self):
        """Test parsing short German month names."""
        assert parse_german_month("Jan") == 1
        assert parse_german_month("Feb") == 2
        assert parse_german_month("Mär") == 3
        assert parse_german_month("Apr") == 4
        assert parse_german_month("Jun") == 6
        assert parse_german_month("Jul") == 7
        assert parse_german_month("Aug") == 8
        assert parse_german_month("Sep") == 9
        assert parse_german_month("Okt") == 10
        assert parse_german_month("Nov") == 11
        assert parse_german_month("Dez") == 12

    def test_english_months_fallback(self):
        """Test that English month names also work."""
        assert parse_german_month("January") == 1
        assert parse_german_month("March") == 3
        assert parse_german_month("December") == 12

    def test_numeric_strings(self):
        """Test parsing numeric strings."""
        assert parse_german_month("1") == 1
        assert parse_german_month("12") == 12
        assert parse_german_month("6") == 6

    def test_invalid_values(self):
        """Test that invalid values return None."""
        assert parse_german_month("") is None
        assert parse_german_month("InvalidMonth") is None
        assert parse_german_month("13") is None
        assert parse_german_month("0") is None
        assert parse_german_month("-1") is None

    def test_with_whitespace(self):
        """Test that whitespace is handled."""
        assert parse_german_month("  Januar  ") == 1
        assert parse_german_month("  12  ") == 12

    def test_alternative_spelling(self):
        """Test alternative spellings (no umlaut)."""
        assert parse_german_month("Maerz") == 3
        assert parse_german_month("mar") == 3


class TestParseYearMonth:
    """Tests for parse_year_month function."""

    def test_iso_format(self):
        """Test parsing ISO format (YYYY-MM)."""
        assert parse_year_month("2023-01") == (2023, 1)
        assert parse_year_month("2024-12") == (2024, 12)

    def test_slash_format(self):
        """Test parsing slash format (MM/YYYY)."""
        assert parse_year_month("01/2023") == (2023, 1)
        assert parse_year_month("12/2024") == (2024, 12)

    def test_text_format_german(self):
        """Test parsing text format with German month."""
        assert parse_year_month("Januar 2023") == (2023, 1)
        assert parse_year_month("2023 Dezember") == (2023, 12)
        assert parse_year_month("März 2024") == (2024, 3)

    def test_text_format_english(self):
        """Test parsing text format with English month."""
        assert parse_year_month("January 2023") == (2023, 1)
        assert parse_year_month("2023 December") == (2023, 12)

    def test_invalid_values(self):
        """Test that invalid values return None."""
        assert parse_year_month("") is None
        assert parse_year_month("invalid") is None
        assert parse_year_month("2023") is None  # No month

    def test_with_whitespace(self):
        """Test that whitespace is handled."""
        assert parse_year_month("  2023-01  ") == (2023, 1)
        assert parse_year_month("  Januar 2023  ") == (2023, 1)
