"""Mapping tables loader script.

Loads industry and brand mapping tables from client spreadsheets.
This script handles the industry_map and brand_map tables.

Expected formats:

Industry mapping (industry_map.csv/xlsx):
- Wirtschaftsgruppe: Nielsen industry classification (German)
- Sector_Label: YouGov sector label
- Description: Optional description

Brand mapping (brand_map.csv/xlsx):
- Nielsen_Brand: Brand name in Nielsen data
- YouGov_Brand_Label: Brand label in YouGov data
- Wirtschaftsgruppe: Optional industry context
- Confidence: Optional confidence score (0-1)
- Notes: Optional notes

Usage:
    python -m scripts.load_mappings --industry data/industry_map.csv
    python -m scripts.load_mappings --brand data/brand_map.csv
    python -m scripts.load_mappings --industry data/industry_map.xlsx --brand data/brand_map.xlsx
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.session import async_session_factory
from src.db.models import IndustryMap, BrandMap

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# Industry map column mappings
INDUSTRY_COLUMN_MAPPINGS = {
    "wirtschaftsgruppe": "wirtschaftsgruppe",
    "nielsen_industry": "wirtschaftsgruppe",
    "industry": "wirtschaftsgruppe",
    "sector_label": "sector_label",
    "yougov_sector": "sector_label",
    "sector": "sector_label",
    "description": "description",
    "beschreibung": "description",
    "notes": "description",
}

# Brand map column mappings
BRAND_COLUMN_MAPPINGS = {
    "nielsen_brand": "nielsen_brand",
    "nielsen": "nielsen_brand",
    "brand_nielsen": "nielsen_brand",
    "yougov_brand_label": "yougov_brand_label",
    "yougov_brand": "yougov_brand_label",
    "yougov": "yougov_brand_label",
    "brand_yougov": "yougov_brand_label",
    "wirtschaftsgruppe": "wirtschaftsgruppe",
    "industry": "wirtschaftsgruppe",
    "confidence": "confidence",
    "score": "confidence",
    "notes": "notes",
    "anmerkungen": "notes",
}


def load_file(file_path: Path, sheet_name: Optional[str] = None) -> pd.DataFrame:
    """Load Excel or CSV file into DataFrame."""
    suffix = file_path.suffix.lower()

    if suffix in [".xlsx", ".xls"]:
        if sheet_name:
            df = pd.read_excel(file_path, sheet_name=sheet_name)
        else:
            df = pd.read_excel(file_path)
    elif suffix == ".csv":
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


def normalize_columns(df: pd.DataFrame, mappings: dict) -> pd.DataFrame:
    """Normalize column names using provided mappings."""
    df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")

    rename_map = {}
    for col in df.columns:
        if col in mappings:
            rename_map[col] = mappings[col]

    df = df.rename(columns=rename_map)
    return df


async def load_industry_mappings(
    file_path: Path,
    sheet_name: Optional[str] = None,
    clear_existing: bool = False,
    dry_run: bool = False,
) -> dict:
    """Load industry mappings from file.

    Args:
        file_path: Path to mapping file
        sheet_name: Sheet name for Excel files
        clear_existing: If True, delete existing mappings first
        dry_run: If True, validate but don't insert

    Returns:
        Dict with load statistics
    """
    logger.info(f"Loading industry mappings from: {file_path}")
    df = load_file(file_path, sheet_name)
    logger.info(f"Loaded {len(df)} rows")

    df = normalize_columns(df, INDUSTRY_COLUMN_MAPPINGS)
    logger.info(f"Columns: {list(df.columns)}")

    # Validate required columns
    if "wirtschaftsgruppe" not in df.columns:
        raise ValueError("Missing required column: wirtschaftsgruppe")
    if "sector_label" not in df.columns:
        raise ValueError("Missing required column: sector_label")

    # Parse records
    records = []
    errors = 0

    for idx, row in df.iterrows():
        wirtschaftsgruppe = str(row.get("wirtschaftsgruppe", "")).strip()
        sector_label = str(row.get("sector_label", "")).strip()

        if not wirtschaftsgruppe or not sector_label:
            errors += 1
            continue

        description = row.get("description")
        if pd.isna(description):
            description = None
        else:
            description = str(description).strip() or None

        records.append({
            "wirtschaftsgruppe": wirtschaftsgruppe,
            "sector_label": sector_label,
            "description": description,
            "is_active": True,
        })

    logger.info(f"Parsed {len(records)} valid records, {errors} errors")

    if dry_run:
        logger.info("Dry run - not inserting records")
        return {"total_rows": len(df), "valid_records": len(records), "errors": errors, "inserted": 0}

    async with async_session_factory() as session:
        if clear_existing:
            from sqlalchemy import delete
            await session.execute(delete(IndustryMap))
            logger.info("Cleared existing industry mappings")

        for record in records:
            industry_map = IndustryMap(**record)
            session.add(industry_map)

        await session.commit()

    logger.info(f"Inserted {len(records)} industry mappings")
    return {"total_rows": len(df), "valid_records": len(records), "errors": errors, "inserted": len(records)}


async def load_brand_mappings(
    file_path: Path,
    sheet_name: Optional[str] = None,
    clear_existing: bool = False,
    dry_run: bool = False,
) -> dict:
    """Load brand mappings from file.

    Args:
        file_path: Path to mapping file
        sheet_name: Sheet name for Excel files
        clear_existing: If True, delete existing mappings first
        dry_run: If True, validate but don't insert

    Returns:
        Dict with load statistics
    """
    logger.info(f"Loading brand mappings from: {file_path}")
    df = load_file(file_path, sheet_name)
    logger.info(f"Loaded {len(df)} rows")

    df = normalize_columns(df, BRAND_COLUMN_MAPPINGS)
    logger.info(f"Columns: {list(df.columns)}")

    # Validate required columns
    if "nielsen_brand" not in df.columns:
        raise ValueError("Missing required column: nielsen_brand")
    if "yougov_brand_label" not in df.columns:
        raise ValueError("Missing required column: yougov_brand_label")

    # Parse records
    records = []
    errors = 0

    for idx, row in df.iterrows():
        nielsen_brand = str(row.get("nielsen_brand", "")).strip()
        yougov_brand_label = str(row.get("yougov_brand_label", "")).strip()

        if not nielsen_brand or not yougov_brand_label:
            errors += 1
            continue

        # Optional fields
        wirtschaftsgruppe = row.get("wirtschaftsgruppe")
        if pd.isna(wirtschaftsgruppe):
            wirtschaftsgruppe = None
        else:
            wirtschaftsgruppe = str(wirtschaftsgruppe).strip() or None

        confidence = row.get("confidence")
        if pd.isna(confidence):
            confidence = None
        else:
            try:
                confidence = float(confidence)
                if not (0 <= confidence <= 1):
                    confidence = None
            except (ValueError, TypeError):
                confidence = None

        notes = row.get("notes")
        if pd.isna(notes):
            notes = None
        else:
            notes = str(notes).strip() or None

        records.append({
            "nielsen_brand": nielsen_brand,
            "yougov_brand_label": yougov_brand_label,
            "wirtschaftsgruppe": wirtschaftsgruppe,
            "confidence": confidence,
            "notes": notes,
            "is_active": True,
        })

    logger.info(f"Parsed {len(records)} valid records, {errors} errors")

    if dry_run:
        logger.info("Dry run - not inserting records")
        return {"total_rows": len(df), "valid_records": len(records), "errors": errors, "inserted": 0}

    async with async_session_factory() as session:
        if clear_existing:
            from sqlalchemy import delete
            await session.execute(delete(BrandMap))
            logger.info("Cleared existing brand mappings")

        for record in records:
            brand_map = BrandMap(**record)
            session.add(brand_map)

        await session.commit()

    logger.info(f"Inserted {len(records)} brand mappings")
    return {"total_rows": len(df), "valid_records": len(records), "errors": errors, "inserted": len(records)}


async def main():
    parser = argparse.ArgumentParser(description="Load industry and brand mapping tables")
    parser.add_argument("--industry", type=Path, help="Path to industry mapping file")
    parser.add_argument("--brand", type=Path, help="Path to brand mapping file")
    parser.add_argument("--sheet", type=str, help="Sheet name for Excel files")
    parser.add_argument("--clear", action="store_true", help="Clear existing mappings before loading")
    parser.add_argument("--dry-run", action="store_true", help="Validate without inserting")

    args = parser.parse_args()

    if not args.industry and not args.brand:
        parser.error("At least one of --industry or --brand must be specified")

    results = {}

    if args.industry:
        if not args.industry.exists():
            logger.error(f"Industry file not found: {args.industry}")
            sys.exit(1)
        try:
            results["industry"] = await load_industry_mappings(
                args.industry,
                sheet_name=args.sheet,
                clear_existing=args.clear,
                dry_run=args.dry_run,
            )
        except Exception as e:
            logger.error(f"Industry mapping load failed: {e}")
            sys.exit(1)

    if args.brand:
        if not args.brand.exists():
            logger.error(f"Brand file not found: {args.brand}")
            sys.exit(1)
        try:
            results["brand"] = await load_brand_mappings(
                args.brand,
                sheet_name=args.sheet,
                clear_existing=args.clear,
                dry_run=args.dry_run,
            )
        except Exception as e:
            logger.error(f"Brand mapping load failed: {e}")
            sys.exit(1)

    logger.info(f"Load complete: {results}")


if __name__ == "__main__":
    asyncio.run(main())
