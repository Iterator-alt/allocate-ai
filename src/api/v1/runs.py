"""Run management API endpoints - Prisma-only version.

Endpoints:
- POST /runs - Start a new generation run (accepts {run_id, action: "start"})
- GET /runs/{id}/status - Poll run state from ProjectVersionAiRun
- GET /runs/{id}/result - Get allocation result from ProjectVersionAiRun
- GET /runs/{id}/artifacts - Run metrics and artifact download availability
- GET /runs/{id}/debug-zip - Download debug ZIP file (requires STAGE1_DEBUG_MODE=True)

Flow:
1. POST /runs with {run_id, action: "start"} where run_id = externalRunId from ProjectVersionAiRun
2. Python reads inputs from ProjectVersion, runs Stage 1-4 pipeline
3. Results are stored in ProjectVersionAiRun.allocationResult
4. Poll status until "completed", then GET /result

NO PYTHON TABLES REQUIRED - all state is stored in Prisma tables.
"""

import logging
import os
import re
import json
import zipfile
import shutil
from datetime import datetime
from typing import Optional, List
from decimal import Decimal
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.dependencies import get_db
from src.api.schemas import (
    RunStatusResponse,
    RunStatus,
    ErrorResponse,
    StartRunRequest,
    StartRunResponse,
    RunArtifactsResponse,
    RunArtifactStatus,
    RunArtifactFileStatus,
)
from src.api.middleware import (
    get_session_context,
    SessionContext,
    limiter,
)
from src.db.models.prisma_tables import PrismaProjectVersion, PrismaProjectVersionAiRun

# Stage 1 imports
from src.services.stage1 import (
    Stage1Orchestrator,
    UserCampaignInput,
    Stage1Result,
    Stage1Status,
)

# Stage 2-4 AI Pipeline imports
from src.services.llm_gateway.client import OpenAIClient
from src.services.mediamix.prompt_assembly import PromptAssemblyService, PromptAssemblyInput
from src.services.chat.preference_extraction import (
    extract_chat_preferences,
    build_preference_prompt_text,
    apply_channel_adjustments,
)
from src.services.warnings import build_warnings_from_context
from src.services.errors import humanize_error, get_error_title
from src.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


# =============================================================================
# Data Classes for In-Memory State
# =============================================================================

@dataclass
class CampaignInputs:
    """Campaign inputs extracted from ProjectVersion."""
    customer_name: str
    industry: str
    brand_kpi: str
    media_channels: List[str]
    goal_mode: str
    goal_text: str
    total_budget: Optional[float]
    direction: str


# =============================================================================
# Helper Functions
# =============================================================================

def extract_budget_from_goal_text(goal_text: str) -> Optional[float]:
    """Extract budget amount from goal_text using regex.

    Supports formats like:
    - "€2M budget" -> 2000000
    - "2M EUR" -> 2000000
    - "€500K" -> 500000
    - "500000 euros" -> 500000
    - "budget of 1,000,000" -> 1000000
    """
    if not goal_text:
        return None

    # Normalize text
    text = goal_text.lower().replace(",", "").replace(" ", "")

    # Pattern 1: €2M, €500K, 2M€, etc.
    pattern_m = r'[€$]?(\d+(?:\.\d+)?)\s*m(?:illion)?|(\d+(?:\.\d+)?)\s*m(?:illion)?\s*[€$]?'
    match = re.search(pattern_m, text, re.IGNORECASE)
    if match:
        value = float(match.group(1) or match.group(2))
        return value * 1_000_000

    # Pattern 2: €500K, 500K€, etc.
    pattern_k = r'[€$]?(\d+(?:\.\d+)?)\s*k|(\d+(?:\.\d+)?)\s*k\s*[€$]?'
    match = re.search(pattern_k, text, re.IGNORECASE)
    if match:
        value = float(match.group(1) or match.group(2))
        return value * 1_000

    # Pattern 3: Plain numbers with currency indicators
    pattern_plain = r'[€$]?\s*(\d{5,})\s*(?:eur(?:o)?s?)?|budget\s*(?:of)?\s*[€$]?\s*(\d{5,})'
    match = re.search(pattern_plain, text, re.IGNORECASE)
    if match:
        value = match.group(1) or match.group(2)
        return float(value)

    return None


def map_goal_mode_to_direction(goal_mode: str) -> str:
    """Map Prisma goalMode to our direction format.

    Prisma goalMode values:
    - "goal": User has a KPI goal, calculate required budget (Goal→Budget)
    - "budget": User has a fixed budget, optimize KPI (Budget→Impact)
    """
    if goal_mode == "goal":
        return "goal_to_budget"
    else:
        return "budget_to_impact"


# =============================================================================
# Channel Name Mapping (Nielsen Mediengruppe → User-Facing Names)
# =============================================================================

# Map from Nielsen internal channel names to user-facing UI names
NIELSEN_TO_UI_CHANNEL_MAP = {
    "FERNSEHEN": "TV",
    "ONLINE": "Digital",
    "PLAKAT": "OOH",
    "RADIO": "Radio",
    "SOCIAL": "Social",
    "ZEITUNGEN": "Newspapers",
    "PUBLIKUMSZEITSCHRIFTEN": "Magazines",
    "FACHZEITSCHRIFTEN": "Trade Press",
    "AT-RETAIL-MEDIA": "Retail Media",
    "SEARCH": "Search",
    "KINO": "Cinema",
    "TRANSPORT MEDIA": "Transport",
    "AMBIENT MEDIA": "Ambient",
    "WERBESENDUNGEN": "Direct Mail",
}

# Reverse mapping: UI names → Nielsen names (for filtering)
UI_TO_NIELSEN_CHANNEL_MAP = {
    "TV": "FERNSEHEN",
    "Digital": "ONLINE",
    "Online": "ONLINE",  # Alternative UI name for Digital
    "OOH": "PLAKAT",
    "Out-of-Home": "PLAKAT",  # Alternative UI name for OOH
    "Radio": "RADIO",
    "Social": "SOCIAL",
    "Print": ["ZEITUNGEN", "PUBLIKUMSZEITSCHRIFTEN"],  # Print maps to multiple
    "Newspapers": "ZEITUNGEN",
    "Magazines": "PUBLIKUMSZEITSCHRIFTEN",
    "Trade Press": "FACHZEITSCHRIFTEN",
    "Retail Media": "AT-RETAIL-MEDIA",
    "Search": "SEARCH",
    "Cinema": "KINO",
    "Transport": "TRANSPORT MEDIA",
    "Ambient": "AMBIENT MEDIA",
    "Direct Mail": "WERBESENDUNGEN",
}

# Comprehensive channel name normalization map
# Maps ALL known variants (German, English, abbreviations, LLM outputs) to canonical UI names
# Keys should be UPPERCASE for case-insensitive lookup
CHANNEL_NORMALIZATION_MAP = {
    # TV variants
    "TV": "TV",
    "TELEVISION": "TV",
    "FERNSEHEN": "TV",

    # Online/Digital variants - UI uses "Online"
    "DIGITAL": "Online",
    "ONLINE": "Online",
    "INTERNET": "Online",

    # OOH variants - UI uses "Out-of-Home"
    "OOH": "Out-of-Home",
    "OUT OF HOME": "Out-of-Home",
    "OUT-OF-HOME": "Out-of-Home",
    "OUTDOOR": "Out-of-Home",
    "PLAKAT": "Out-of-Home",
    "PLAAT": "Out-of-Home",  # Common LLM typo for PLAKAT
    "BILLBOARD": "Out-of-Home",
    "BILLBOARDS": "Out-of-Home",

    # Radio variants
    "RADIO": "Radio",

    # Social variants
    "SOCIAL": "Social",
    "SOCIAL MEDIA": "Social",
    "SOCIALMEDIA": "Social",

    # Newspapers variants
    "NEWSPAPERS": "Newspapers",
    "NEWSPAPER": "Newspapers",
    "ZEITUNGEN": "Newspapers",
    "ZEITUNG": "Newspapers",
    "NEWS": "Newspapers",
    "PRINT NEWS": "Newspapers",

    # Magazines variants
    "MAGAZINES": "Magazines",
    "MAGAZINE": "Magazines",
    "PUBLIKUMSZEITSCHRIFTEN": "Magazines",
    "ZEITSCHRIFTEN": "Magazines",
    "ZEITSCHRIFT": "Magazines",
    "CONSUMER MAGAZINES": "Magazines",

    # Print (generic - maps to both newspapers and magazines context)
    "PRINT": "Print",

    # Trade Press variants
    "TRADE PRESS": "Trade Press",
    "TRADE": "Trade Press",
    "FACHZEITSCHRIFTEN": "Trade Press",
    "TRADE PUBLICATIONS": "Trade Press",
    "B2B": "Trade Press",

    # Retail Media variants
    "RETAIL MEDIA": "Retail Media",
    "RETAIL": "Retail Media",
    "AT-RETAIL-MEDIA": "Retail Media",
    "POS": "Retail Media",
    "POINT OF SALE": "Retail Media",

    # Search variants
    "SEARCH": "Search",
    "SEM": "Search",
    "PAID SEARCH": "Search",

    # Cinema variants
    "CINEMA": "Cinema",
    "KINO": "Cinema",
    "MOVIE": "Cinema",
    "MOVIES": "Cinema",

    # Transport variants
    "TRANSPORT": "Transport",
    "TRANSPORT MEDIA": "Transport",
    "TRANSIT": "Transport",
    "TRANSIT ADVERTISING": "Transport",

    # Ambient variants
    "AMBIENT": "Ambient",
    "AMBIENT MEDIA": "Ambient",
    "EXPERIENTIAL": "Ambient",

    # Direct Mail variants
    "DIRECT MAIL": "Direct Mail",
    "DIRECTMAIL": "Direct Mail",
    "WERBESENDUNGEN": "Direct Mail",
    "DM": "Direct Mail",
    "MAIL": "Direct Mail",
}


def map_nielsen_channel_to_ui(nielsen_channel: str) -> str:
    """Map Nielsen Mediengruppe name to user-facing UI channel name."""
    return NIELSEN_TO_UI_CHANNEL_MAP.get(nielsen_channel.upper(), nielsen_channel)


def get_allowed_nielsen_channels(ui_channels: List[str]) -> set:
    """Get set of Nielsen channel names that correspond to user-selected UI channels."""
    allowed = set()
    for ui_channel in ui_channels:
        nielsen_name = UI_TO_NIELSEN_CHANNEL_MAP.get(ui_channel)
        if nielsen_name:
            # Handle case where UI channel maps to multiple Nielsen channels (e.g., Print)
            if isinstance(nielsen_name, list):
                allowed.update(nielsen_name)
            else:
                allowed.add(nielsen_name)
        else:
            # If no mapping found, allow exact match (case-insensitive)
            allowed.add(ui_channel.upper())
    return allowed


def _normalize_channel_name(channel: str) -> str:
    """Normalize a channel name for comparison.

    Handles case-insensitive matching and maps all known variants
    (German Nielsen names, English names, abbreviations, LLM outputs)
    to canonical UI names.
    """
    channel_upper = channel.strip().upper()

    # Use comprehensive normalization map (handles all variants)
    normalized = CHANNEL_NORMALIZATION_MAP.get(channel_upper)
    if normalized:
        return normalized

    # Fallback: try Nielsen to UI mapping
    ui_name = NIELSEN_TO_UI_CHANNEL_MAP.get(channel_upper)
    if ui_name:
        return ui_name

    # Return original with title case as last resort
    return channel.strip().title()


def _normalized_user_channel_set(user_channels: List[str]) -> set:
    """Normalize user-selected channel names to canonical UI names for comparison."""
    return {_normalize_channel_name(ch) for ch in (user_channels or [])}


def _check_allocation_validity(
    parsed_allocation: dict,
    user_channels: set,
    external_run_id: int,
) -> tuple:
    """
    Check if LLM allocation output is valid.

    Validates:
    1. All user-selected channels are present
    2. No extra channels that user didn't select
    3. Percentages sum to 100% (within 1% tolerance)
    4. kpi_projection is present and not null
    5. confidence_score is present and not null

    Returns:
        Tuple of (is_valid: bool, errors: List[str])
    """
    errors = []
    allocations = parsed_allocation.get("allocations", parsed_allocation.get("channels", []))

    # Normalize user channels to canonical UI names using the same normalization
    normalized_user_channels = set()
    for ch in user_channels:
        normalized_user_channels.add(_normalize_channel_name(ch))

    # Get output channels (normalized to UI names)
    output_channels = set()
    for alloc in allocations:
        channel = alloc.get("channel", alloc.get("name", ""))
        normalized = _normalize_channel_name(channel)
        output_channels.add(normalized)

    # Check for missing channels
    missing = normalized_user_channels - output_channels
    if missing:
        errors.append(f"Missing channels: {', '.join(sorted(missing))}")

    # Check for extra channels (only if user specified channels)
    if user_channels:
        extra = output_channels - normalized_user_channels
        if extra:
            errors.append(f"Extra channels not requested: {', '.join(sorted(extra))}")

    # Check sum (within 1% tolerance)
    total = sum(
        float(alloc.get("share_pct", alloc.get("percentage", 0)) or 0)
        for alloc in allocations
    )
    if abs(total - 100.0) > 1.0:
        errors.append(f"Allocation sum is {total:.1f}% (expected 100%)")

    # Check kpi_projection (must be present and not null)
    kpi_proj = parsed_allocation.get("kpi_projection", parsed_allocation.get("kpiProjection"))
    if kpi_proj is None:
        errors.append("kpi_projection is missing or null")

    # Check confidence (must be present and not null)
    confidence = parsed_allocation.get("confidence", parsed_allocation.get("confidence_score"))
    if confidence is None:
        errors.append("confidence_score is missing or null")

    is_valid = len(errors) == 0
    if not is_valid:
        logger.warning(f"[ExternalRunId {external_run_id}] Allocation validation failed: {errors}")

    return is_valid, errors


async def _retry_llm_allocation(
    llm_client,
    assembled_prompt,
    external_run_id: int,
) -> tuple:
    """
    Retry LLM call for allocation.

    Returns:
        Tuple of (parsed_allocation: dict or None, success: bool)
    """
    logger.info(f"[ExternalRunId {external_run_id}] Retrying LLM allocation call...")

    try:
        llm_response = await llm_client.generate(
            system_prompt=assembled_prompt.system_prompt,
            user_prompt=assembled_prompt.user_prompt,
            temperature=0.3,  # Lower temperature for retry
            max_tokens=4096,
            json_mode=True,
        )

        logger.info(f"[ExternalRunId {external_run_id}] Retry LLM response: {llm_response.total_tokens} tokens")

        parsed_allocation = json.loads(llm_response.content)
        return parsed_allocation, True

    except json.JSONDecodeError as e:
        logger.error(f"[ExternalRunId {external_run_id}] Retry failed - JSON parse error: {e}")
        return None, False
    except Exception as e:
        logger.error(f"[ExternalRunId {external_run_id}] Retry failed - error: {e}")
        return None, False


async def _resolve_customer_historical_spend(
    session: AsyncSession,
    brand_info: Optional[dict],
    external_run_id: int,
) -> tuple[Optional[float], Optional[dict]]:
    """Resolve customer historical spend: snapshot -> DB recompute -> warning.

    Returns:
        Tuple of (spend_eur or None, warning dict or None)
    """
    brand_info = brand_info or {}

    spend = brand_info.get("total_spend_teuro")
    if spend:
        logger.info(
            f"[ExternalRunId {external_run_id}] Using historical spend from snapshot: {spend}"
        )
        return float(spend), None

    nielsen_brand = brand_info.get("nielsen_brand")
    if nielsen_brand:
        from src.services.stage1.repository import Stage1Repository

        repo = Stage1Repository(session)
        spend = await repo.get_nielsen_brand_total_spend(marke=nielsen_brand)
        if spend:
            logger.info(
                f"[ExternalRunId {external_run_id}] Recomputed historical spend from DB "
                f"via Stage1Repository.get_nielsen_brand_total_spend(marke={nielsen_brand!r}): "
                f"{spend}"
            )
            return spend, None

        logger.warning(
            f"[ExternalRunId {external_run_id}] DB recompute returned no spend for "
            f"nielsen_brand={nielsen_brand!r}"
        )

    logger.warning(f"[ExternalRunId {external_run_id}] Historical spend not available")
    return None, {
        "color": "yellow",
        "title": "Historical Spend Unavailable",
        "description": (
            "Customer's historical spend data not available. "
            "Budget recommendations may be less anchored to customer's typical scale."
        ),
    }


async def _validate_allocation_with_retry(
    llm_client,
    assembled_prompt,
    parsed_allocation: dict,
    normalized_user_channels_set: set,
    external_run_id: int,
    debug_dir: Optional[str] = None,
) -> tuple[dict, list, bool]:
    """Validate LLM allocation output; retry once on failure with paired warnings.

    Emits two distinct warnings when retry is attempted:
    1. At retry trigger — validation failed, regeneration starting
    2. After retry — success, still invalid, or retry call failed

    Returns:
        Tuple of (parsed_allocation, validation_warnings, did_retry)
    """
    validation_warnings: list = []
    did_retry = False

    is_valid, validation_errors = _check_allocation_validity(
        parsed_allocation, normalized_user_channels_set, external_run_id
    )
    if is_valid:
        return parsed_allocation, validation_warnings, did_retry

    validation_warnings.append({
        "color": "yellow",
        "title": "Allocation Validation Failed",
        "description": (
            f"AI output did not pass validation ({'; '.join(validation_errors)}). "
            "Regenerating allocation."
        ),
    })

    logger.warning(
        f"[ExternalRunId {external_run_id}] Allocation invalid, attempting retry..."
    )
    retry_result, retry_success = await _retry_llm_allocation(
        llm_client, assembled_prompt, external_run_id
    )

    if retry_success and retry_result:
        did_retry = True
        parsed_allocation = retry_result

        if debug_dir:
            with open(
                f"{debug_dir}/07_parsed_response_retry.json", "w", encoding="utf-8"
            ) as f:
                json.dump(parsed_allocation, f, indent=2, ensure_ascii=False)

        is_valid_after_retry, validation_errors_after_retry = _check_allocation_validity(
            parsed_allocation, normalized_user_channels_set, external_run_id
        )

        if is_valid_after_retry:
            validation_warnings.append({
                "color": "yellow",
                "title": "Allocation Regenerated",
                "description": (
                    "AI allocation was successfully regenerated after validation failed."
                ),
            })
        else:
            validation_warnings.append({
                "color": "yellow",
                "title": "Allocation Still Invalid After Retry",
                "description": (
                    "Regenerated allocation still has validation issues. "
                    "Using best available output with adjustments."
                ),
            })
            for error in validation_errors_after_retry:
                validation_warnings.append({
                    "color": "yellow",
                    "title": "Allocation Issue",
                    "description": error,
                })
    else:
        validation_warnings.append({
            "color": "yellow",
            "title": "Allocation Retry Failed",
            "description": (
                "AI allocation regeneration failed. Using original output with adjustments."
            ),
        })
        for error in validation_errors:
            validation_warnings.append({
                "color": "yellow",
                "title": "Allocation Issue",
                "description": error,
            })

    return parsed_allocation, validation_warnings, did_retry


def _check_guardrail_violations(
    allocations: list,
    external_run_id: int,
) -> list:
    """
    Check allocations against guardrail guidelines.
    Returns warnings for violations but does NOT modify values.

    Guidelines:
    - Minimum 5% per channel
    - Maximum 60% single channel
    - At least 3 channels allocated
    """
    warnings = []

    # Guideline: Minimum 5% per channel
    for alloc in allocations:
        share = alloc.get("share_pct", 0)
        channel = alloc.get("channel", "Unknown")
        if 0 < share < 5.0:
            logger.info(f"[ExternalRunId {external_run_id}] Guardrail: {channel} at {share}% (below 5% guideline)")
            warnings.append({
                "color": "yellow",
                "title": "Low Allocation",
                "description": f"{channel} allocated {share:.1f}% (below 5% guideline). Value shown as-is per AI recommendation.",
            })

    # Guideline: Maximum 60% single channel
    for alloc in allocations:
        share = alloc.get("share_pct", 0)
        channel = alloc.get("channel", "Unknown")
        if share > 60.0:
            logger.info(f"[ExternalRunId {external_run_id}] Guardrail: {channel} at {share}% (above 60% guideline)")
            warnings.append({
                "color": "yellow",
                "title": "High Concentration",
                "description": f"{channel} allocated {share:.1f}% (above 60% guideline). Value shown as-is per AI recommendation.",
            })

    # Guideline: At least 3 channels
    if len(allocations) < 3:
        logger.info(f"[ExternalRunId {external_run_id}] Guardrail: Only {len(allocations)} channels (guideline suggests 3+)")
        warnings.append({
            "color": "yellow",
            "title": "Limited Diversification",
            "description": f"Only {len(allocations)} channel(s) allocated (guideline suggests 3+). Allocation shown as-is per AI recommendation.",
        })

    return warnings


async def get_ai_run_by_external_id(db: AsyncSession, external_run_id: int) -> Optional[PrismaProjectVersionAiRun]:
    """Look up ProjectVersionAiRun by externalRunId."""
    query = select(PrismaProjectVersionAiRun).where(
        PrismaProjectVersionAiRun.externalRunId == external_run_id
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_project_version(db: AsyncSession, project_version_id: str) -> Optional[PrismaProjectVersion]:
    """Look up ProjectVersion by ID."""
    query = select(PrismaProjectVersion).where(
        PrismaProjectVersion.id == project_version_id
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


def extract_campaign_inputs(project_version: PrismaProjectVersion) -> CampaignInputs:
    """Extract campaign inputs from ProjectVersion."""
    total_budget = extract_budget_from_goal_text(project_version.goalText)
    direction = map_goal_mode_to_direction(project_version.goalMode)

    return CampaignInputs(
        customer_name=project_version.customer,
        industry=project_version.industry,
        brand_kpi=project_version.brandKpi,
        media_channels=list(project_version.mediaChannels) if project_version.mediaChannels else [],
        goal_mode=project_version.goalMode,
        goal_text=project_version.goalText,
        total_budget=total_budget,
        direction=direction,
    )


def should_skip_stage1(
    current_inputs: CampaignInputs,
    ai_run: PrismaProjectVersionAiRun,
) -> bool:
    """Determine if Stage 1 can be skipped based on what changed.

    Stage 1 can be skipped if ONLY these fields changed:
    - goal_text
    - total_budget
    - brand_kpi
    - direction
    - mediaChannels

    Stage 1 MUST run if ANY of these changed:
    - customer_name
    - industry
    - confirmedCompetitors

    Also requires existing competitorSnapshot and confirmedCompetitors from a previous run.

    Returns:
        True if Stage 1 can be skipped, False otherwise
    """
    # Must have existing competitor data to skip Stage 1
    if not ai_run.competitorSnapshot:
        logger.info("Stage 1 required: No existing competitorSnapshot")
        return False

    if not ai_run.confirmedCompetitors:
        logger.info("Stage 1 required: No existing confirmedCompetitors")
        return False

    # Get last run inputs from rawPayload if stored, otherwise we need Stage 1
    last_inputs = ai_run.rawPayload.get("last_inputs") if ai_run.rawPayload else None

    if not last_inputs:
        # First run or no cached inputs - must run Stage 1
        logger.info("Stage 1 required: No cached last_inputs in rawPayload")
        return False

    # Check fields that REQUIRE Stage 1 if changed
    if current_inputs.customer_name != last_inputs.get("customer_name"):
        logger.info(f"Stage 1 required: customer_name changed from '{last_inputs.get('customer_name')}' to '{current_inputs.customer_name}'")
        return False

    if current_inputs.industry != last_inputs.get("industry"):
        logger.info(f"Stage 1 required: industry changed from '{last_inputs.get('industry')}' to '{current_inputs.industry}'")
        return False

    # If we get here, only preference fields changed - can skip Stage 1
    logger.info("Stage 1 can be skipped: Only preference fields changed")
    return True


def save_inputs_to_raw_payload(
    ai_run: PrismaProjectVersionAiRun,
    inputs: CampaignInputs,
) -> None:
    """Save current inputs to rawPayload for future skip detection."""
    if ai_run.rawPayload is None:
        ai_run.rawPayload = {}

    ai_run.rawPayload["last_inputs"] = {
        "customer_name": inputs.customer_name,
        "industry": inputs.industry,
        "brand_kpi": inputs.brand_kpi,
        "goal_text": inputs.goal_text,
        "total_budget": inputs.total_budget,
        "direction": inputs.direction,
        "media_channels": inputs.media_channels,
    }

    # Mark JSONB column as modified for SQLAlchemy to detect the change
    flag_modified(ai_run, 'rawPayload')


# =============================================================================
# Background Task: Full Pipeline Processing
# =============================================================================

async def run_full_pipeline_background(
    external_run_id: int,
    prisma_ai_run_id: str,
):
    """Background task to run the full Stage 1-4 pipeline.

    This runs asynchronously after POST /runs returns.
    All state is stored in ProjectVersionAiRun.
    Uses shared connection pool from src/db/session.py.
    """
    from src.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        try:
            # Get the AI run record
            query = select(PrismaProjectVersionAiRun).where(
                PrismaProjectVersionAiRun.id == prisma_ai_run_id
            )
            result = await session.execute(query)
            ai_run = result.scalar_one_or_none()

            if not ai_run:
                logger.error(f"ProjectVersionAiRun {prisma_ai_run_id} not found")
                return

            # Get ProjectVersion for inputs
            project_version = await get_project_version(session, ai_run.projectVersionId)
            if not project_version:
                logger.error(f"ProjectVersion {ai_run.projectVersionId} not found")
                error_msg = "Project configuration not found. Please contact support."
                await _update_ai_run_status(session, ai_run, "failed", error=error_msg)
                await _insert_error_card(prisma_ai_run_id, external_run_id, error_msg)
                return

            # Extract campaign inputs
            inputs = extract_campaign_inputs(project_version)
            logger.info(f"[ExternalRunId {external_run_id}] Starting pipeline for {inputs.customer_name}")

            # Update status to matching (Stage 1)
            await _update_ai_run_status(session, ai_run, "matching", stage="S1", progress_pct=10)

            # =================================================================
            # Stage 1: Competitor Matching
            # =================================================================
            orchestrator = Stage1Orchestrator(session=session)

            user_input = UserCampaignInput(
                brand_name=inputs.customer_name,
                industry=inputs.industry,
                brand_kpi=inputs.brand_kpi,
                media_channels=inputs.media_channels,
                goal_direction=inputs.direction,
            )

            stage1_result = await orchestrator.process(user_input, run_id=str(external_run_id))
            stage1_ai_calls = stage1_result.ai_calls_count

            if stage1_result.status == Stage1Status.FAILED:
                raw_error = "; ".join(stage1_result.errors) if stage1_result.errors else "Stage 1 failed"
                error_msg = humanize_error(raw_error)
                _persist_trace_snapshot(ai_run, stage1_ai_calls, 0, False)
                await _update_ai_run_status(session, ai_run, "failed", error=error_msg)
                await _insert_error_card(prisma_ai_run_id, external_run_id, error_msg)
                logger.error(f"[ExternalRunId {external_run_id}] Stage 1 failed: {raw_error}")
                return

            # Store competitor data
            # YouGov brand labels (used as primary identifiers)
            yougov_brands = [c.brand_label for c in stage1_result.competitors]
            # Nielsen brand names (different naming convention, can be None)
            nielsen_brands = [c.nielsen_brand for c in stage1_result.competitors if c.nielsen_brand]

            competitor_snapshot = _build_competitor_snapshot(stage1_result, inputs.industry)

            # Validate that we have competitors before proceeding
            snapshot_competitors = competitor_snapshot.get("competitors", []) if competitor_snapshot else []
            if not snapshot_competitors:
                error_msg = "No competitors found for the given brand and industry. Please check your inputs."
                logger.error(f"[ExternalRunId {external_run_id}] {error_msg}")
                _persist_trace_snapshot(ai_run, stage1_ai_calls, 0, False)
                await _update_ai_run_status(session, ai_run, "failed", error=error_msg)
                await _insert_error_card(prisma_ai_run_id, external_run_id, error_msg)
                return

            ai_run.competitorSnapshot = competitor_snapshot

            # Save inputs for future skip detection
            save_inputs_to_raw_payload(ai_run, inputs)

            await session.commit()

            logger.info(f"[ExternalRunId {external_run_id}] Stage 1 completed: {len(yougov_brands)} YouGov brands, {len(nielsen_brands)} Nielsen brands")

            # Check if we should wait for confirmation or auto-proceed
            if not get_settings().bypass_competitor_confirmation:
                # Wait for user confirmation via POST /runs/{id}/competitors/confirm
                ai_run.status = "awaiting_confirmation"
                ai_run.stage = None
                ai_run.progressPct = 30
                ai_run.updatedAt = datetime.utcnow()
                await session.commit()
                logger.info(f"[ExternalRunId {external_run_id}] Waiting for competitor confirmation")
                return  # Stop here, Stage 2-4 will be triggered by confirm endpoint

            # Auto-confirm competitors (bypass mode)
            ai_run.confirmedCompetitors = yougov_brands
            await session.commit()

            # =================================================================
            # Stage 2: AI Allocation Generation
            # =================================================================
            await _update_ai_run_status(session, ai_run, "generating", stage="S2", progress_pct=40)

            debug_dir = _ensure_artifact_debug_dir(external_run_id)

            llm_client = OpenAIClient()
            prompt_service = PromptAssemblyService(session)

            # Build prompt
            total_budget = Decimal(str(inputs.total_budget)) if inputs.total_budget else None

            # Get customer's historical spend from Stage 1 data
            # NOTE: Despite the name 'total_spend_teuro', it's already converted to EUR in repository.py
            customer_historical_spend = None
            historical_spend_warning = None
            if stage1_result.brand_data and stage1_result.brand_data.total_spend_teuro:
                customer_historical_spend = stage1_result.brand_data.total_spend_teuro  # Already in EUR
            elif stage1_result.confirmed_brand and stage1_result.confirmed_brand.nielsen_brand:
                from src.services.stage1.repository import Stage1Repository

                repo = Stage1Repository(session)
                customer_historical_spend = await repo.get_nielsen_brand_total_spend(
                    marke=stage1_result.confirmed_brand.nielsen_brand
                )
                if customer_historical_spend:
                    logger.info(
                        f"[ExternalRunId {external_run_id}] Recomputed historical spend from DB "
                        f"via Stage1Repository.get_nielsen_brand_total_spend("
                        f"marke={stage1_result.confirmed_brand.nielsen_brand!r}): "
                        f"{customer_historical_spend}"
                    )

            if customer_historical_spend is None:
                logger.warning(
                    f"[ExternalRunId {external_run_id}] Historical spend not available from Stage 1"
                )
                historical_spend_warning = {
                    "color": "yellow",
                    "title": "Historical Spend Unavailable",
                    "description": "Customer's historical spend data not available. Budget recommendations may be less anchored to customer's typical scale.",
                }

            # Inform Stage 2 prompt if the user changed media channels for this run
            additional_context = inputs.goal_text
            if (ai_run.rawPayload or {}).get("media_channels_changed"):
                channels_note = "Note: The user has updated the media channel selection for this run. The provided channel list reflects the user's current choice."
                additional_context = f"{additional_context}\n\n{channels_note}" if additional_context else channels_note
                logger.info(f"[ExternalRunId {external_run_id}] media_channels_changed=True - adding channel update note to Stage 2 prompt")

            prompt_input = PromptAssemblyInput(
                customer_name=inputs.customer_name,
                industry=inputs.industry,
                brand_kpi=inputs.brand_kpi,
                total_budget=total_budget,
                time_period_start=None,
                time_period_end=None,
                channels=inputs.media_channels,
                # IMPORTANT: Use separate brand lists - YouGov and Nielsen have different naming conventions
                nielsen_brands=nielsen_brands[:5],  # Nielsen brand names (e.g., "EHRMANN")
                yougov_brands=yougov_brands[:5],    # YouGov brand labels (e.g., "Ehrmann Almighurt")
                additional_context=additional_context,
                goal_direction=inputs.direction,  # Pass direction to Stage 2
                goal_text=inputs.goal_text,  # Pass goal text for Goal→Budget mode
                customer_historical_spend=customer_historical_spend,  # Customer's historical spend in EUR
            )

            assembled_prompt = await prompt_service.assemble_prompt(
                input_params=prompt_input,
                wirtschaftsgruppe=inputs.industry,
            )

            # Save Stage 2 prompt artifact
            with open(f"{debug_dir}/06_prompt.txt", "w", encoding="utf-8") as f:
                f.write("=== SYSTEM PROMPT ===\n")
                f.write(assembled_prompt.system_prompt)
                f.write("\n\n=== USER PROMPT ===\n")
                f.write(assembled_prompt.user_prompt)
                f.write("\n\n=== METADATA ===\n")
                f.write(json.dumps(assembled_prompt.metadata, indent=2, default=str))

            logger.info(f"[ExternalRunId {external_run_id}] Calling OpenAI...")

            llm_response = await llm_client.generate(
                system_prompt=assembled_prompt.system_prompt,
                user_prompt=assembled_prompt.user_prompt,
                temperature=0.7,
                max_tokens=4096,
                json_mode=True,
            )

            logger.info(f"[ExternalRunId {external_run_id}] OpenAI response: {llm_response.total_tokens} tokens")

            with open(f"{debug_dir}/06_llm_response.txt", "w", encoding="utf-8") as f:
                f.write("=== RAW LLM RESPONSE ===\n")
                f.write(f"Model: {llm_response.model}\n")
                f.write(f"Total Tokens: {llm_response.total_tokens}\n")
                f.write(f"Prompt Tokens: {llm_response.prompt_tokens}\n")
                f.write(f"Completion Tokens: {llm_response.completion_tokens}\n")
                f.write("\n=== CONTENT ===\n")
                f.write(llm_response.content)

            # =================================================================
            # Stage 3: Parse Response
            # =================================================================
            await _update_ai_run_status(session, ai_run, "parsing", stage="S3", progress_pct=70)

            # Parse LLM response
            try:
                parsed_allocation = json.loads(llm_response.content)
            except json.JSONDecodeError as e:
                logger.error(f"[ExternalRunId {external_run_id}] Failed to parse LLM response: {e}")
                error_msg = "Could not process AI response. Please try running again."
                _persist_trace_snapshot(ai_run, stage1_ai_calls, 1, False)
                await _update_ai_run_status(session, ai_run, "failed", error=error_msg)
                await _insert_error_card(prisma_ai_run_id, external_run_id, error_msg)
                return

            with open(f"{debug_dir}/07_parsed_response.json", "w", encoding="utf-8") as f:
                json.dump(parsed_allocation, f, indent=2, ensure_ascii=False)

            # Get user-selected channels for validation
            user_channels = inputs.media_channels or []
            normalized_user_channels_set = _normalized_user_channel_set(user_channels)

            # =================================================================
            # Validation & Retry Logic
            # =================================================================
            parsed_allocation, validation_warnings, did_retry = await _validate_allocation_with_retry(
                llm_client,
                assembled_prompt,
                parsed_allocation,
                normalized_user_channels_set,
                external_run_id,
                debug_dir=debug_dir,
            )
            stage2_ai_calls = 2 if did_retry else 1

            # Build allocation result
            allocations = []
            channels_data = parsed_allocation.get("channels", parsed_allocation.get("allocations", []))

            # Map to allowed Nielsen channel names
            allowed_nielsen_channels = get_allowed_nielsen_channels(user_channels)
            logger.info(f"[ExternalRunId {external_run_id}] User channels: {user_channels} -> Allowed Nielsen: {allowed_nielsen_channels}")

            # Try to extract total budget from LLM response if not in inputs
            total_budget = inputs.total_budget
            if not total_budget:
                # Check if LLM returned a total budget
                llm_total = parsed_allocation.get("totalBudgetEur", parsed_allocation.get("total_budget_eur"))
                if llm_total:
                    total_budget = float(llm_total)

            for channel in channels_data:
                # Get Nielsen channel name from LLM response
                nielsen_channel = channel.get("name", channel.get("channel", "Unknown"))

                # Normalize channel name to UI name FIRST (handles all German/English variants)
                ui_channel = _normalize_channel_name(nielsen_channel)

                # Filter: Only include channels the user selected (compare normalized UI names)
                if normalized_user_channels_set and ui_channel not in normalized_user_channels_set:
                    logger.debug(f"[ExternalRunId {external_run_id}] Skipping channel {nielsen_channel} -> {ui_channel} - not in user selection {normalized_user_channels_set}")
                    continue

                # Get share percentage - handle various field names
                share_pct = float(
                    channel.get("percentage") or
                    channel.get("share_pct") or
                    channel.get("sharePct") or
                    0
                )

                # Get budget amount - handle various field names from LLM
                budget_value = (
                    channel.get("amount") or
                    channel.get("budget") or
                    channel.get("budgetGrossEur") or  # camelCase from LLM
                    channel.get("budget_gross_eur") or  # snake_case
                    None
                )

                # If no explicit budget but we have total_budget and share_pct, calculate it
                if budget_value:
                    budget_gross_eur = float(budget_value)
                elif total_budget and share_pct > 0:
                    budget_gross_eur = round(total_budget * share_pct / 100, 2)
                else:
                    budget_gross_eur = None

                allocations.append({
                    "channel": ui_channel,  # Use UI channel name instead of Nielsen name
                    "share_pct": share_pct,
                    "budget_gross_eur": budget_gross_eur,
                    "reasoning": channel.get("rationale", channel.get("reasoning", "")),
                })

            # =================================================================
            # Post-processing: Validate and normalize allocations
            # =================================================================

            # Step 1: Deduplicate channels (merge allocations for same underlying channel)
            # This handles cases where user selected "Online" and LLM returned "ONLINE" -> "Digital"
            channel_map = {}
            for a in allocations:
                channel_name = a["channel"]
                if channel_name in channel_map:
                    # Merge: add percentages and budgets
                    channel_map[channel_name]["share_pct"] += a["share_pct"]
                    if a["budget_gross_eur"] and channel_map[channel_name]["budget_gross_eur"]:
                        channel_map[channel_name]["budget_gross_eur"] += a["budget_gross_eur"]
                    # Append reasoning
                    channel_map[channel_name]["reasoning"] += f" {a['reasoning']}"
                else:
                    channel_map[channel_name] = a.copy()

            allocations = list(channel_map.values())
            logger.info(f"[ExternalRunId {external_run_id}] After deduplication: {len(allocations)} unique channels")

            # Step 2: Compare against normalized user channel set (same as validation)
            normalized_user_channels = normalized_user_channels_set

            logger.info(f"[ExternalRunId {external_run_id}] User channels (normalized): {normalized_user_channels}")

            # Step 3: Normalize shares to 100% if needed
            total_share = sum(a["share_pct"] for a in allocations)
            if total_share > 0 and abs(total_share - 100.0) > 0.01:
                logger.warning(f"[ExternalRunId {external_run_id}] Share percentages sum to {total_share}%, normalizing to 100%")
                scale_factor = 100.0 / total_share
                for a in allocations:
                    a["share_pct"] = round(a["share_pct"] * scale_factor, 2)
                    # Recalculate budget if we have total_budget
                    if total_budget and a["share_pct"] > 0:
                        a["budget_gross_eur"] = round(total_budget * a["share_pct"] / 100, 2)

            # Step 4: Check for missing user-selected channels (using normalized names)
            # NOTE: Do NOT fabricate allocations for missing channels.
            # Instead, add warnings explaining why they were excluded.
            allocated_channels = {a["channel"] for a in allocations}
            missing_channels = normalized_user_channels - allocated_channels

            if missing_channels:
                logger.warning(f"[ExternalRunId {external_run_id}] Channels excluded (no AI allocation): {missing_channels}")
                for missing_ch in missing_channels:
                    validation_warnings.append({
                        "color": "yellow",
                        "title": "Channel Excluded",
                        "description": f"{missing_ch} was not allocated by AI — insufficient benchmark data for this channel.",
                    })

                # Re-normalize remaining allocations to 100% (don't add missing channels)
                total_share = sum(a["share_pct"] for a in allocations)
                if total_share > 0 and abs(total_share - 100.0) > 0.01:
                    logger.info(f"[ExternalRunId {external_run_id}] Normalizing {total_share}% to 100% after excluding missing channels")
                    scale_factor = 100.0 / total_share
                    for a in allocations:
                        a["share_pct"] = round(a["share_pct"] * scale_factor, 2)
                        if total_budget and a["share_pct"] > 0:
                            a["budget_gross_eur"] = round(total_budget * a["share_pct"] / 100, 2)

            # Log final allocation summary
            final_total = sum(a["share_pct"] for a in allocations)
            logger.info(f"[ExternalRunId {external_run_id}] Final allocations: {len(allocations)} channels, {final_total}% total")

            # =================================================================
            # Stage 4: Store Results
            # =================================================================
            await _update_ai_run_status(session, ai_run, "completing", stage="S4", progress_pct=90)

            # Calculate total budget from allocations if not provided
            if not total_budget:
                budget_sum = sum(a["budget_gross_eur"] or 0 for a in allocations)
                if budget_sum > 0:
                    total_budget = budget_sum

            # Recalculate ALL budgets from final share_pct - LLM-provided amounts can be
            # inconsistent with shares, and share post-processing above doesn't always
            # recalculate budgets (e.g., missing-channel scaling with shares still at 100%)
            if total_budget and allocations:
                for a in allocations:
                    a["budget_gross_eur"] = round(float(total_budget) * a["share_pct"] / 100, 2)
                # Force exact match: put any rounding residue on the largest channel
                residue = round(float(total_budget) - sum(a["budget_gross_eur"] for a in allocations), 2)
                if residue:
                    largest = max(allocations, key=lambda a: a["budget_gross_eur"])
                    largest["budget_gross_eur"] = round(largest["budget_gross_eur"] + residue, 2)

            # Extract kpi_projection from LLM response - MUST NOT be null
            kpi_projection_raw = parsed_allocation.get("kpi_projection", parsed_allocation.get("kpiProjection"))
            kpi_projection = None
            if kpi_projection_raw is not None:
                try:
                    kpi_projection = float(kpi_projection_raw)
                except (TypeError, ValueError):
                    logger.warning(f"[ExternalRunId {external_run_id}] Could not parse kpi_projection: {kpi_projection_raw}")
                    kpi_projection = None
                    validation_warnings.append({
                        "color": "yellow",
                        "title": "KPI Projection Invalid",
                        "description": "AI returned invalid KPI projection value. Defaulting to 0.",
                    })

            # If LLM didn't return kpi_projection after retry, default to 0 with warning
            if kpi_projection is None:
                logger.warning(f"[ExternalRunId {external_run_id}] LLM did not return kpi_projection, defaulting to 0.0")
                kpi_projection = 0.0
                validation_warnings.append({
                    "color": "yellow",
                    "title": "KPI Projection Unavailable",
                    "description": "AI could not estimate KPI impact. Defaulting to 0 (no projected change).",
                })

            # Extract confidence_score from LLM response
            confidence_raw = parsed_allocation.get("confidence", parsed_allocation.get("confidence_score"))
            confidence_score = None
            if confidence_raw is not None:
                try:
                    confidence_score = float(confidence_raw)
                except (TypeError, ValueError):
                    logger.warning(f"[ExternalRunId {external_run_id}] Could not parse confidence: {confidence_raw}")
                    confidence_score = None
                    validation_warnings.append({
                        "color": "yellow",
                        "title": "Confidence Score Invalid",
                        "description": "AI returned invalid confidence value. Defaulting to 0.",
                    })

            # If LLM didn't return confidence after retry, default to 0 with warning
            if confidence_score is None:
                logger.warning(f"[ExternalRunId {external_run_id}] LLM did not return confidence, defaulting to 0.0")
                confidence_score = 0.0
                validation_warnings.append({
                    "color": "yellow",
                    "title": "Confidence Score Unavailable",
                    "description": "AI could not assess confidence level. Defaulting to 0 (low confidence).",
                })

            # Build structured warnings from context
            # Identify competitors excluded due to missing Nielsen data
            excluded_competitors = [
                c.brand_label for c in stage1_result.competitors
                if c.nielsen_brand is None
            ] if stage1_result and stage1_result.competitors else []

            structured_warnings = build_warnings_from_context(
                parsed_allocation=parsed_allocation,
                total_budget=float(total_budget) if total_budget else None,
                competitor_data=[c for c in stage1_result.competitors if c.nielsen_brand] if stage1_result else [],
                historical_spend=customer_historical_spend,
                excluded_competitors=excluded_competitors,
            )

            # Add validation warnings (from retry logic, missing channels, etc.)
            structured_warnings.extend(validation_warnings)

            # Add historical spend warning if applicable
            if historical_spend_warning:
                structured_warnings.append(historical_spend_warning)

            # Check guardrail violations (warnings only, values not modified)
            guardrail_warnings = _check_guardrail_violations(allocations, external_run_id)
            structured_warnings.extend(guardrail_warnings)

            allocation_result = {
                "run_id": external_run_id,
                "allocations": allocations,
                "total_budget_eur": total_budget,
                "kpi_projection": kpi_projection,
                "reasoning_summary": parsed_allocation.get("summary", parsed_allocation.get("reasoning_summary", "")),
                "confidence_score": confidence_score,  # Use extracted/defaulted value, not raw
                "warnings": structured_warnings,
                "is_cached": False,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }

            with open(f"{debug_dir}/08_final_result.json", "w", encoding="utf-8") as f:
                json.dump(allocation_result, f, indent=2, ensure_ascii=False)
            logger.info(f"[ExternalRunId {external_run_id}] Artifact files saved to {debug_dir}/")
            _create_artifact_zip(external_run_id, debug_dir)

            # Store in ProjectVersionAiRun
            ai_run.allocationResult = allocation_result
            ai_run.status = "completed"
            ai_run.completedAt = datetime.utcnow()
            ai_run.updatedAt = datetime.utcnow()
            ai_run.progressPct = 100
            ai_run.stage = None
            _persist_trace_snapshot(ai_run, stage1_ai_calls, stage2_ai_calls, did_retry)
            flag_modified(ai_run, 'allocationResult')  # JSONB column needs explicit flag

            await session.commit()

            # Insert feedback cards into chatSnapshot using FRESH session
            # This is completely isolated from the pipeline ORM state
            logger.info(f"[ExternalRunId {external_run_id}] CALLING _insert_feedback_cards with ai_run.id={ai_run.id}")
            await _insert_feedback_cards(ai_run.id, external_run_id, allocation_result)
            logger.info(f"[ExternalRunId {external_run_id}] _insert_feedback_cards RETURNED")

            logger.info(f"[ExternalRunId {external_run_id}] Pipeline completed successfully")

        except Exception as e:
            logger.error(f"[ExternalRunId {external_run_id}] Pipeline failed: {str(e)}", exc_info=True)
            human_error = humanize_error(str(e))
            try:
                await session.rollback()
                # Try to update status to failed
                query = select(PrismaProjectVersionAiRun).where(
                    PrismaProjectVersionAiRun.id == prisma_ai_run_id
                )
                result = await session.execute(query)
                ai_run = result.scalar_one_or_none()
                if ai_run:
                    await _update_ai_run_status(session, ai_run, "failed", error=human_error)
            except Exception as e2:
                logger.error(f"Failed to update status after error: {e2}")

            # Insert run_failed card into chatSnapshot (uses separate session)
            await _insert_error_card(prisma_ai_run_id, external_run_id, human_error)


def _build_allocation_summary(allocation_result: dict) -> str:
    """Build human-readable allocation summary for the feedback card."""
    parts = []

    # Total budget if available
    total = allocation_result.get("total_budget_eur")
    if total:
        parts.append(f"Total Budget: EUR {total:,.0f}")

    # Channel breakdown (field is "allocations" not "channel_allocations")
    allocations = allocation_result.get("allocations", [])
    if allocations:
        parts.append("\nAllocation:")
        for alloc in allocations:
            channel = alloc.get("channel", "Unknown")
            amount = alloc.get("budget_gross_eur") or alloc.get("amount_eur") or 0
            pct = alloc.get("share_pct") or alloc.get("percentage") or 0
            parts.append(f"  • {channel}: EUR {amount:,.0f} ({pct:.1f}%)")

    # Reasoning summary
    reasoning = allocation_result.get("reasoning_summary")
    if reasoning:
        parts.append(f"\nReasoning: {reasoning}")

    return "\n".join(parts)


async def _insert_feedback_cards(
    ai_run_id: str,
    external_run_id: int,
    allocation_result: dict,
) -> None:
    """Insert feedback cards into chatSnapshot when allocation completes.

    Inserts at the BEGINNING of messages array:
    1. Warning cards (if any) - one message per warning
    2. Allocation summary card

    Stale feedback cards from previous runs are removed first, so the
    snapshot always contains cards for the latest allocation only.

    Cards have role="system" and card_type for frontend styling.

    Uses a FRESH database session to completely isolate from pipeline ORM state.
    """
    from sqlalchemy import update, text
    from src.db.session import async_session_factory

    try:
        logger.info(f"[ExternalRunId {external_run_id}] Starting _insert_feedback_cards with fresh session, ai_run_id={ai_run_id} (type={type(ai_run_id).__name__})")

        # Use completely fresh session, isolated from pipeline session
        async with async_session_factory() as fresh_session:
            # Get existing chatSnapshot directly from database
            query = select(PrismaProjectVersionAiRun.chatSnapshot).where(
                PrismaProjectVersionAiRun.id == ai_run_id
            )
            result = await fresh_session.execute(query)
            existing_snapshot = result.scalar_one_or_none()
            existing_messages = existing_snapshot.get("messages", []) if existing_snapshot else []

            logger.info(f"[ExternalRunId {external_run_id}] Existing chatSnapshot has {len(existing_messages)} messages")

            # Drop stale feedback cards from previous runs so cards are never duplicated;
            # the snapshot should only carry cards for the latest allocation result.
            stale_count = sum(
                1 for msg in existing_messages
                if msg.get("role") == "system"
                and msg.get("card_type") in ("allocation_summary", "warning")
            )
            if stale_count:
                existing_messages = [
                    msg for msg in existing_messages
                    if not (
                        msg.get("role") == "system"
                        and msg.get("card_type") in ("allocation_summary", "warning")
                    )
                ]
                logger.info(f"[ExternalRunId {external_run_id}] Removed {stale_count} stale feedback cards from previous run")

            # Build new cards to insert at beginning
            cards_to_insert = []
            timestamp = datetime.utcnow().isoformat()

            # 1. Warning cards (if any) — now with structured color/title/description
            warnings = allocation_result.get("warnings", [])
            for i, warning in enumerate(warnings):
                # Handle both structured warnings (dict) and legacy string warnings
                if isinstance(warning, dict):
                    cards_to_insert.append({
                        "id": f"warning_{i}",
                        "role": "system",
                        "card_type": "warning",
                        "color": warning.get("color", "yellow"),
                        "title": warning.get("title", "Warning"),
                        "description": warning.get("description", ""),
                        "created_at": timestamp,
                    })
                else:
                    # Legacy string warning — convert to structured
                    cards_to_insert.append({
                        "id": f"warning_{i}",
                        "role": "system",
                        "card_type": "warning",
                        "color": "yellow",
                        "title": "Notice",
                        "description": str(warning),
                        "created_at": timestamp,
                    })

            # 2. Allocation summary card
            summary_content = _build_allocation_summary(allocation_result)
            cards_to_insert.append({
                "id": "summary_0",
                "role": "system",
                "card_type": "allocation_summary",
                "content": summary_content,
                "created_at": timestamp,
            })

            # Re-index existing messages (shift integer IDs)
            for idx, msg in enumerate(existing_messages):
                if isinstance(msg.get("id"), int):
                    msg["id"] = idx + len(cards_to_insert)

            # Combine: cards first, then existing messages
            chat_snapshot = {
                "messages": cards_to_insert + existing_messages,
                "updated_at": timestamp,
            }

            # Use direct SQL UPDATE
            stmt = update(PrismaProjectVersionAiRun).where(
                PrismaProjectVersionAiRun.id == ai_run_id
            ).values(chatSnapshot=chat_snapshot)
            await fresh_session.execute(stmt)
            await fresh_session.commit()

            logger.info(f"[ExternalRunId {external_run_id}] Inserted {len(cards_to_insert)} feedback cards into chatSnapshot")
            logger.info(f"[ExternalRunId {external_run_id}] chatSnapshot now has {len(chat_snapshot.get('messages', []))} total messages")

            # Verify the update
            verify_result = await fresh_session.execute(
                select(PrismaProjectVersionAiRun.chatSnapshot).where(
                    PrismaProjectVersionAiRun.id == ai_run_id
                )
            )
            verified_snapshot = verify_result.scalar_one_or_none()
            if verified_snapshot:
                logger.info(f"[ExternalRunId {external_run_id}] VERIFIED: chatSnapshot has {len(verified_snapshot.get('messages', []))} messages after commit")
            else:
                logger.warning(f"[ExternalRunId {external_run_id}] VERIFICATION FAILED: chatSnapshot is NULL after commit")

    except Exception as e:
        logger.error(f"[ExternalRunId {external_run_id}] FEEDBACK CARDS FAILED: {e}", exc_info=True)
        # Do not raise - feedback cards are non-critical, pipeline should complete regardless


async def _insert_error_card(
    ai_run_id: str,
    external_run_id: int,
    error_message: str,
) -> None:
    """Insert run_failed card into chatSnapshot when run fails.

    Creates a red error card with the human-readable error message.
    Removes any previous error cards to avoid duplicates.

    Uses a FRESH database session to completely isolate from pipeline ORM state.
    """
    from sqlalchemy import update
    from src.db.session import async_session_factory

    try:
        logger.info(f"[ExternalRunId {external_run_id}] Inserting run_failed card into chatSnapshot")

        async with async_session_factory() as fresh_session:
            # Get existing chatSnapshot
            query = select(PrismaProjectVersionAiRun.chatSnapshot).where(
                PrismaProjectVersionAiRun.id == ai_run_id
            )
            result = await fresh_session.execute(query)
            existing_snapshot = result.scalar_one_or_none()
            existing_messages = existing_snapshot.get("messages", []) if existing_snapshot else []

            # Remove any previous error cards to avoid duplicates
            existing_messages = [
                msg for msg in existing_messages
                if not (msg.get("role") == "system" and msg.get("card_type") == "run_failed")
            ]

            timestamp = datetime.utcnow().isoformat()
            error_card = {
                "id": "error_0",
                "role": "system",
                "card_type": "run_failed",
                "color": "red",
                "title": get_error_title(error_message),
                "description": error_message,
                "created_at": timestamp,
            }

            chat_snapshot = {
                "messages": [error_card] + existing_messages,
                "updated_at": timestamp,
            }

            # Use direct SQL UPDATE
            stmt = update(PrismaProjectVersionAiRun).where(
                PrismaProjectVersionAiRun.id == ai_run_id
            ).values(chatSnapshot=chat_snapshot)
            await fresh_session.execute(stmt)
            await fresh_session.commit()

            logger.info(f"[ExternalRunId {external_run_id}] Inserted run_failed card into chatSnapshot")

    except Exception as e:
        logger.error(f"[ExternalRunId {external_run_id}] Failed to insert error card: {e}", exc_info=True)
        # Do not raise - error cards are non-critical


async def _update_ai_run_status(
    session: AsyncSession,
    ai_run: PrismaProjectVersionAiRun,
    status: str,
    stage: Optional[str] = None,
    progress_pct: Optional[int] = None,
    error: Optional[str] = None,
):
    """Update ProjectVersionAiRun status fields."""
    ai_run.status = status
    ai_run.updatedAt = datetime.utcnow()

    if stage is not None:
        ai_run.stage = stage
    if progress_pct is not None:
        ai_run.progressPct = progress_pct
    if error is not None:
        # Error message should already be humanized by caller
        ai_run.errorMessage = error
    if status == "matching":
        ai_run.startedAt = datetime.utcnow()

    await session.commit()


def _artifact_run_dir(run_id: int) -> str:
    return f"debug_output/run_{run_id}"


def _artifact_zip_path(run_id: int) -> str:
    return f"debug_output/run_{run_id}.zip"


def _ensure_artifact_debug_dir(external_run_id: int) -> str:
    debug_dir = _artifact_run_dir(external_run_id)
    os.makedirs(debug_dir, exist_ok=True)
    return debug_dir


def _create_artifact_zip(external_run_id: int, debug_dir: str) -> None:
    """Zip artifact files and remove the run directory after successful archive."""
    zip_path = _artifact_zip_path(external_run_id)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(debug_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, debug_dir)
                    zipf.write(file_path, arcname)
        shutil.rmtree(debug_dir)
        logger.info(f"[ExternalRunId {external_run_id}] Artifact ZIP created: {zip_path}")
    except Exception as zip_error:
        logger.warning(f"[ExternalRunId {external_run_id}] Failed to create artifact ZIP: {zip_error}")


def _build_trace_snapshot(
    stage1_ai_calls: int,
    stage2_ai_calls: int,
    did_retry: bool,
) -> dict:
    return {
        "llm_calls_count": stage1_ai_calls + stage2_ai_calls,
        "stage1_ai_calls": stage1_ai_calls,
        "stage2_ai_calls": stage2_ai_calls,
        "stage2_retry": did_retry,
    }


def _persist_trace_snapshot(
    ai_run: PrismaProjectVersionAiRun,
    stage1_ai_calls: int,
    stage2_ai_calls: int,
    did_retry: bool,
) -> None:
    ai_run.traceSnapshot = _build_trace_snapshot(
        stage1_ai_calls, stage2_ai_calls, did_retry
    )
    flag_modified(ai_run, "traceSnapshot")


def _build_competitor_snapshot(result: Stage1Result, industry: str) -> dict:
    """Build the competitor snapshot JSON for ProjectVersionAiRun.competitorSnapshot."""
    competitors = []
    for c in result.competitors:
        competitors.append({
            "nielsen_brand": c.nielsen_brand,
            "yougov_brand_label": c.brand_label,
            "wirtschaftsgruppe": industry,
            "has_nielsen_data": c.nielsen_brand is not None,
            "has_yougov_data": True,
            "total_spend_eur": c.total_spend_teuro * 1000 if c.total_spend_teuro else None,
            "match_confidence": 1.0,
            "avg_kpi_score": c.avg_kpi_score,
        })

    return {
        "competitors": competitors,
        "brand_info": {
            "brand_label": result.confirmed_brand.yougov_brand if result.confirmed_brand else None,
            "nielsen_brand": result.confirmed_brand.nielsen_brand if result.confirmed_brand else None,
            "match_type": result.confirmed_brand.match_type.value if result.confirmed_brand else None,
            "confidence": result.confirmed_brand.confidence if result.confirmed_brand else None,
            # Historical spend from brand data (already in EUR despite the name)
            "total_spend_teuro": result.brand_data.total_spend_teuro if result.brand_data else None,
        } if result.confirmed_brand else None,
        "yougov_sectors": result.yougov_sectors,
        "nielsen_sectors": result.nielsen_sectors,
    }


async def _run_stages_2_to_4_pipeline(
    prisma_ai_run_id: str,
    external_run_id: int,
    previous_shares: Optional[dict] = None,
    previous_completed_at: Optional[str] = None,
):
    """Run Stages 2-4 after competitor confirmation.

    This is called from the confirm endpoint after user approves competitors.
    Stage 1 data is read from competitorSnapshot in the database.
    Uses shared connection pool from src/db/session.py.

    Args:
        previous_shares: channel -> share_pct of the previous run's allocation
            (captured before allocationResult is cleared); baseline for explicit
            chat channel adjustments.
        previous_completed_at: ISO timestamp of the previous run's completion;
            only chat messages newer than this are considered for preference
            extraction (older ones were already applied to the previous result).
    """
    from src.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        try:
            # Get the AI run record
            query = select(PrismaProjectVersionAiRun).where(
                PrismaProjectVersionAiRun.id == prisma_ai_run_id
            )
            result = await session.execute(query)
            ai_run = result.scalar_one_or_none()

            if not ai_run:
                logger.error(f"ProjectVersionAiRun {prisma_ai_run_id} not found")
                return

            # Get ProjectVersion for inputs
            project_version = await get_project_version(session, ai_run.projectVersionId)
            if not project_version:
                logger.error(f"ProjectVersion {ai_run.projectVersionId} not found")
                error_msg = "Project configuration not found. Please contact support."
                await _update_ai_run_status(session, ai_run, "failed", error=error_msg)
                await _insert_error_card(prisma_ai_run_id, external_run_id, error_msg)
                return

            # Extract campaign inputs
            inputs = extract_campaign_inputs(project_version)
            logger.info(f"[ExternalRunId {external_run_id}] Starting Stage 2-4 pipeline for {inputs.customer_name}")

            # Get confirmed competitors (set by frontend)
            # confirmedCompetitors contains YouGov brand names (e.g., "Exquisa", "Landliebe")
            confirmed_yougov = ai_run.confirmedCompetitors or []

            # Look up Nielsen names from competitorSnapshot (stored during Stage 1)
            # competitorSnapshot has both YouGov and Nielsen names for each competitor
            snapshot = ai_run.competitorSnapshot or {}
            snapshot_competitors = snapshot.get("competitors", [])

            # Build mapping: YouGov name -> Nielsen name
            yougov_to_nielsen = {
                c.get("yougov_brand_label"): c.get("nielsen_brand")
                for c in snapshot_competitors
                if c.get("nielsen_brand")
            }

            # Map confirmed YouGov names to Nielsen names
            yougov_brands = confirmed_yougov
            nielsen_brands = [yougov_to_nielsen.get(yg) for yg in confirmed_yougov]
            nielsen_brands = [n for n in nielsen_brands if n]  # Filter out None

            logger.info(f"[ExternalRunId {external_run_id}] Confirmed: YouGov={yougov_brands}, Nielsen={nielsen_brands}")

            # Extract customer historical spend from competitorSnapshot (set during Stage 1)
            brand_info = (ai_run.competitorSnapshot or {}).get("brand_info")
            customer_historical_spend, historical_spend_warning = await _resolve_customer_historical_spend(
                session, brand_info, external_run_id
            )

            # =================================================================
            # Stage 2: AI Allocation Generation
            # =================================================================
            await _update_ai_run_status(session, ai_run, "generating", stage="S2", progress_pct=40)

            debug_dir = _ensure_artifact_debug_dir(external_run_id)
            stage1_ai_calls = 0

            llm_client = OpenAIClient()
            prompt_service = PromptAssemblyService(session)

            # Build prompt
            total_budget = Decimal(str(inputs.total_budget)) if inputs.total_budget else None

            # Inform Stage 2 prompt if the user changed media channels for this run
            additional_context = inputs.goal_text
            if (ai_run.rawPayload or {}).get("media_channels_changed"):
                channels_note = "Note: The user has updated the media channel selection for this run. The provided channel list reflects the user's current choice."
                additional_context = f"{additional_context}\n\n{channels_note}" if additional_context else channels_note
                logger.info(f"[ExternalRunId {external_run_id}] media_channels_changed=True - adding channel update note to Stage 2 prompt")

            # Extract net allocation preferences from chat (fail-open: None on error/empty)
            chat_prefs = await extract_chat_preferences(
                messages=(ai_run.chatSnapshot or {}).get("messages", []),
                campaign_context={
                    "customer_name": inputs.customer_name,
                    "brand_kpi": inputs.brand_kpi,
                    "channels": inputs.media_channels,
                    "goal_mode": inputs.direction,
                },
                external_run_id=external_run_id,
                since_timestamp=previous_completed_at,
            )

            # previous_shares (channel -> share_pct of the previous result) is passed in
            # by the caller, captured before allocationResult was cleared
            chat_prefs_text = build_preference_prompt_text(chat_prefs, previous_shares)

            prompt_input = PromptAssemblyInput(
                customer_name=inputs.customer_name,
                industry=inputs.industry,
                brand_kpi=inputs.brand_kpi,
                total_budget=total_budget,
                time_period_start=None,
                time_period_end=None,
                channels=inputs.media_channels,
                nielsen_brands=nielsen_brands[:5],
                yougov_brands=yougov_brands[:5],
                additional_context=additional_context,
                goal_direction=inputs.direction,
                goal_text=inputs.goal_text,
                customer_historical_spend=customer_historical_spend,
                chat_preferences=chat_prefs_text,
            )

            assembled_prompt = await prompt_service.assemble_prompt(
                input_params=prompt_input,
                wirtschaftsgruppe=inputs.industry,
            )

            # Save Stage 2 prompt artifact
            with open(f"{debug_dir}/06_prompt.txt", "w", encoding="utf-8") as f:
                f.write("=== SYSTEM PROMPT ===\n")
                f.write(assembled_prompt.system_prompt)
                f.write("\n\n=== USER PROMPT ===\n")
                f.write(assembled_prompt.user_prompt)
                f.write("\n\n=== METADATA ===\n")
                f.write(json.dumps(assembled_prompt.metadata, indent=2, default=str))

            logger.info(f"[ExternalRunId {external_run_id}] Calling OpenAI...")

            llm_response = await llm_client.generate(
                system_prompt=assembled_prompt.system_prompt,
                user_prompt=assembled_prompt.user_prompt,
                temperature=0.7,
                max_tokens=4096,
                json_mode=True,
            )

            logger.info(f"[ExternalRunId {external_run_id}] OpenAI response: {llm_response.total_tokens} tokens")

            with open(f"{debug_dir}/06_llm_response.txt", "w", encoding="utf-8") as f:
                f.write("=== RAW LLM RESPONSE ===\n")
                f.write(f"Model: {llm_response.model}\n")
                f.write(f"Total Tokens: {llm_response.total_tokens}\n")
                f.write(f"Prompt Tokens: {llm_response.prompt_tokens}\n")
                f.write(f"Completion Tokens: {llm_response.completion_tokens}\n")
                f.write("\n=== CONTENT ===\n")
                f.write(llm_response.content)

            # =================================================================
            # Stage 3: Parse Response
            # =================================================================
            await _update_ai_run_status(session, ai_run, "parsing", stage="S3", progress_pct=70)

            try:
                parsed_allocation = json.loads(llm_response.content)
            except json.JSONDecodeError as e:
                logger.error(f"[ExternalRunId {external_run_id}] Failed to parse LLM response: {e}")
                error_msg = "Could not process AI response. Please try running again."
                _persist_trace_snapshot(ai_run, stage1_ai_calls, 1, False)
                await _update_ai_run_status(session, ai_run, "failed", error=error_msg)
                await _insert_error_card(prisma_ai_run_id, external_run_id, error_msg)
                return

            with open(f"{debug_dir}/07_parsed_response.json", "w", encoding="utf-8") as f:
                json.dump(parsed_allocation, f, indent=2, ensure_ascii=False)

            # Get user-selected channels for validation
            user_channels = inputs.media_channels or []
            normalized_user_channels_set = _normalized_user_channel_set(user_channels)

            # =================================================================
            # Validation & Retry Logic
            # =================================================================
            parsed_allocation, validation_warnings, did_retry = await _validate_allocation_with_retry(
                llm_client,
                assembled_prompt,
                parsed_allocation,
                normalized_user_channels_set,
                external_run_id,
                debug_dir=debug_dir,
            )
            stage2_ai_calls = 2 if did_retry else 1

            # Build allocation result (same logic as main pipeline)
            allocations = []
            channels_data = parsed_allocation.get("channels", parsed_allocation.get("allocations", []))

            allowed_nielsen_channels = get_allowed_nielsen_channels(user_channels)

            total_budget_val = inputs.total_budget
            if not total_budget_val:
                llm_total = parsed_allocation.get("totalBudgetEur", parsed_allocation.get("total_budget_eur"))
                if llm_total:
                    total_budget_val = float(llm_total)

            for channel in channels_data:
                nielsen_channel = channel.get("name", channel.get("channel", "Unknown"))

                # Normalize channel name to UI name FIRST (handles all German/English variants)
                ui_channel = _normalize_channel_name(nielsen_channel)

                # Filter: Only include channels the user selected (compare normalized UI names)
                if normalized_user_channels_set and ui_channel not in normalized_user_channels_set:
                    logger.debug(f"[ExternalRunId {external_run_id}] Skipping channel {nielsen_channel} -> {ui_channel} - not in user selection {normalized_user_channels_set}")
                    continue

                share_pct = float(
                    channel.get("percentage") or
                    channel.get("share_pct") or
                    channel.get("sharePct") or
                    0
                )

                budget_value = (
                    channel.get("amount") or
                    channel.get("budget") or
                    channel.get("budgetGrossEur") or
                    channel.get("budget_gross_eur") or
                    None
                )

                if budget_value:
                    budget_gross_eur = float(budget_value)
                elif total_budget_val and share_pct > 0:
                    budget_gross_eur = round(total_budget_val * share_pct / 100, 2)
                else:
                    budget_gross_eur = None

                allocations.append({
                    "channel": ui_channel,
                    "share_pct": share_pct,
                    "budget_gross_eur": budget_gross_eur,
                    "reasoning": channel.get("rationale", channel.get("reasoning", "")),
                })

            # Post-processing: deduplicate, normalize, add missing channels
            channel_map = {}
            for a in allocations:
                channel_name = a["channel"]
                if channel_name in channel_map:
                    channel_map[channel_name]["share_pct"] += a["share_pct"]
                    if a["budget_gross_eur"] and channel_map[channel_name]["budget_gross_eur"]:
                        channel_map[channel_name]["budget_gross_eur"] += a["budget_gross_eur"]
                    channel_map[channel_name]["reasoning"] += f" {a['reasoning']}"
                else:
                    channel_map[channel_name] = a.copy()

            allocations = list(channel_map.values())

            # Compare against normalized user channel set (same as validation)
            normalized_user_channels = normalized_user_channels_set

            # Normalize shares to 100%
            total_share = sum(a["share_pct"] for a in allocations)
            if total_share > 0 and abs(total_share - 100.0) > 0.01:
                scale_factor = 100.0 / total_share
                for a in allocations:
                    a["share_pct"] = round(a["share_pct"] * scale_factor, 2)
                    if total_budget_val and a["share_pct"] > 0:
                        a["budget_gross_eur"] = round(total_budget_val * a["share_pct"] / 100, 2)

            # Check for missing channels - do NOT fabricate allocations
            # Instead, add warnings explaining why they were excluded
            allocated_channels = {a["channel"] for a in allocations}
            missing_channels = normalized_user_channels - allocated_channels

            if missing_channels:
                logger.warning(f"[ExternalRunId {external_run_id}] Channels excluded (no AI allocation): {missing_channels}")
                for missing_ch in missing_channels:
                    validation_warnings.append({
                        "color": "yellow",
                        "title": "Channel Excluded",
                        "description": f"{missing_ch} was not allocated by AI — insufficient benchmark data for this channel.",
                    })

                # Re-normalize remaining allocations to 100% (don't add missing channels)
                total_share = sum(a["share_pct"] for a in allocations)
                if total_share > 0 and abs(total_share - 100.0) > 0.01:
                    logger.info(f"[ExternalRunId {external_run_id}] Normalizing {total_share}% to 100% after excluding missing channels")
                    scale_factor = 100.0 / total_share
                    for a in allocations:
                        a["share_pct"] = round(a["share_pct"] * scale_factor, 2)
                        if total_budget_val and a["share_pct"] > 0:
                            a["budget_gross_eur"] = round(total_budget_val * a["share_pct"] / 100, 2)

            # Deterministically enforce explicit "+/-X%" channel requests from chat:
            # target = previous run share + delta_pp, other channels rebalanced proportionally
            if chat_prefs and chat_prefs.channel_adjustments:
                apply_channel_adjustments(
                    allocations=allocations,
                    adjustments=chat_prefs.channel_adjustments,
                    previous_shares=previous_shares,
                    external_run_id=external_run_id,
                )

            # =================================================================
            # Stage 4: Store Results
            # =================================================================
            await _update_ai_run_status(session, ai_run, "completing", stage="S4", progress_pct=90)

            if not total_budget_val:
                budget_sum = sum(a["budget_gross_eur"] or 0 for a in allocations)
                if budget_sum > 0:
                    total_budget_val = budget_sum

            # Recalculate ALL budgets from final share_pct - LLM-provided amounts can be
            # inconsistent with shares, and share post-processing above doesn't always
            # recalculate budgets (e.g., missing-channel scaling with shares still at 100%)
            if total_budget_val and allocations:
                for a in allocations:
                    a["budget_gross_eur"] = round(float(total_budget_val) * a["share_pct"] / 100, 2)
                # Force exact match: put any rounding residue on the largest channel
                residue = round(float(total_budget_val) - sum(a["budget_gross_eur"] for a in allocations), 2)
                if residue:
                    largest = max(allocations, key=lambda a: a["budget_gross_eur"])
                    largest["budget_gross_eur"] = round(largest["budget_gross_eur"] + residue, 2)

            # Extract kpi_projection from LLM response
            kpi_projection_raw = parsed_allocation.get("kpi_projection", parsed_allocation.get("kpiProjection"))
            kpi_projection = None
            if kpi_projection_raw is not None:
                try:
                    kpi_projection = float(kpi_projection_raw)
                except (TypeError, ValueError):
                    logger.warning(f"[ExternalRunId {external_run_id}] Could not parse kpi_projection: {kpi_projection_raw}")
                    kpi_projection = None
                    validation_warnings.append({
                        "color": "yellow",
                        "title": "KPI Projection Invalid",
                        "description": "AI returned invalid KPI projection value. Defaulting to 0.",
                    })

            # If LLM didn't return kpi_projection after retry, default to 0 with warning
            if kpi_projection is None:
                logger.warning(f"[ExternalRunId {external_run_id}] LLM did not return kpi_projection, defaulting to 0.0")
                kpi_projection = 0.0
                validation_warnings.append({
                    "color": "yellow",
                    "title": "KPI Projection Unavailable",
                    "description": "AI could not estimate KPI impact. Defaulting to 0 (no projected change).",
                })

            # Extract confidence_score from LLM response
            confidence_raw = parsed_allocation.get("confidence", parsed_allocation.get("confidence_score"))
            confidence_score = None
            if confidence_raw is not None:
                try:
                    confidence_score = float(confidence_raw)
                except (TypeError, ValueError):
                    logger.warning(f"[ExternalRunId {external_run_id}] Could not parse confidence: {confidence_raw}")
                    confidence_score = None
                    validation_warnings.append({
                        "color": "yellow",
                        "title": "Confidence Score Invalid",
                        "description": "AI returned invalid confidence value. Defaulting to 0.",
                    })

            # If LLM didn't return confidence after retry, default to 0 with warning
            if confidence_score is None:
                logger.warning(f"[ExternalRunId {external_run_id}] LLM did not return confidence, defaulting to 0.0")
                confidence_score = 0.0
                validation_warnings.append({
                    "color": "yellow",
                    "title": "Confidence Score Unavailable",
                    "description": "AI could not assess confidence level. Defaulting to 0 (low confidence).",
                })

            # Build structured warnings from context
            # Get competitor info from snapshot (Stage 2-4 pipeline doesn't have stage1_result)
            snapshot_competitors = (ai_run.competitorSnapshot or {}).get("competitors", [])
            excluded_competitors = [
                c.get("yougov_brand_label") for c in snapshot_competitors
                if not c.get("has_nielsen_data") and c.get("yougov_brand_label")
            ]
            competitors_with_data = [c for c in snapshot_competitors if c.get("has_nielsen_data")]

            structured_warnings = build_warnings_from_context(
                parsed_allocation=parsed_allocation,
                total_budget=float(total_budget_val) if total_budget_val else None,
                competitor_data=competitors_with_data,
                historical_spend=customer_historical_spend,  # Now extracted from snapshot
                excluded_competitors=excluded_competitors,
            )

            # Add validation warnings (from retry logic, missing channels, etc.)
            structured_warnings.extend(validation_warnings)

            # Add historical spend warning if applicable
            if historical_spend_warning:
                structured_warnings.append(historical_spend_warning)

            # Check guardrail violations (warnings only, values not modified)
            guardrail_warnings = _check_guardrail_violations(allocations, external_run_id)
            structured_warnings.extend(guardrail_warnings)

            allocation_result = {
                "run_id": external_run_id,
                "allocations": allocations,
                "total_budget_eur": total_budget_val,
                "kpi_projection": kpi_projection,
                "reasoning_summary": parsed_allocation.get("summary", parsed_allocation.get("reasoning_summary", "")),
                "confidence_score": confidence_score,  # Use extracted/defaulted value, not raw
                "warnings": structured_warnings,
                "is_cached": False,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }

            with open(f"{debug_dir}/08_final_result.json", "w", encoding="utf-8") as f:
                json.dump(allocation_result, f, indent=2, ensure_ascii=False)
            logger.info(f"[ExternalRunId {external_run_id}] Artifact files saved to {debug_dir}/")
            _create_artifact_zip(external_run_id, debug_dir)

            # Store result
            ai_run.allocationResult = allocation_result
            ai_run.status = "completed"
            ai_run.completedAt = datetime.utcnow()
            ai_run.updatedAt = datetime.utcnow()
            ai_run.progressPct = 100
            ai_run.stage = None
            _persist_trace_snapshot(ai_run, stage1_ai_calls, stage2_ai_calls, did_retry)
            flag_modified(ai_run, 'allocationResult')  # JSONB column needs explicit flag

            await session.commit()

            # Insert feedback cards into chatSnapshot using FRESH session
            # This is completely isolated from the pipeline ORM state
            logger.info(f"[ExternalRunId {external_run_id}] CALLING _insert_feedback_cards with ai_run.id={ai_run.id}")
            await _insert_feedback_cards(ai_run.id, external_run_id, allocation_result)
            logger.info(f"[ExternalRunId {external_run_id}] _insert_feedback_cards RETURNED")

            logger.info(f"[ExternalRunId {external_run_id}] Stage 2-4 pipeline completed successfully")

        except Exception as e:
            logger.error(f"[ExternalRunId {external_run_id}] Stage 2-4 pipeline failed: {str(e)}", exc_info=True)
            human_error = humanize_error(str(e))
            try:
                await session.rollback()
                query = select(PrismaProjectVersionAiRun).where(
                    PrismaProjectVersionAiRun.id == prisma_ai_run_id
                )
                result = await session.execute(query)
                ai_run = result.scalar_one_or_none()
                if ai_run:
                    await _update_ai_run_status(session, ai_run, "failed", error=human_error)
            except Exception as e2:
                logger.error(f"Failed to update status after error: {e2}")

            # Insert run_failed card into chatSnapshot (uses separate session)
            await _insert_error_card(prisma_ai_run_id, external_run_id, human_error)


# =============================================================================
# API Endpoints
# =============================================================================

@router.post(
    "",
    response_model=StartRunResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Run started successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        404: {"model": ErrorResponse, "description": "ProjectVersionAiRun not found"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
@limiter.limit("20/hour")
async def create_run(
    request: Request,
    run_request: StartRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> StartRunResponse:
    """Start a new generation run.

    Request body: {run_id, action: "start"}

    The run_id is the externalRunId from ProjectVersionAiRun (set by JS Backend).

    This endpoint:
    1. Looks up ProjectVersionAiRun by externalRunId
    2. Gets ProjectVersion and extracts campaign inputs
    3. Starts the full Stage 1-4 pipeline in background
    4. Stores results in ProjectVersionAiRun.allocationResult

    After starting:
    1. Poll GET /runs/{run_id}/status until "completed"
    2. GET /runs/{run_id}/result for allocation
    """
    settings = get_settings()

    external_run_id = run_request.run_id

    # Look up ProjectVersionAiRun by externalRunId
    ai_run = await get_ai_run_by_external_id(db, external_run_id)

    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProjectVersionAiRun with externalRunId {external_run_id} not found",
        )

    # Get ProjectVersion for campaign inputs
    project_version = await get_project_version(db, ai_run.projectVersionId)

    if not project_version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProjectVersion {ai_run.projectVersionId} not found",
        )

    # Extract campaign inputs
    inputs = extract_campaign_inputs(project_version)

    # Determine if Stage 1 can be skipped
    # Priority: skip_competitor_fetch > definition_changed > auto-detection
    if run_request.skip_competitor_fetch:
        # Highest priority: frontend explicitly wants to skip Stage 1 and keep existing competitors
        can_skip_stage1 = True
        skip_reason = "skip_competitor_fetch flag set by frontend"
        logger.info(f"[ExternalRunId {external_run_id}] Skipping Stage 1 — skip_competitor_fetch flag set by frontend")
    elif run_request.definition_changed is not None:
        # Frontend explicitly told us via definition_changed
        can_skip_stage1 = not run_request.definition_changed
        skip_reason = "frontend flag" if can_skip_stage1 else "frontend flag (definition_changed=true)"
    else:
        # Fallback to auto-detection
        can_skip_stage1 = should_skip_stage1(inputs, ai_run)
        skip_reason = "auto-detection" if can_skip_stage1 else "auto-detection (inputs changed)"

    # media_channels_changed NEVER affects the Stage 1 skip decision - channel
    # changes don't require a Stage 1 rerun. Logged for traceability and passed
    # through to Stage 2 (via rawPayload) so the prompt knows channels changed.
    if run_request.media_channels_changed is not None:
        logger.info(
            f"[ExternalRunId {external_run_id}] media_channels_changed="
            f"{run_request.media_channels_changed} (informational only - does not affect Stage 1 skip)"
        )

    if can_skip_stage1:
        # Stage 1 SKIP: Only preference fields changed
        # Preserve: competitorSnapshot, confirmedCompetitors, chatSnapshot
        # Clear: allocationResult
        logger.info(f"[ExternalRunId {external_run_id}] Skipping Stage 1 - {skip_reason}")

        # Capture previous shares BEFORE clearing allocationResult - they are the
        # baseline for explicit "+/-X%" channel adjustments from chat
        previous_shares = {
            a.get("channel"): a.get("share_pct")
            for a in ((ai_run.allocationResult or {}).get("allocations") or [])
            if a.get("channel") and a.get("share_pct") is not None
        }
        # Capture previous completion time - chat preferences stated before it
        # were already applied to the previous result and must not re-apply
        previous_completed_at = (
            ai_run.completedAt.isoformat() if ai_run.completedAt else None
        )

        ai_run.status = "pending"
        ai_run.progressPct = 0
        ai_run.stage = None
        ai_run.errorMessage = None
        ai_run.allocationResult = None  # Clear old result
        # Preserve: competitorSnapshot, confirmedCompetitors, chatSnapshot
        ai_run.updatedAt = datetime.utcnow()

        # Save current inputs for future skip detection
        save_inputs_to_raw_payload(ai_run, inputs)
        ai_run.rawPayload["media_channels_changed"] = bool(run_request.media_channels_changed)
        flag_modified(ai_run, "rawPayload")
        await db.commit()

        # Start Stage 2-4 only pipeline in background
        background_tasks.add_task(
            _run_stages_2_to_4_pipeline,
            prisma_ai_run_id=ai_run.id,
            external_run_id=external_run_id,
            previous_shares=previous_shares,
            previous_completed_at=previous_completed_at,
        )

        logger.info(f"Run started (Stage 2-4 only) for externalRunId={external_run_id}")

    else:
        # Stage 1 REQUIRED: Customer/industry/competitors changed
        # Clear: allocationResult, competitorSnapshot, confirmedCompetitors
        # Preserve: chatSnapshot
        logger.info(f"[ExternalRunId {external_run_id}] Running full Stage 1-4 pipeline - {skip_reason}")

        ai_run.status = "pending"
        ai_run.progressPct = 0
        ai_run.stage = None
        ai_run.errorMessage = None
        ai_run.allocationResult = None  # Clear old result
        ai_run.competitorSnapshot = None  # Clear - will be regenerated
        ai_run.confirmedCompetitors = None  # Clear - will need re-confirmation
        # Preserve: chatSnapshot
        ai_run.updatedAt = datetime.utcnow()

        # Save current inputs for future skip detection
        save_inputs_to_raw_payload(ai_run, inputs)
        ai_run.rawPayload["media_channels_changed"] = bool(run_request.media_channels_changed)
        flag_modified(ai_run, "rawPayload")
        await db.commit()

        # Start full pipeline in background
        background_tasks.add_task(
            run_full_pipeline_background,
            external_run_id=external_run_id,
            prisma_ai_run_id=ai_run.id,
        )

        logger.info(f"Run started (full Stage 1-4) for externalRunId={external_run_id}")

    return StartRunResponse(
        run_id=external_run_id,
        status="started",
        error_message=None
    )


@router.get(
    "/{run_id}/status",
    response_model=RunStatusResponse,
    responses={
        200: {"description": "Run status retrieved"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def get_run_status(
    request: Request,
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> RunStatusResponse:
    """Get the status of a generation run.

    Use this endpoint to poll for run completion.
    The run_id is the externalRunId from ProjectVersionAiRun.

    Status progression:
    - pending → matching (S1) → generating (S2) → parsing (S3) → completing (S4) → completed
    - Any stage can transition to failed
    """
    ai_run = await get_ai_run_by_external_id(db, run_id)

    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with externalRunId {run_id} not found",
        )

    # Map status to our RunStatus enum
    status_map = {
        "pending": RunStatus.PENDING,
        "matching": RunStatus.MATCHING,
        "awaiting_confirmation": RunStatus.AWAITING_CONFIRMATION,
        "generating": RunStatus.GENERATING,
        "parsing": RunStatus.PARSING,
        "completing": RunStatus.FEEDBACK,
        "completed": RunStatus.COMPLETED,
        "failed": RunStatus.FAILED,
        "cancelled": RunStatus.CANCELLED,
    }

    # Generate human-readable progress message
    progress_messages = {
        "pending": "Queued for processing",
        "matching": "Finding competitor brands...",
        "awaiting_confirmation": "Waiting for competitor confirmation...",
        "generating": "Generating allocation with AI...",
        "parsing": "Processing results...",
        "completing": "Finalizing results...",
        "completed": "Completed",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }

    # Determine the effective status - handle None/missing status
    effective_status = ai_run.status or "pending"
    mapped_status = status_map.get(effective_status, RunStatus.PENDING)
    progress_message = progress_messages.get(effective_status, "Processing...")

    return RunStatusResponse(
        id=run_id,
        status=mapped_status,
        stage=ai_run.stage,
        progress_pct=ai_run.progressPct or 0,
        started_at=ai_run.startedAt,
        completed_at=ai_run.completedAt,
        error_message=ai_run.errorMessage,
        progress=progress_message,
    )


@router.get(
    "/{run_id}/result",
    responses={
        200: {"description": "Allocation result retrieved"},
        404: {"model": ErrorResponse, "description": "Run not found"},
        400: {"model": ErrorResponse, "description": "Run not completed"},
    },
)
async def get_run_result(
    request: Request,
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get the allocation result for a completed run.

    The run_id is the externalRunId from ProjectVersionAiRun.
    Returns the allocationResult JSON stored in ProjectVersionAiRun.
    """
    ai_run = await get_ai_run_by_external_id(db, run_id)

    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with externalRunId {run_id} not found",
        )

    if ai_run.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run is not completed. Current status: {ai_run.status}",
        )

    if not ai_run.allocationResult:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No allocation result found for this run",
        )

    return ai_run.allocationResult


# Debug bundle mapping: n -> list of files in bundle
DEBUG_BUNDLE_MAP = {
    1: [  # Stage 1 Data Discovery
        "01_industry_resolution.json",
        "02_brand_competitors.json",
        "03_yougov_filter.json",
        "04_nielsen_filter.json",
        "Y1_prompt.txt",
        "Y2_prompt.txt",
        "N1_prompt.txt",
    ],
    2: [  # Filtered Data and LLM Input
        "05_filtered_data.json",
        "06_prompt.txt",
        "06_llm_response.txt",
    ],
    3: [  # Allocation Output
        "07_parsed_response.json",
        "08_final_result.json",
    ],
}

# Files that indicate Stage 1 was run (for n=1 and n=2 bundles)
STAGE1_INDICATOR_FILES = ["01_industry_resolution.json", "05_filtered_data.json"]

ARTIFACT_META = {
    1: {"name": "stage1_discovery", "label": "Stage 1 Data Discovery"},
    2: {"name": "filtered_data_llm", "label": "Filtered Data & LLM Input"},
    3: {"name": "allocation_output", "label": "Allocation Output"},
}

ARTIFACT_INDICATOR_FILES = {
    1: "01_industry_resolution.json",
    2: "05_filtered_data.json",
    3: "08_final_result.json",
}

IN_PROGRESS_RUN_STATUSES = {
    "pending",
    "matching",
    "awaiting_confirmation",
    "generating",
    "parsing",
    "completing",
}

_RUN_STATUS_MAP = {
    "pending": RunStatus.PENDING,
    "matching": RunStatus.MATCHING,
    "awaiting_confirmation": RunStatus.AWAITING_CONFIRMATION,
    "generating": RunStatus.GENERATING,
    "parsing": RunStatus.PARSING,
    "completing": RunStatus.FEEDBACK,
    "completed": RunStatus.COMPLETED,
    "failed": RunStatus.FAILED,
    "cancelled": RunStatus.CANCELLED,
}


def _list_artifact_files(run_id: int) -> set[str]:
    """List artifact filenames from the run directory and/or ZIP archive."""
    files: set[str] = set()
    run_dir = _artifact_run_dir(run_id)
    if os.path.isdir(run_dir):
        files.update(os.listdir(run_dir))
    zip_path = _artifact_zip_path(run_id)
    if os.path.exists(zip_path):
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                files.update(z.namelist())
        except zipfile.BadZipFile:
            pass
    return files


def _stage1_ran(available_files: set[str]) -> bool:
    return "01_industry_resolution.json" in available_files


def _compute_run_duration_seconds(ai_run: PrismaProjectVersionAiRun) -> Optional[float]:
    if not ai_run.startedAt:
        return None
    end = ai_run.completedAt
    effective_status = ai_run.status or "pending"
    if not end and effective_status in ("failed", "cancelled", "completed"):
        end = ai_run.updatedAt
    elif not end and effective_status not in ("pending",):
        end = datetime.utcnow()
    if not end:
        return None
    return (end - ai_run.startedAt).total_seconds()


def _build_artifact_status(
    run_id: int,
    artifact_number: int,
    run_status: str,
    available_files: set[str],
    stage1_ran: bool,
) -> RunArtifactStatus:
    meta = ARTIFACT_META[artifact_number]
    expected_files = DEBUG_BUNDLE_MAP[artifact_number]
    present_files = [f for f in expected_files if f in available_files]
    missing_files = [f for f in expected_files if f not in available_files]
    files_found = len(present_files)
    files_expected = len(expected_files)
    indicator = ARTIFACT_INDICATOR_FILES[artifact_number]
    download_url = f"/api/v1/runs/{run_id}/debug-zip?n={artifact_number}"

    if run_status in IN_PROGRESS_RUN_STATUSES and not present_files:
        status = "pending"
        message = (
            "Run is still in progress. Artifacts will become available when each stage completes."
        )
        download_available = False
    elif artifact_number in (1, 2) and not stage1_ran:
        status = "unavailable"
        message = "Not available — Stage 1 was skipped for this run."
        download_available = False
    elif artifact_number == 3 and indicator not in available_files and "07_parsed_response.json" not in available_files:
        if run_status == "failed":
            status = "unavailable"
            message = "Not available — run did not produce allocation output."
        elif run_status in IN_PROGRESS_RUN_STATUSES:
            status = "pending"
            message = "Allocation output is not ready yet."
        else:
            status = "unavailable"
            message = "Allocation output files are not available for this run."
        download_available = False
    elif files_found == files_expected:
        status = "available"
        if artifact_number == 1:
            message = "All Stage 1 files are ready to download."
        elif artifact_number == 2:
            message = "All Stage 2 input files are ready to download."
        else:
            message = "Allocation output is ready to download."
        download_available = True
    elif indicator in available_files or present_files:
        status = "partial"
        if run_status == "failed":
            if artifact_number == 1:
                message = "Run failed before all Stage 1 files were generated."
            elif artifact_number == 2:
                message = "Run failed before all Stage 2 input files were generated."
            else:
                message = "Run failed before all allocation output files were generated."
        else:
            message = "Some files are available; download will include partial content."
        download_available = True
    else:
        status = "unavailable"
        if artifact_number in (1, 2):
            message = "Not available — Stage 1 did not complete successfully."
        else:
            message = "Not available — run did not produce allocation output."
        download_available = False

    file_statuses = [
        RunArtifactFileStatus(filename=f, present=f in available_files)
        for f in expected_files
    ]

    return RunArtifactStatus(
        artifact_number=artifact_number,
        name=meta["name"],
        label=meta["label"],
        status=status,
        files_found=files_found,
        files_expected=files_expected,
        missing_files=missing_files,
        message=message,
        download_available=download_available,
        download_url=download_url,
        files=file_statuses,
    )


@router.get(
    "/{run_id}/artifacts",
    response_model=RunArtifactsResponse,
    responses={
        200: {"description": "Run artifacts and metrics retrieved"},
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def get_run_artifacts(
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get run metrics and artifact availability for the user-facing artifacts panel.

    The run_id is the externalRunId from ProjectVersionAiRun.
    """
    ai_run = await get_ai_run_by_external_id(db, run_id)
    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with externalRunId {run_id} not found",
        )

    effective_status = ai_run.status or "pending"
    mapped_status = _RUN_STATUS_MAP.get(effective_status, RunStatus.PENDING)
    available_files = _list_artifact_files(run_id)
    stage1_ran = _stage1_ran(available_files)

    trace = ai_run.traceSnapshot or {}
    llm_calls_count = trace.get("llm_calls_count")
    llm_breakdown = None
    if trace:
        llm_breakdown = {
            "stage1_ai_calls": trace.get("stage1_ai_calls"),
            "stage2_ai_calls": trace.get("stage2_ai_calls"),
            "stage2_retry": trace.get("stage2_retry"),
        }

    artifacts = [
        _build_artifact_status(run_id, n, effective_status, available_files, stage1_ran)
        for n in (1, 2, 3)
    ]

    return RunArtifactsResponse(
        run_id=run_id,
        run_status=mapped_status,
        started_at=ai_run.startedAt,
        completed_at=ai_run.completedAt,
        duration_seconds=_compute_run_duration_seconds(ai_run),
        llm_calls_count=llm_calls_count,
        llm_calls_breakdown=llm_breakdown,
        error_message=ai_run.errorMessage,
        artifacts=artifacts,
    )


@router.get(
    "/{run_id}/debug-zip",
    responses={
        200: {"description": "Debug bundle ZIP", "content": {"application/zip": {}}},
        400: {"model": ErrorResponse, "description": "Missing or invalid n parameter"},
        404: {"model": ErrorResponse, "description": "Debug bundle not found"},
    },
)
async def get_debug_bundle(
    request: Request,
    run_id: int,
    n: Optional[int] = Query(None, description="Bundle number 1-3"),
):
    """Get a debug bundle ZIP for a run.

    The run_id is the externalRunId from ProjectVersionAiRun.
    The n parameter specifies which bundle to return:
      1 = Stage 1 Data Discovery (industry resolution, brand/competitors, filters, prompts)
      2 = Filtered Data & LLM Input (filtered data, Stage 2 prompt, LLM response)
      3 = Allocation Output (parsed response, final result)

    Bundles 1-2 only exist for runs that executed Stage 1 (full pipeline).
    Bundle 3 exists for all completed runs.
    Debug files are only created when STAGE1_DEBUG_MODE=True.
    """
    import io

    # Validate n parameter
    if n is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Parameter n is required. Must be 1, 2, or 3.",
        )

    if n not in DEBUG_BUNDLE_MAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid n value. Must be 1, 2, or 3.",
        )

    bundle_files = DEBUG_BUNDLE_MAP[n]
    collected_files: dict[str, bytes] = {}

    run_dir = f"debug_output/run_{run_id}"
    zip_path = f"debug_output/run_{run_id}.zip"

    # Collect files from run directory or ZIP
    for filename in bundle_files:
        content = None
        file_path = f"{run_dir}/{filename}"

        # First try run directory (in-progress runs)
        if os.path.exists(file_path):
            with open(file_path, "rb") as f:
                content = f.read()
        # Then try inside the ZIP (completed runs)
        elif os.path.exists(zip_path):
            try:
                with zipfile.ZipFile(zip_path, "r") as z:
                    if filename in z.namelist():
                        content = z.read(filename)
            except (zipfile.BadZipFile, KeyError):
                pass

        if content is not None:
            collected_files[filename] = content

    # Check if we have any files
    if not collected_files:
        if n in (1, 2):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bundle n={n} not available for this run — Stage 1 was skipped.",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bundle n={n} not found. Debug mode may be disabled or run hasn't completed.",
            )

    # For bundle 1 and 2, check if Stage 1 files exist (indicator that Stage 1 ran)
    if n == 1:
        if "01_industry_resolution.json" not in collected_files:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bundle n={n} not available for this run — Stage 1 was skipped.",
            )
    elif n == 2:
        if "05_filtered_data.json" not in collected_files:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bundle n={n} not available for this run — Stage 1 was skipped.",
            )

    # Create in-memory ZIP with collected files
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in collected_files.items():
            zf.writestr(filename, content)
    zip_buffer.seek(0)

    bundle_names = {1: "stage1_discovery", 2: "filtered_data_llm", 3: "allocation_output"}
    zip_filename = f"run_{run_id}_{bundle_names[n]}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_filename}"},
    )
