"""Tests for data ingestion scripts."""

import tempfile
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
import pytest_asyncio

from scripts.ingest_nielsen import (
    normalize_columns as normalize_nielsen_columns,
    parse_spend,
    parse_row as parse_nielsen_row,
)
from scripts.ingest_yougov import (
    normalize_columns as normalize_yougov_columns,
    parse_kpi_value,
    parse_row as parse_yougov_row,
)


class TestNielsenIngestion:
    """Tests for Nielsen data ingestion."""

    def test_normalize_columns_german(self):
        """Test normalizing German column names."""
        df = pd.DataFrame(columns=["Marke", "Wirtschaftsgruppe", "Jahr", "Monat", "Kanal", "Ausgaben"])
        df = normalize_nielsen_columns(df)
        assert "brand_name" in df.columns
        assert "wirtschaftsgruppe" in df.columns
        assert "year" in df.columns
        assert "month" in df.columns
        assert "channel" in df.columns
        assert "spend_eur" in df.columns

    def test_normalize_columns_english(self):
        """Test normalizing English column names."""
        df = pd.DataFrame(columns=["Brand", "Industry", "Year", "Month", "Channel", "Spend"])
        df = normalize_nielsen_columns(df)
        assert "brand_name" in df.columns
        assert "wirtschaftsgruppe" in df.columns
        assert "year" in df.columns
        assert "month" in df.columns
        assert "channel" in df.columns
        assert "spend_eur" in df.columns

    def test_parse_spend_numeric(self):
        """Test parsing numeric spend values."""
        assert parse_spend(1000.50) == Decimal("1000.50")
        assert parse_spend(100000) == Decimal("100000")

    def test_parse_spend_string(self):
        """Test parsing string spend values (German format: . = thousands, , = decimal)."""
        # Note: Function is designed for German format where . is thousands separator
        assert parse_spend("1.000,50") == Decimal("1000.50")  # German format
        assert parse_spend("€ 1.000,50") == Decimal("1000.50")
        assert parse_spend("1000,50") == Decimal("1000.50")  # No thousands separator
        assert parse_spend("EUR 1.000,50") == Decimal("1000.50")

    def test_parse_spend_invalid(self):
        """Test parsing invalid spend values."""
        assert parse_spend(None) is None
        assert parse_spend(float("nan")) is None
        assert parse_spend("invalid") is None

    def test_parse_row_valid(self):
        """Test parsing a valid Nielsen row."""
        row = pd.Series({
            "brand_name": "BMW",
            "wirtschaftsgruppe": "Automotive",
            "year": 2023,
            "month": 1,
            "channel": "TV",
            "spend_eur": 100000,
        })
        result = parse_nielsen_row(row, "test.csv")
        assert result is not None
        assert result["brand_name"] == "BMW"
        assert result["wirtschaftsgruppe"] == "Automotive"
        assert result["year"] == 2023
        assert result["month"] == 1
        assert result["channel"] == "TV"
        assert result["spend_eur"] == Decimal("100000")

    def test_parse_row_german_month(self):
        """Test parsing row with German month name."""
        row = pd.Series({
            "brand_name": "BMW",
            "wirtschaftsgruppe": "Automotive",
            "year": 2023,
            "month": "Januar",
            "channel": "TV",
            "spend_eur": 100000,
        })
        result = parse_nielsen_row(row, "test.csv")
        assert result is not None
        assert result["month"] == 1

    def test_parse_row_missing_brand(self):
        """Test parsing row with missing brand."""
        row = pd.Series({
            "brand_name": "",
            "wirtschaftsgruppe": "Automotive",
            "year": 2023,
            "month": 1,
            "channel": "TV",
            "spend_eur": 100000,
        })
        result = parse_nielsen_row(row, "test.csv")
        assert result is None

    def test_parse_row_invalid_month(self):
        """Test parsing row with invalid month."""
        row = pd.Series({
            "brand_name": "BMW",
            "wirtschaftsgruppe": "Automotive",
            "year": 2023,
            "month": 13,
            "channel": "TV",
            "spend_eur": 100000,
        })
        result = parse_nielsen_row(row, "test.csv")
        assert result is None


class TestYouGovIngestion:
    """Tests for YouGov data ingestion."""

    def test_normalize_columns_german(self):
        """Test normalizing German column names."""
        df = pd.DataFrame(columns=["Marke", "Sektor", "Jahr", "Monat", "Werbeerinnerung", "Gestützte_Bekanntheit", "Kaufbereitschaft"])
        df = normalize_yougov_columns(df)
        assert "brand_label" in df.columns
        assert "sector" in df.columns
        assert "year" in df.columns
        assert "month" in df.columns
        assert "adaware" in df.columns
        assert "aided" in df.columns
        assert "consider" in df.columns

    def test_parse_kpi_value_numeric(self):
        """Test parsing numeric KPI values."""
        assert parse_kpi_value(45.5) == Decimal("45.5")
        assert parse_kpi_value(0) == Decimal("0")
        assert parse_kpi_value(100) == Decimal("100")

    def test_parse_kpi_value_string(self):
        """Test parsing string KPI values."""
        assert parse_kpi_value("45.5") == Decimal("45.5")
        assert parse_kpi_value("45,5") == Decimal("45.5")  # German format
        assert parse_kpi_value("45.5%") == Decimal("45.5")

    def test_parse_kpi_value_invalid(self):
        """Test parsing invalid KPI values."""
        assert parse_kpi_value(None) is None
        assert parse_kpi_value(float("nan")) is None
        assert parse_kpi_value("invalid") is None
        assert parse_kpi_value(-10) is None  # Negative
        assert parse_kpi_value(150) is None  # Over 100

    def test_parse_row_valid(self):
        """Test parsing a valid YouGov row."""
        row = pd.Series({
            "brand_label": "BMW",
            "sector": "Automotive",
            "year": 2023,
            "month": 1,
            "adaware": 45.5,
            "aided": 78.2,
            "consider": 32.1,
        })
        result = parse_yougov_row(row, "test.csv")
        assert result is not None
        assert result["brand_label"] == "BMW"
        assert result["sector"] == "Automotive"
        assert result["year"] == 2023
        assert result["month"] == 1
        assert result["adaware"] == Decimal("45.5")
        assert result["aided"] == Decimal("78.2")
        assert result["consider"] == Decimal("32.1")

    def test_parse_row_partial_kpi(self):
        """Test parsing row with only some KPI values."""
        row = pd.Series({
            "brand_label": "BMW",
            "sector": "Automotive",
            "year": 2023,
            "month": 1,
            "adaware": 45.5,
            "aided": None,
            "consider": None,
        })
        result = parse_yougov_row(row, "test.csv")
        assert result is not None
        assert result["adaware"] == Decimal("45.5")
        assert result["aided"] is None
        assert result["consider"] is None

    def test_parse_row_no_kpi(self):
        """Test parsing row with no KPI values returns None."""
        row = pd.Series({
            "brand_label": "BMW",
            "sector": "Automotive",
            "year": 2023,
            "month": 1,
            "adaware": None,
            "aided": None,
            "consider": None,
        })
        result = parse_yougov_row(row, "test.csv")
        assert result is None

    def test_parse_row_german_month(self):
        """Test parsing row with German month name."""
        row = pd.Series({
            "brand_label": "BMW",
            "sector": "Automotive",
            "year": 2023,
            "month": "März",
            "adaware": 45.5,
        })
        result = parse_yougov_row(row, "test.csv")
        assert result is not None
        assert result["month"] == 3
