"""Guard #3: Change Detection Service.

Detects if run inputs are unchanged from a previous run
and returns cached results if available.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from src.db.models import Run, AllocationResult, RunStatus
from src.repositories import RunRepository, AllocationResultRepository


@dataclass
class ChangeDetectionResult:
    """Result of change detection check."""

    has_changes: bool
    cached_run_id: Optional[int] = None
    cached_result: Optional[Dict[str, Any]] = None
    cache_age_minutes: Optional[int] = None
    input_hash: str = ""
    reason: str = ""


class ChangeDetectionGuard:
    """Guard #3: Detects unchanged inputs and returns cached results.

    Computes a hash of the run inputs and checks if a previous
    successful run with the same hash exists.
    """

    def __init__(
        self,
        session: AsyncSession,
        cache_ttl_minutes: int = 60,
    ):
        """Initialize the guard.

        Args:
            session: Database session
            cache_ttl_minutes: Cache time-to-live in minutes (default 60)
        """
        self.session = session
        self.run_repo = RunRepository(session)
        self.result_repo = AllocationResultRepository(session)
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)

    async def check_for_cached_result(
        self,
        customer_name: str,
        industry: str,
        brand_kpi: str,
        confirmed_competitors: Optional[List[str]] = None,
        total_budget: Optional[float] = None,
        channels: Optional[List[str]] = None,
        session_token: Optional[str] = None,
    ) -> ChangeDetectionResult:
        """Check if a cached result exists for these inputs.

        Args:
            customer_name: Client name
            industry: Wirtschaftsgruppe
            brand_kpi: Target KPI
            confirmed_competitors: List of confirmed competitor names
            total_budget: Optional total budget
            channels: Optional channel list
            session_token: Optional session for user-specific cache

        Returns:
            ChangeDetectionResult indicating if cache hit occurred
        """
        # Compute input hash
        input_hash = self._compute_input_hash(
            customer_name=customer_name,
            industry=industry,
            brand_kpi=brand_kpi,
            confirmed_competitors=confirmed_competitors,
            total_budget=total_budget,
            channels=channels,
        )

        # Look for a previous completed run with same hash
        cached_run = await self._find_cached_run(
            input_hash=input_hash,
            session_token=session_token,
        )

        if not cached_run:
            return ChangeDetectionResult(
                has_changes=True,
                input_hash=input_hash,
                reason="No previous run found with matching inputs",
            )

        # Check if cache is still valid (within TTL)
        if cached_run.completed_at:
            age = datetime.utcnow() - cached_run.completed_at.replace(tzinfo=None)
            if age > self.cache_ttl:
                return ChangeDetectionResult(
                    has_changes=True,
                    cached_run_id=cached_run.id,
                    input_hash=input_hash,
                    cache_age_minutes=int(age.total_seconds() / 60),
                    reason=f"Cache expired (age: {int(age.total_seconds() / 60)} minutes)",
                )

        # Get the cached result
        cached_result = await self.result_repo.get_by_run_id(cached_run.id)

        if not cached_result:
            return ChangeDetectionResult(
                has_changes=True,
                cached_run_id=cached_run.id,
                input_hash=input_hash,
                reason="Previous run found but no result available",
            )

        # Cache hit!
        cache_age = None
        if cached_run.completed_at:
            age = datetime.utcnow() - cached_run.completed_at.replace(tzinfo=None)
            cache_age = int(age.total_seconds() / 60)

        return ChangeDetectionResult(
            has_changes=False,
            cached_run_id=cached_run.id,
            cached_result=cached_result.allocations,
            cache_age_minutes=cache_age,
            input_hash=input_hash,
            reason="Returning cached result (inputs unchanged)",
        )

    def _compute_input_hash(
        self,
        customer_name: str,
        industry: str,
        brand_kpi: str,
        confirmed_competitors: Optional[List[str]] = None,
        total_budget: Optional[float] = None,
        channels: Optional[List[str]] = None,
    ) -> str:
        """Compute a deterministic hash of input parameters.

        The hash is used to identify runs with identical inputs.
        """
        # Normalize inputs
        normalized = {
            "customer_name": customer_name.lower().strip(),
            "industry": industry.lower().strip(),
            "brand_kpi": brand_kpi.lower().strip(),
            "confirmed_competitors": sorted(
                [c.lower().strip() for c in (confirmed_competitors or [])]
            ),
            "total_budget": str(total_budget) if total_budget else None,
            "channels": sorted(
                [c.lower().strip() for c in (channels or [])]
            ) if channels else None,
        }

        # Create deterministic JSON string
        json_str = json.dumps(normalized, sort_keys=True, separators=(",", ":"))

        # Compute SHA-256 hash
        hash_obj = hashlib.sha256(json_str.encode("utf-8"))
        return hash_obj.hexdigest()

    async def _find_cached_run(
        self,
        input_hash: str,
        session_token: Optional[str] = None,
    ) -> Optional[Run]:
        """Find a previous successful run with the same input hash."""
        query = (
            select(Run)
            .where(
                and_(
                    Run.input_hash == input_hash,
                    Run.status == RunStatus.COMPLETED.value,
                )
            )
            .order_by(Run.completed_at.desc())
            .limit(1)
        )

        # Optionally filter by session token for user-specific cache
        if session_token:
            query = query.where(Run.session_token == session_token)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def invalidate_cache(
        self,
        input_hash: Optional[str] = None,
        run_id: Optional[int] = None,
    ) -> int:
        """Invalidate cached results.

        Can invalidate by input hash or run ID.
        Returns the number of runs invalidated.

        Note: This doesn't delete runs, just marks them as not cacheable
        by clearing the input_hash.
        """
        count = 0

        if input_hash:
            query = select(Run).where(Run.input_hash == input_hash)
            result = await self.session.execute(query)
            for run in result.scalars().all():
                run.input_hash = None
                count += 1

        if run_id:
            run = await self.run_repo.get(run_id)
            if run:
                run.input_hash = None
                count += 1

        if count > 0:
            await self.session.flush()

        return count

    async def update_run_hash(self, run_id: int, input_hash: str) -> None:
        """Update the input hash for a run.

        Called after competitor confirmation to store the final input hash.
        """
        run = await self.run_repo.get(run_id)
        if run:
            run.input_hash = input_hash
            await self.session.flush()

    def get_cache_key_preview(
        self,
        customer_name: str,
        industry: str,
        brand_kpi: str,
        confirmed_competitors: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Get a preview of what the cache key would be.

        Useful for debugging and understanding cache behavior.
        """
        input_hash = self._compute_input_hash(
            customer_name=customer_name,
            industry=industry,
            brand_kpi=brand_kpi,
            confirmed_competitors=confirmed_competitors,
        )

        return {
            "input_hash": input_hash,
            "normalized_inputs": {
                "customer_name": customer_name.lower().strip(),
                "industry": industry.lower().strip(),
                "brand_kpi": brand_kpi.lower().strip(),
                "confirmed_competitors": sorted(
                    [c.lower().strip() for c in (confirmed_competitors or [])]
                ),
            },
        }
