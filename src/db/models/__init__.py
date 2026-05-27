"""Database models package."""

from src.db.models.data import NielsenSpend, YouGovKPI, YouGov, Nielsen
from src.db.models.mapping import BrandMap, IndustryMap
from src.db.models.prompt import ExpertKnowledge, PromptGuardrails, PromptTrace
from src.db.models.run import AllocationResult, ChatHistory, Run, RunStatus
from src.db.models.shared import Project, ProjectVersion, User
from src.db.models.logging import UsageLog
from src.db.models.prisma_tables import PrismaProjectVersion, PrismaProjectVersionAiRun

__all__ = [
    # Data tables (new Stage 1 schema)
    "YouGov",
    "Nielsen",
    # Data tables (legacy - kept for backward compatibility)
    "NielsenSpend",
    "YouGovKPI",
    # Mapping tables
    "IndustryMap",
    "BrandMap",
    # Prompt tables
    "ExpertKnowledge",
    "PromptGuardrails",
    "PromptTrace",
    # Run tables
    "Run",
    "RunStatus",
    "AllocationResult",
    "ChatHistory",
    # Shared tables
    "User",
    "Project",
    "ProjectVersion",
    # Prisma tables (managed by JS Backend, queried/updated by Python)
    "PrismaProjectVersion",
    "PrismaProjectVersionAiRun",
    # Logging tables
    "UsageLog",
]
