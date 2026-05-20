"""YouGov data ingestion script.

Parses YouGov brand KPI data from CSV files and loads into the yougov_kpi table.

Expected columns:
- Brand/Marke: Brand label
- Sector: Industry sector
- Year/Jahr: Year
- Month/Monat: Month
- Adaware: Ad awareness metric (0-100)
- Aided: Aided brand awareness (0-100)
- Consider: Purchase consideration (0-100)

Usage:
    python -m scripts.ingest_yougov data/yougov_sample.csv
"""

import argparse
import asyncio
import logging
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.session import async_session_factory
from src.db.models import YouGovKPI
from src.utils.date_utils import parse_german_month, parse_year_month

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Column name mappings
COLUMN_MAPPINGS = {
    # Brand
    "marke": "brand_label",
    "brand": "brand_label",
    "brand_label": "brand_label",
    "markenname": "brand_label",
    # Sector
    "sector": "sector",
    "sektor": "sector",
    "branche": "sector",
    "industry": "sector",
    # Year
    "jahr": "year",
    "year": "year",
    # Month
    "monat": "month",
    "month": "month",
    # KPI metrics
    "adaware": "adaware",
    "ad_aware": "adaware",
    "ad_awareness": "adaware",
    "werbeerinnerung": "adaware",
    "aided": "aided",
    "aided_awareness": "aided",
    "gestützte_bekanntheit": "aided",
    "consider": "consider",
    "consideration": "consider",
    "kaufbereitschaft": "consider",
}

REQUIRED_COLUMNS = ["brand_label", "sector", "year", "month"]
KPI_COLUMNS = ["adaware", "aided", "consider"]


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to expected format."""
    df.columns = df.columns.str.lower().str.strip()

    rename_map = {}
    for col in df.columns:
        if col in COLUMN_MAPPINGS:
            rename_map[col] = COLUMN_MAPPINGS[col]

    df = df.rename(columns=rename_map)
    return df


def parse_kpi_value(value) -> Optional[Decimal]:
    """Parse KPI value to Decimal (0-100 range)."""
    if pd.isna(value):
        return None

    if isinstance(value, (int, float)):
        val = Decimal(str(value))
    else:
        # Handle string values
        value_str = str(value).strip()
        value_str = value_str.replace("%", "").replace(",", ".").strip()

        try:
            val = Decimal(value_str)
        except InvalidOperation:
            return None

    # Ensure value is in 0-100 range
    if val < 0:
        return None
    if val > 100:
        # Might be in 0-1 range, convert
        if val <= 1:
            val = val * 100
        else:
            return None

    return val


def parse_row(row: pd.Series, source_file: str) -> Optional[dict]:
    """Parse a single row to a YouGovKPI dict."""
    try:
        # Brand label
        brand_label = str(row.get("brand_label", "")).strip()
        if not brand_label:
            return None

        # Sector
        sector = str(row.get("sector", "")).strip()
        if not sector:
            return None

        # Year and month
        year = row.get("year")
        month = row.get("month")

        # Handle combined year-month column
        if pd.isna(year) or pd.isna(month):
            year_month = row.get("year_month") or row.get("zeitraum") or row.get("period")
            if year_month and not pd.isna(year_month):
                parsed = parse_year_month(str(year_month))
                if parsed:
                    year, month = parsed

        # Parse year
        if pd.isna(year):
            return None
        year = int(year)

        # Parse month
        if pd.isna(month):
            return None
        if isinstance(month, str):
            month = parse_german_month(month)
            if month is None:
                return None
        else:
            month = int(month)

        if not (1 <= month <= 12):
            return None

        # Parse KPI values (all optional but at least one required)
        adaware = parse_kpi_value(row.get("adaware"))
        aided = parse_kpi_value(row.get("aided"))
        consider = parse_kpi_value(row.get("consider"))

        # At least one KPI should be present
        if adaware is None and aided is None and consider is None:
            return None

        return {
            "brand_label": brand_label,
            "sector": sector,
            "year": year,
            "month": month,
            "adaware": adaware,
            "aided": aided,
            "consider": consider,
            "source_file": source_file,
        }

    except Exception as e:
        logger.warning(f"Failed to parse row: {e}")
        return None


def load_file(file_path: Path) -> pd.DataFrame:
    """Load CSV file into DataFrame."""
    # Try different encodings
    for encoding in ["utf-8", "latin-1", "cp1252"]:
        try:
            df = pd.read_csv(file_path, encoding=encoding)
            return df
        except UnicodeDecodeError:
            continue

    raise ValueError(f"Could not decode CSV file: {file_path}")


async def ingest_yougov(
    file_path: Path,
    batch_size: int = 1000,
    dry_run: bool = False,
) -> dict:
    """Ingest YouGov data from file.

    Args:
        file_path: Path to CSV file
        batch_size: Number of records to insert per batch
        dry_run: If True, validate but don't insert

    Returns:
        Dict with ingestion statistics
    """
    logger.info(f"Loading file: {file_path}")
    df = load_file(file_path)
    logger.info(f"Loaded {len(df)} rows")

    # Normalize columns
    df = normalize_columns(df)
    logger.info(f"Columns after normalization: {list(df.columns)}")

    # Check required columns
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Check for at least one KPI column
    has_kpi = any(col in df.columns for col in KPI_COLUMNS)
    if not has_kpi:
        raise ValueError(f"At least one KPI column required: {KPI_COLUMNS}")

    # Parse rows
    source_file = file_path.name
    records = []
    errors = 0

    for idx, row in df.iterrows():
        parsed = parse_row(row, source_file)
        if parsed:
            records.append(parsed)
        else:
            errors += 1

    logger.info(f"Parsed {len(records)} valid records, {errors} errors")

    if dry_run:
        logger.info("Dry run - not inserting records")
        return {"total_rows": len(df), "valid_records": len(records), "errors": errors, "inserted": 0}

    # Insert records
    inserted = 0
    async with async_session_factory() as session:
        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            for record in batch:
                yougov_kpi = YouGovKPI(**record)
                session.add(yougov_kpi)
            await session.commit()
            inserted += len(batch)
            logger.info(f"Inserted {inserted}/{len(records)} records")

    return {"total_rows": len(df), "valid_records": len(records), "errors": errors, "inserted": inserted}


async def main():
    parser = argparse.ArgumentParser(description="Ingest YouGov brand KPI data")
    parser.add_argument("file", type=Path, help="Path to CSV file")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for inserts")
    parser.add_argument("--dry-run", action="store_true", help="Validate without inserting")

    args = parser.parse_args()

    if not args.file.exists():
        logger.error(f"File not found: {args.file}")
        sys.exit(1)

    try:
        stats = await ingest_yougov(
            args.file,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
        logger.info(f"Ingestion complete: {stats}")
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
