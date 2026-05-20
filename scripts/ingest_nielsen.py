"""Nielsen data ingestion script.

Parses Nielsen advertising spend data from Excel/CSV files and loads
into the nielsen_spend table.

Expected columns:
- Brand/Marke: Brand name
- Wirtschaftsgruppe: Industry classification
- Year/Jahr: Year
- Month/Monat: Month (German or numeric)
- Channel/Kanal: Advertising channel
- Spend/Ausgaben: Spend amount in EUR

Usage:
    python -m scripts.ingest_nielsen data/nielsen_sample.xlsx
    python -m scripts.ingest_nielsen data/nielsen_sample.csv --sheet "Sheet1"
"""

import argparse
import asyncio
import logging
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.session import async_session_factory
from src.db.models import NielsenSpend
from src.utils.date_utils import parse_german_month, parse_year_month

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Column name mappings (German -> English)
COLUMN_MAPPINGS = {
    # Brand name
    "marke": "brand_name",
    "brand": "brand_name",
    "brand_name": "brand_name",
    "markenname": "brand_name",
    # Industry
    "wirtschaftsgruppe": "wirtschaftsgruppe",
    "industry": "wirtschaftsgruppe",
    "branche": "wirtschaftsgruppe",
    # Year
    "jahr": "year",
    "year": "year",
    # Month
    "monat": "month",
    "month": "month",
    # Channel
    "kanal": "channel",
    "channel": "channel",
    "medium": "channel",
    "medienkanal": "channel",
    # Spend
    "ausgaben": "spend_eur",
    "spend": "spend_eur",
    "spend_eur": "spend_eur",
    "werbeausgaben": "spend_eur",
    "budget": "spend_eur",
}

REQUIRED_COLUMNS = ["brand_name", "wirtschaftsgruppe", "year", "month", "channel", "spend_eur"]


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to expected format."""
    df.columns = df.columns.str.lower().str.strip()

    rename_map = {}
    for col in df.columns:
        if col in COLUMN_MAPPINGS:
            rename_map[col] = COLUMN_MAPPINGS[col]

    df = df.rename(columns=rename_map)
    return df


def parse_spend(value) -> Optional[Decimal]:
    """Parse spend value to Decimal."""
    if pd.isna(value):
        return None

    if isinstance(value, (int, float)):
        return Decimal(str(value))

    # Handle string values
    value_str = str(value).strip()

    # Remove currency symbols and thousands separators
    value_str = value_str.replace("€", "").replace("EUR", "").strip()
    value_str = value_str.replace(".", "").replace(",", ".")  # German number format

    try:
        return Decimal(value_str)
    except InvalidOperation:
        return None


def parse_row(row: pd.Series, source_file: str) -> Optional[dict]:
    """Parse a single row to a NielsenSpend dict."""
    try:
        # Brand name
        brand_name = str(row.get("brand_name", "")).strip()
        if not brand_name:
            return None

        # Wirtschaftsgruppe
        wirtschaftsgruppe = str(row.get("wirtschaftsgruppe", "")).strip()
        if not wirtschaftsgruppe:
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

        # Parse month (might be German name)
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

        # Channel
        channel = str(row.get("channel", "")).strip()
        if not channel:
            return None

        # Spend
        spend_eur = parse_spend(row.get("spend_eur"))
        if spend_eur is None:
            return None

        return {
            "brand_name": brand_name,
            "wirtschaftsgruppe": wirtschaftsgruppe,
            "year": year,
            "month": month,
            "channel": channel,
            "spend_eur": spend_eur,
            "source_file": source_file,
        }

    except Exception as e:
        logger.warning(f"Failed to parse row: {e}")
        return None


def load_file(file_path: Path, sheet_name: Optional[str] = None) -> pd.DataFrame:
    """Load Excel or CSV file into DataFrame."""
    suffix = file_path.suffix.lower()

    if suffix in [".xlsx", ".xls"]:
        if sheet_name:
            df = pd.read_excel(file_path, sheet_name=sheet_name)
        else:
            df = pd.read_excel(file_path)
    elif suffix == ".csv":
        # Try different encodings
        for encoding in ["utf-8", "latin-1", "cp1252"]:
            try:
                df = pd.read_csv(file_path, encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError(f"Could not decode CSV file: {file_path}")
    else:
        raise ValueError(f"Unsupported file format: {suffix}")

    return df


async def ingest_nielsen(
    file_path: Path,
    sheet_name: Optional[str] = None,
    batch_size: int = 1000,
    dry_run: bool = False,
) -> dict:
    """Ingest Nielsen data from file.

    Args:
        file_path: Path to Excel/CSV file
        sheet_name: Sheet name for Excel files
        batch_size: Number of records to insert per batch
        dry_run: If True, validate but don't insert

    Returns:
        Dict with ingestion statistics
    """
    logger.info(f"Loading file: {file_path}")
    df = load_file(file_path, sheet_name)
    logger.info(f"Loaded {len(df)} rows")

    # Normalize columns
    df = normalize_columns(df)
    logger.info(f"Columns after normalization: {list(df.columns)}")

    # Check required columns
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

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
                nielsen_spend = NielsenSpend(**record)
                session.add(nielsen_spend)
            await session.commit()
            inserted += len(batch)
            logger.info(f"Inserted {inserted}/{len(records)} records")

    return {"total_rows": len(df), "valid_records": len(records), "errors": errors, "inserted": inserted}


async def main():
    parser = argparse.ArgumentParser(description="Ingest Nielsen advertising spend data")
    parser.add_argument("file", type=Path, help="Path to Excel/CSV file")
    parser.add_argument("--sheet", type=str, help="Sheet name for Excel files")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for inserts")
    parser.add_argument("--dry-run", action="store_true", help="Validate without inserting")

    args = parser.parse_args()

    if not args.file.exists():
        logger.error(f"File not found: {args.file}")
        sys.exit(1)

    try:
        stats = await ingest_nielsen(
            args.file,
            sheet_name=args.sheet,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
        logger.info(f"Ingestion complete: {stats}")
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
