"""Create and populate yougov and nielsen data tables on staging.

This script:
1. Creates the yougov table (new, won't affect existing tables)
2. Creates the nielsen table (new, won't affect existing tables)
3. Populates yougov from CSV file
4. Populates nielsen from Excel file

Run with: python scripts/create_data_tables_staging.py
"""

import asyncio
import pandas as pd
from datetime import datetime, date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Staging database connection
DATABASE_URL = "postgresql+asyncpg://mp_root:xs8rLdsOVM95hb27@20.79.8.67:5432/allocate_db"

# Data files
YOUGOV_CSV = Path("C:/Users/Mohit/allocate-ai/YouGov-Nielsen-Sample/20260427_YouGov_BIX_Food1-Food2_2023-2026_monthly_work.csv")
NIELSEN_XLSX = Path("C:/Users/Mohit/allocate-ai/YouGov-Nielsen-Sample/Spendings AlexD 2023 - 03 2026.xlsx")


async def create_tables(session: AsyncSession):
    """Create yougov and nielsen tables on staging."""

    # Create yougov table
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS yougov (
            id SERIAL PRIMARY KEY,
            date DATE NOT NULL,
            sector_label TEXT NOT NULL,
            brand_label TEXT NOT NULL,
            metric TEXT NOT NULL,
            score FLOAT,
            analysis_id TEXT,
            region TEXT,
            sector_id INTEGER,
            brand_id INTEGER,
            volume FLOAT,
            positives FLOAT,
            negatives FLOAT,
            neutrals FLOAT,
            positives_neutrals FLOAT,
            negatives_neutrals FLOAT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    print("[OK] Created yougov table")

    # Create indexes for yougov
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_yougov_sector ON yougov(sector_label)"))
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_yougov_brand ON yougov(brand_label)"))
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_yougov_metric ON yougov(metric)"))
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_yougov_date ON yougov(date)"))
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_yougov_sector_brand ON yougov(sector_label, brand_label)"))
    print("[OK] Created yougov indexes")

    # Create nielsen table
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS nielsen (
            id SERIAL PRIMARY KEY,
            wirtschaftsgruppe TEXT NOT NULL,
            konzern TEXT,
            firma TEXT,
            marke TEXT NOT NULL,
            produktmarke TEXT,
            jahr INTEGER NOT NULL,
            monat TEXT NOT NULL,
            mediengruppe TEXT,
            teuro FLOAT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    print("[OK] Created nielsen table")

    # Create indexes for nielsen
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_nielsen_wg ON nielsen(wirtschaftsgruppe)"))
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_nielsen_marke ON nielsen(marke)"))
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_nielsen_jahr ON nielsen(jahr)"))
    await session.execute(text("CREATE INDEX IF NOT EXISTS idx_nielsen_marke_jahr ON nielsen(marke, jahr)"))
    print("[OK] Created nielsen indexes")

    await session.commit()


async def populate_yougov(session: AsyncSession):
    """Populate yougov table from CSV file using fast COPY."""
    print(f"\nLoading YouGov data from {YOUGOV_CSV}...")

    # Read CSV
    df = pd.read_csv(YOUGOV_CSV)
    print(f"  Loaded {len(df)} rows from CSV")

    # Parse dates
    df['date'] = pd.to_datetime(df['date']).dt.date

    # Clear existing data
    await session.execute(text("TRUNCATE TABLE yougov RESTART IDENTITY"))
    print("  Cleared existing yougov data")

    # Build VALUES for bulk insert (chunks of 100 for SQL size)
    batch_size = 100
    total_inserted = 0

    for i in range(0, len(df), batch_size):
        batch = df.iloc[i:i+batch_size]
        values_list = []
        params = {}

        for j, (_, row) in enumerate(batch.iterrows()):
            idx = j
            params[f"date_{idx}"] = row["date"]
            params[f"sector_label_{idx}"] = row["sector_label"]
            params[f"brand_label_{idx}"] = row["brand_label"]
            params[f"metric_{idx}"] = row["metric"]
            params[f"score_{idx}"] = row["score"] if pd.notna(row["score"]) else None
            params[f"analysis_id_{idx}"] = row.get("analysis_id") if pd.notna(row.get("analysis_id")) else None
            params[f"region_{idx}"] = row.get("region") if pd.notna(row.get("region")) else None
            params[f"sector_id_{idx}"] = int(row["sector_id"]) if pd.notna(row.get("sector_id")) else None
            params[f"brand_id_{idx}"] = int(row["brand_id"]) if pd.notna(row.get("brand_id")) else None
            params[f"volume_{idx}"] = float(row["volume"]) if pd.notna(row.get("volume")) else None
            params[f"positives_{idx}"] = float(row["positives"]) if pd.notna(row.get("positives")) else None
            params[f"negatives_{idx}"] = float(row["negatives"]) if pd.notna(row.get("negatives")) else None
            params[f"neutrals_{idx}"] = float(row["neutrals"]) if pd.notna(row.get("neutrals")) else None
            params[f"positives_neutrals_{idx}"] = float(row["positives_neutrals"]) if pd.notna(row.get("positives_neutrals")) else None
            params[f"negatives_neutrals_{idx}"] = float(row["negatives_neutrals"]) if pd.notna(row.get("negatives_neutrals")) else None

            values_list.append(f"""(
                :date_{idx}, :sector_label_{idx}, :brand_label_{idx}, :metric_{idx}, :score_{idx},
                :analysis_id_{idx}, :region_{idx}, :sector_id_{idx}, :brand_id_{idx},
                :volume_{idx}, :positives_{idx}, :negatives_{idx}, :neutrals_{idx},
                :positives_neutrals_{idx}, :negatives_neutrals_{idx}
            )""")

        sql = f"""
            INSERT INTO yougov (
                date, sector_label, brand_label, metric, score,
                analysis_id, region, sector_id, brand_id,
                volume, positives, negatives, neutrals,
                positives_neutrals, negatives_neutrals
            ) VALUES {', '.join(values_list)}
        """
        await session.execute(text(sql), params)

        total_inserted += len(batch)
        if total_inserted % 1000 == 0 or total_inserted == len(df):
            await session.commit()
            print(f"  Inserted {total_inserted}/{len(df)} rows...")

    await session.commit()
    print(f"[OK] Populated yougov with {total_inserted} rows")


async def populate_nielsen(session: AsyncSession):
    """Populate nielsen table from Excel file using bulk insert."""
    print(f"\nLoading Nielsen data from {NIELSEN_XLSX}...")

    # Read Excel - header is at row 8 (0-indexed), data starts from row 9
    df = pd.read_excel(NIELSEN_XLSX, header=8)

    # Map column names to lowercase
    column_mapping = {
        'Wirtschaftsgruppe': 'wirtschaftsgruppe',
        'Konzern': 'konzern',
        'Firma': 'firma',
        'Marke': 'marke',
        'Produktmarke': 'produktmarke',
        'Jahr': 'jahr',
        'Monat': 'monat',
        'Mediengruppe': 'mediengruppe',
        'TEuro': 'teuro',
    }
    df = df.rename(columns=column_mapping)

    print(f"  Loaded {len(df)} rows from Excel")
    print(f"  Columns: {list(df.columns)}")

    # Clear existing data
    await session.execute(text("TRUNCATE TABLE nielsen RESTART IDENTITY"))
    print("  Cleared existing nielsen data")

    # Filter out rows without required fields
    df = df.dropna(subset=['wirtschaftsgruppe', 'marke'])

    # Bulk insert in batches
    batch_size = 100
    total_inserted = 0

    for i in range(0, len(df), batch_size):
        batch = df.iloc[i:i+batch_size]
        values_list = []
        params = {}

        for j, (_, row) in enumerate(batch.iterrows()):
            idx = j
            params[f"wirtschaftsgruppe_{idx}"] = str(row["wirtschaftsgruppe"])
            params[f"konzern_{idx}"] = str(row["konzern"]) if pd.notna(row.get("konzern")) else None
            params[f"firma_{idx}"] = str(row["firma"]) if pd.notna(row.get("firma")) else None
            params[f"marke_{idx}"] = str(row["marke"])
            params[f"produktmarke_{idx}"] = str(row["produktmarke"]) if pd.notna(row.get("produktmarke")) else None
            params[f"jahr_{idx}"] = int(row["jahr"]) if pd.notna(row.get("jahr")) else 2024
            params[f"monat_{idx}"] = str(row["monat"]) if pd.notna(row.get("monat")) else "Januar"
            params[f"mediengruppe_{idx}"] = str(row["mediengruppe"]) if pd.notna(row.get("mediengruppe")) else None
            params[f"teuro_{idx}"] = float(row["teuro"]) if pd.notna(row.get("teuro")) else None

            values_list.append(f"""(
                :wirtschaftsgruppe_{idx}, :konzern_{idx}, :firma_{idx}, :marke_{idx}, :produktmarke_{idx},
                :jahr_{idx}, :monat_{idx}, :mediengruppe_{idx}, :teuro_{idx}
            )""")

        sql = f"""
            INSERT INTO nielsen (
                wirtschaftsgruppe, konzern, firma, marke, produktmarke,
                jahr, monat, mediengruppe, teuro
            ) VALUES {', '.join(values_list)}
        """
        await session.execute(text(sql), params)

        total_inserted += len(batch)
        if total_inserted % 1000 == 0 or total_inserted == len(df):
            await session.commit()
            print(f"  Inserted {total_inserted}/{len(df)} rows...")

    await session.commit()
    print(f"[OK] Populated nielsen with {total_inserted} rows")


async def verify_data(session: AsyncSession):
    """Verify the data was inserted correctly."""
    print("\nVerifying data...")

    # Count yougov
    result = await session.execute(text("SELECT COUNT(*) FROM yougov"))
    yougov_count = result.scalar()
    print(f"  yougov: {yougov_count} rows")

    # Sample yougov
    result = await session.execute(text("SELECT DISTINCT sector_label FROM yougov LIMIT 5"))
    sectors = [row[0] for row in result.fetchall()]
    print(f"  yougov sectors: {sectors}")

    # Count nielsen
    result = await session.execute(text("SELECT COUNT(*) FROM nielsen"))
    nielsen_count = result.scalar()
    print(f"  nielsen: {nielsen_count} rows")

    # Sample nielsen
    result = await session.execute(text("SELECT DISTINCT wirtschaftsgruppe FROM nielsen LIMIT 5"))
    industries = [row[0] for row in result.fetchall()]
    print(f"  nielsen industries: {industries}")

    print("\n[SUCCESS] Data tables created and populated!")


async def main():
    """Main function."""
    print("=" * 60)
    print("Creating data tables on STAGING database")
    print("=" * 60)
    print(f"Database: {DATABASE_URL.split('@')[1]}")
    print()

    engine = create_async_engine(DATABASE_URL)
    async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session_maker() as session:
        try:
            # Step 1: Create tables
            await create_tables(session)

            # Step 2: Populate yougov
            await populate_yougov(session)

            # Step 3: Populate nielsen
            await populate_nielsen(session)

            # Step 4: Verify
            await verify_data(session)

        except Exception as e:
            await session.rollback()
            print(f"\n[ERROR] {e}")
            raise

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
