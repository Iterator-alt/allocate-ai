"""Repositories for prompt management: Expert Knowledge and Guardrails."""

from typing import List, Optional, Dict, Any

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ExpertKnowledge, PromptGuardrails, PromptTrace
from src.repositories.base import BaseRepository


class ExpertKnowledgeRepository(BaseRepository[ExpertKnowledge]):
    """Repository for expert knowledge (media planning heuristics).

    Expert knowledge is versioned - only active versions are used in prompts.
    Multiple categories can exist (channel_heuristics, budget_rules, etc.).
    """

    def __init__(self, session: AsyncSession):
        super().__init__(session, ExpertKnowledge)

    async def get_active_by_category(
        self,
        category: str,
    ) -> Optional[ExpertKnowledge]:
        """Get the active version of expert knowledge for a category.

        Args:
            category: Knowledge category (e.g., "channel_heuristics")

        Returns:
            Active ExpertKnowledge record or None
        """
        query = (
            select(ExpertKnowledge)
            .where(
                and_(
                    ExpertKnowledge.category == category,
                    ExpertKnowledge.is_active == True,
                )
            )
            .order_by(ExpertKnowledge.version.desc())
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_all_active(self) -> List[ExpertKnowledge]:
        """Get all active expert knowledge records."""
        query = (
            select(ExpertKnowledge)
            .where(ExpertKnowledge.is_active == True)
            .order_by(ExpertKnowledge.category, ExpertKnowledge.version.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_categories(self) -> List[str]:
        """Get all distinct knowledge categories."""
        query = (
            select(ExpertKnowledge.category)
            .distinct()
            .order_by(ExpertKnowledge.category)
        )
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]

    async def get_version_history(
        self,
        category: str,
        limit: int = 10,
    ) -> List[ExpertKnowledge]:
        """Get version history for a category."""
        query = (
            select(ExpertKnowledge)
            .where(ExpertKnowledge.category == category)
            .order_by(ExpertKnowledge.version.desc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_version(
        self,
        category: str,
        version: int,
    ) -> Optional[ExpertKnowledge]:
        """Get a specific version of expert knowledge."""
        query = select(ExpertKnowledge).where(
            and_(
                ExpertKnowledge.category == category,
                ExpertKnowledge.version == version,
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_version(
        self,
        category: str,
        content: str,
        structured_content: Optional[Dict[str, Any]] = None,
        change_notes: Optional[str] = None,
        set_active: bool = True,
    ) -> ExpertKnowledge:
        """Create a new version of expert knowledge.

        Automatically increments version number and optionally deactivates
        previous versions.
        """
        # Get current max version
        query = (
            select(ExpertKnowledge.version)
            .where(ExpertKnowledge.category == category)
            .order_by(ExpertKnowledge.version.desc())
            .limit(1)
        )
        result = await self.session.execute(query)
        current_max = result.scalar_one_or_none() or 0

        # Deactivate previous active version if setting new active
        if set_active:
            await self._deactivate_category(category)

        # Create new version
        new_knowledge = ExpertKnowledge(
            category=category,
            version=current_max + 1,
            content=content,
            structured_content=structured_content,
            change_notes=change_notes,
            is_active=set_active,
        )
        self.session.add(new_knowledge)
        await self.session.flush()
        await self.session.refresh(new_knowledge)

        return new_knowledge

    async def _deactivate_category(self, category: str) -> None:
        """Deactivate all versions in a category."""
        query = (
            select(ExpertKnowledge)
            .where(
                and_(
                    ExpertKnowledge.category == category,
                    ExpertKnowledge.is_active == True,
                )
            )
        )
        result = await self.session.execute(query)
        for record in result.scalars().all():
            record.is_active = False


class PromptGuardrailsRepository(BaseRepository[PromptGuardrails]):
    """Repository for prompt guardrails (output constraints).

    Guardrails define constraints the LLM must follow when generating
    allocation recommendations.
    """

    def __init__(self, session: AsyncSession):
        super().__init__(session, PromptGuardrails)

    async def get_active_by_type(
        self,
        guardrail_type: str,
    ) -> Optional[PromptGuardrails]:
        """Get the active version of a guardrail type.

        Args:
            guardrail_type: Type (e.g., "output_format", "value_constraints")

        Returns:
            Active PromptGuardrails record or None
        """
        query = (
            select(PromptGuardrails)
            .where(
                and_(
                    PromptGuardrails.guardrail_type == guardrail_type,
                    PromptGuardrails.is_active == True,
                )
            )
            .order_by(PromptGuardrails.version.desc())
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_all_active(self) -> List[PromptGuardrails]:
        """Get all active guardrails."""
        query = (
            select(PromptGuardrails)
            .where(PromptGuardrails.is_active == True)
            .order_by(PromptGuardrails.guardrail_type, PromptGuardrails.version.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_guardrail_types(self) -> List[str]:
        """Get all distinct guardrail types."""
        query = (
            select(PromptGuardrails.guardrail_type)
            .distinct()
            .order_by(PromptGuardrails.guardrail_type)
        )
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]

    async def get_version_history(
        self,
        guardrail_type: str,
        limit: int = 10,
    ) -> List[PromptGuardrails]:
        """Get version history for a guardrail type."""
        query = (
            select(PromptGuardrails)
            .where(PromptGuardrails.guardrail_type == guardrail_type)
            .order_by(PromptGuardrails.version.desc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def create_version(
        self,
        guardrail_type: str,
        content: str,
        structured_rules: Optional[Dict[str, Any]] = None,
        change_notes: Optional[str] = None,
        set_active: bool = True,
    ) -> PromptGuardrails:
        """Create a new version of a guardrail.

        Automatically increments version number and optionally deactivates
        previous versions.
        """
        # Get current max version
        query = (
            select(PromptGuardrails.version)
            .where(PromptGuardrails.guardrail_type == guardrail_type)
            .order_by(PromptGuardrails.version.desc())
            .limit(1)
        )
        result = await self.session.execute(query)
        current_max = result.scalar_one_or_none() or 0

        # Deactivate previous active version if setting new active
        if set_active:
            await self._deactivate_type(guardrail_type)

        # Create new version
        new_guardrail = PromptGuardrails(
            guardrail_type=guardrail_type,
            version=current_max + 1,
            content=content,
            structured_rules=structured_rules,
            change_notes=change_notes,
            is_active=set_active,
        )
        self.session.add(new_guardrail)
        await self.session.flush()
        await self.session.refresh(new_guardrail)

        return new_guardrail

    async def _deactivate_type(self, guardrail_type: str) -> None:
        """Deactivate all versions of a guardrail type."""
        query = (
            select(PromptGuardrails)
            .where(
                and_(
                    PromptGuardrails.guardrail_type == guardrail_type,
                    PromptGuardrails.is_active == True,
                )
            )
        )
        result = await self.session.execute(query)
        for record in result.scalars().all():
            record.is_active = False


class PromptTraceRepository(BaseRepository[PromptTrace]):
    """Repository for prompt traces (LLM call observability)."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, PromptTrace)

    async def get_by_run(self, run_id: int) -> List[PromptTrace]:
        """Get all traces for a run."""
        query = (
            select(PromptTrace)
            .where(PromptTrace.run_id == run_id)
            .order_by(PromptTrace.called_at)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_latest_by_run(self, run_id: int) -> Optional[PromptTrace]:
        """Get the most recent trace for a run."""
        query = (
            select(PromptTrace)
            .where(PromptTrace.run_id == run_id)
            .order_by(PromptTrace.called_at.desc())
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_failed_traces(
        self,
        limit: int = 100,
    ) -> List[PromptTrace]:
        """Get recent failed traces for debugging."""
        query = (
            select(PromptTrace)
            .where(PromptTrace.status != "success")
            .order_by(PromptTrace.called_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_usage_stats(
        self,
        run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Get aggregated usage statistics."""
        from sqlalchemy import func

        query = select(
            func.count(PromptTrace.id).label("total_calls"),
            func.sum(PromptTrace.prompt_tokens).label("total_prompt_tokens"),
            func.sum(PromptTrace.completion_tokens).label("total_completion_tokens"),
            func.sum(PromptTrace.total_tokens).label("total_tokens"),
            func.avg(PromptTrace.latency_ms).label("avg_latency_ms"),
        )

        if run_id:
            query = query.where(PromptTrace.run_id == run_id)

        result = await self.session.execute(query)
        row = result.one()

        return {
            "total_calls": row.total_calls or 0,
            "total_prompt_tokens": row.total_prompt_tokens or 0,
            "total_completion_tokens": row.total_completion_tokens or 0,
            "total_tokens": row.total_tokens or 0,
            "avg_latency_ms": float(row.avg_latency_ms) if row.avg_latency_ms else 0,
        }
