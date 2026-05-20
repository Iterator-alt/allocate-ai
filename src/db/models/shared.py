"""Shared tables: Users, projects, project versions (owned by JS Backend).

These tables are primarily managed by the JS Backend but are referenced
by the AI Backend for context and relationship tracking.
"""

from datetime import datetime
from typing import Optional, List

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """User accounts (owned by JS Backend).

    Minimal model for user reference. Full user management is handled
    by the JS Backend; this model exists for foreign key relationships.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # External ID from JS Backend
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True, index=True)

    # Basic user info
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Role for authorization
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="user")  # user, admin, owner

    # Active status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    runs: Mapped[List["Run"]] = relationship("Run", back_populates="user")
    projects: Mapped[List["Project"]] = relationship("Project", back_populates="owner")


class Project(Base, TimestampMixin):
    """Projects containing budget allocation scenarios (owned by JS Backend).

    A project represents a client engagement or campaign for which
    budget allocations are being generated.
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # External ID from JS Backend
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True, index=True)

    # Project details
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Client/customer name
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Industry context
    industry: Mapped[str] = mapped_column(String(255), nullable=False)

    # Owner
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    # Active status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Project settings (JSON)
    settings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Relationships
    owner: Mapped["User"] = relationship("User", back_populates="projects")
    versions: Mapped[List["ProjectVersion"]] = relationship("ProjectVersion", back_populates="project")
    runs: Mapped[List["Run"]] = relationship("Run", back_populates="project")


class ProjectVersion(Base, TimestampMixin):
    """Project versions for tracking iterations (owned by JS Backend).

    Each version represents a snapshot of project parameters that
    can be used for comparison and rollback.
    """

    __tablename__ = "project_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Link to project
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)

    # Version number
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Version name/label
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Version parameters snapshot
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Notes about this version
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Is this the current active version?
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationship
    project: Mapped["Project"] = relationship("Project", back_populates="versions")


# Import for type hints
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.db.models.run import Run
