"""SQLAlchemy models for Prisma-managed tables.

These tables are owned/managed by the JS Backend (Prisma) but are queried
and updated by the Python AI Backend for the integration flow.

IMPORTANT: These models map to existing Prisma tables with UUID primary keys.
Do NOT run alembic migrations on these - they are managed by Prisma.
"""

from datetime import datetime
from typing import Optional, List

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, JSON, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB

from src.db.base import Base


class PrismaProjectVersion(Base):
    """ProjectVersion table (Prisma-managed).

    Contains campaign inputs configured by the frontend.
    This table is READ-ONLY from Python's perspective.

    Maps to Prisma model: ProjectVersion
    """

    __tablename__ = "ProjectVersion"  # Prisma uses PascalCase table names

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    projectId: Mapped[str] = mapped_column(Text, nullable=False)
    versionNumber: Mapped[int] = mapped_column(Integer, nullable=False)
    versionName: Mapped[str] = mapped_column(Text, nullable=False)

    # Campaign inputs - these are what we need to extract
    customer: Mapped[str] = mapped_column(Text, nullable=False)
    industry: Mapped[str] = mapped_column(Text, nullable=False)
    brandKpi: Mapped[str] = mapped_column(Text, nullable=False)  # adaware, aware, consider
    mediaChannels: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text), nullable=True)
    goalMode: Mapped[str] = mapped_column(Text, nullable=False)  # budget, goal
    goalText: Mapped[str] = mapped_column(Text, nullable=False)

    # Status
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")

    # Timestamps (Prisma uses timestamp(3) without time zone)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    deletedAt: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)

    # User references (optional)
    createdById: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updatedById: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationship to AI runs
    aiRuns: Mapped[List["PrismaProjectVersionAiRun"]] = relationship(
        "PrismaProjectVersionAiRun",
        back_populates="projectVersion",
        foreign_keys="PrismaProjectVersionAiRun.projectVersionId"
    )


class PrismaProjectVersionAiRun(Base):
    """ProjectVersionAiRun table (Prisma-managed).

    The bridge table between JS Backend (ProjectVersion) and Python Backend (Run).
    We READ from this to find the ProjectVersion and WRITE results back.

    Maps to Prisma model: ProjectVersionAiRun
    """

    __tablename__ = "ProjectVersionAiRun"  # Prisma uses PascalCase table names

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    projectVersionId: Mapped[str] = mapped_column(
        Text,
        ForeignKey("ProjectVersion.id"),
        nullable=False
    )

    # Link to Python's runs table - this is set by JS Backend
    # We use this as the lookup key when POST /runs is called
    externalRunId: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, unique=True)

    # Status tracking
    status: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    progressPct: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    progressMessage: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    queuePosition: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    etaSeconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Timestamps (Prisma uses timestamp(3) without time zone)
    startedAt: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    completedAt: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    errorMessage: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # SNAPSHOTS - These are what we write from Python
    confirmedCompetitors: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text), nullable=True)
    competitorSnapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    allocationResult: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    chatSnapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    traceSnapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    statusPayload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    rawPayload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # User references
    createdById: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updatedById: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Soft delete
    deletedAt: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)

    # Timestamps
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)

    # Relationship back to ProjectVersion
    projectVersion: Mapped["PrismaProjectVersion"] = relationship(
        "PrismaProjectVersion",
        back_populates="aiRuns",
        foreign_keys=[projectVersionId]
    )
