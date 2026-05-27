"""Seed staging database with Ehrmann Almighurt test data.

Creates:
1. A User record
2. A Project record
3. A ProjectVersion with Ehrmann campaign inputs
4. A ProjectVersionAiRun with externalRunId=1 ready for POST /runs

Run with: python scripts/seed_staging_ehrmann.py
"""

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Staging database connection
DATABASE_URL = "postgresql+asyncpg://mp_root:xs8rLdsOVM95hb27@20.79.8.67:5432/allocate_db"


async def seed_staging_data():
    """Create test data in Prisma tables on staging."""
    engine = create_async_engine(DATABASE_URL)
    async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session_maker() as session:
        try:
            # Generate UUIDs
            user_id = str(uuid.uuid4())
            project_id = str(uuid.uuid4())
            version_id = str(uuid.uuid4())
            ai_run_id = str(uuid.uuid4())

            # Prisma uses timestamp(3) without timezone
            now = datetime.utcnow()

            print("Creating test data for Ehrmann Almighurt on STAGING...")
            print(f"  User ID: {user_id}")
            print(f"  Project ID: {project_id}")
            print(f"  ProjectVersion ID: {version_id}")
            print(f"  ProjectVersionAiRun ID: {ai_run_id}")

            # 1. Create User
            await session.execute(text("""
                INSERT INTO "User" (id, email, password, name, role, "createdAt", "updatedAt")
                VALUES (:id, :email, :password, :name, :role, :created_at, :updated_at)
                ON CONFLICT (email) DO UPDATE SET name = :name
                RETURNING id
            """), {
                "id": user_id,
                "email": "test@allocate.ai",
                "password": "hashed_password_placeholder",
                "name": "Test User",
                "role": "member",
                "created_at": now,
                "updated_at": now,
            })
            print("  [OK] User created/updated")

            # Get the user id (might be existing one)
            result = await session.execute(text("""
                SELECT id FROM "User" WHERE email = 'test@allocate.ai'
            """))
            user_row = result.fetchone()
            user_id = user_row[0] if user_row else user_id

            # 2. Create Project
            await session.execute(text("""
                INSERT INTO "Project" (id, name, status, "createdById", "createdAt", "updatedAt")
                VALUES (:id, :name, :status, :created_by, :created_at, :updated_at)
                ON CONFLICT (id) DO UPDATE SET name = :name
            """), {
                "id": project_id,
                "name": "Ehrmann Q2 2026 Campaign",
                "status": "active",
                "created_by": user_id,
                "created_at": now,
                "updated_at": now,
            })
            print("  [OK] Project created")

            # 3. Create ProjectVersion with Ehrmann campaign inputs
            # Using real data from debug_output/run_test_ehrmann_008
            await session.execute(text("""
                INSERT INTO "ProjectVersion" (
                    id, "projectId", "versionNumber", "versionName",
                    customer, industry, "brandKpi", "mediaChannels",
                    "goalMode", "goalText", status,
                    "createdById", "createdAt", "updatedAt"
                )
                VALUES (
                    :id, :project_id, :version_number, :version_name,
                    :customer, :industry, :brand_kpi, :media_channels,
                    :goal_mode, :goal_text, :status,
                    :created_by, :created_at, :updated_at
                )
                ON CONFLICT (id) DO UPDATE SET
                    customer = :customer,
                    industry = :industry,
                    "goalText" = :goal_text
            """), {
                "id": version_id,
                "project_id": project_id,
                "version_number": 1,
                "version_name": "v1 - Initial",
                "customer": "Ehrmann Almighurt",
                "industry": "Lebensmittel",
                "brand_kpi": "adaware",
                "media_channels": ["TV", "Digital", "OOH", "Radio", "Print"],
                "goal_mode": "budget",
                "goal_text": "Increase ad awareness by 5 percentage points with 1M EUR budget",
                "status": "active",
                "created_by": user_id,
                "created_at": now,
                "updated_at": now,
            })
            print("  [OK] ProjectVersion created")

            # Update Project's currentVersionId
            await session.execute(text("""
                UPDATE "Project" SET "currentVersionId" = :version_id WHERE id = :project_id
            """), {
                "version_id": version_id,
                "project_id": project_id,
            })
            print("  [OK] Project.currentVersionId updated")

            # 4. Create ProjectVersionAiRun with externalRunId = 1
            external_run_id = 1

            # First check if externalRunId=1 already exists and delete it
            await session.execute(text("""
                DELETE FROM "ProjectVersionAiRun" WHERE "externalRunId" = :external_run_id
            """), {"external_run_id": external_run_id})

            await session.execute(text("""
                INSERT INTO "ProjectVersionAiRun" (
                    id, "projectVersionId", "externalRunId", status,
                    "confirmedCompetitors",
                    "createdAt", "updatedAt"
                )
                VALUES (
                    :id, :project_version_id, :external_run_id, :status,
                    :confirmed_competitors,
                    :created_at, :updated_at
                )
            """), {
                "id": ai_run_id,
                "project_version_id": version_id,
                "external_run_id": external_run_id,
                "status": "pending",
                "confirmed_competitors": [],
                "created_at": now,
                "updated_at": now,
            })
            print(f"  [OK] ProjectVersionAiRun created with externalRunId = {external_run_id}")

            await session.commit()
            print("\n[SUCCESS] All test data created successfully on STAGING!")
            print("\nTo test the API:")
            print('   curl -X POST http://127.0.0.1:8082/api/v1/runs \\')
            print('     -H "Content-Type: application/json" \\')
            print('     -H "X-Session-Token: test-session" \\')
            print(f'     -d \'{{"run_id": {external_run_id}, "action": "start"}}\'')

        except Exception as e:
            await session.rollback()
            print(f"[ERROR] Error: {e}")
            raise

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_staging_data())
