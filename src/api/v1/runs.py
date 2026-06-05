"""Run management API endpoints - Prisma-only version.

Endpoints:
- POST /runs - Start a new generation run (accepts {run_id, action: "start"})
- GET /runs/{id}/status - Poll run state from ProjectVersionAiRun
- GET /runs/{id}/result - Get allocation result from ProjectVersionAiRun
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

from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
from fastapi.responses import FileResponse
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
    "ZEITUNGEN": "Print",  # Newspapers -> Print
    "PUBLIKUMSZEITSCHRIFTEN": "Print",  # Magazines -> Print
    "FACHZEITSCHRIFTEN": "Trade Press",
    "AT-RETAIL-MEDIA": "Retail Media",
    "SEARCH": "Search",
    "KINO": "Cinema",
    "TRANSPORT MEDIA": "Transport",
    "AMBIENT MEDIA": "Ambient",
    "WERBESENDUNGEN": "Direct Mail",
}

# Reverse mapping: UI names → Nielsen names (for filtering)
# Note: "Print" maps to multiple Nielsen channels
UI_TO_NIELSEN_CHANNEL_MAP = {
    "TV": "FERNSEHEN",
    "Digital": "ONLINE",
    "OOH": "PLAKAT",
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
                await _update_ai_run_status(session, ai_run, "failed", error="ProjectVersion not found")
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

            if stage1_result.status == Stage1Status.FAILED:
                error_msg = "; ".join(stage1_result.errors) if stage1_result.errors else "Stage 1 failed"
                await _update_ai_run_status(session, ai_run, "failed", error=error_msg)
                logger.error(f"[ExternalRunId {external_run_id}] Stage 1 failed: {error_msg}")
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
                error_msg = "Stage 1 failed: No competitors found for the given brand and industry"
                logger.error(f"[ExternalRunId {external_run_id}] {error_msg}")
                await _update_ai_run_status(session, ai_run, "failed", error=error_msg)
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

            llm_client = OpenAIClient()
            prompt_service = PromptAssemblyService(session)

            # Build prompt
            total_budget = Decimal(str(inputs.total_budget)) if inputs.total_budget else None

            # Get customer's historical spend from Stage 1 data
            # NOTE: Despite the name 'total_spend_teuro', it's already converted to EUR in repository.py
            customer_historical_spend = None
            if stage1_result.brand_data and stage1_result.brand_data.total_spend_teuro:
                customer_historical_spend = stage1_result.brand_data.total_spend_teuro  # Already in EUR

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
                additional_context=inputs.goal_text,
                goal_direction=inputs.direction,  # Pass direction to Stage 2
                goal_text=inputs.goal_text,  # Pass goal text for Goal→Budget mode
                customer_historical_spend=customer_historical_spend,  # Customer's historical spend in EUR
            )

            assembled_prompt = await prompt_service.assemble_prompt(
                input_params=prompt_input,
                wirtschaftsgruppe=inputs.industry,
            )

            # DEBUG: Save prompt to file for debugging
            if get_settings().stage1_debug_mode:
                debug_dir = f"debug_output/run_{external_run_id}"
                os.makedirs(debug_dir, exist_ok=True)
                with open(f"{debug_dir}/S2_prompt.txt", "w", encoding="utf-8") as f:
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

            # DEBUG: Save raw LLM response
            if get_settings().stage1_debug_mode:
                debug_dir = f"debug_output/run_{external_run_id}"
                os.makedirs(debug_dir, exist_ok=True)
                with open(f"{debug_dir}/S2_llm_response.txt", "w", encoding="utf-8") as f:
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
                await _update_ai_run_status(session, ai_run, "failed", error=f"Failed to parse LLM response: {e}")
                return

            # DEBUG: Save parsed allocation (raw from LLM before post-processing)
            if get_settings().stage1_debug_mode:
                with open(f"{debug_dir}/S2_parsed_raw.json", "w", encoding="utf-8") as f:
                    json.dump(parsed_allocation, f, indent=2, ensure_ascii=False)

            # Build allocation result
            allocations = []
            channels_data = parsed_allocation.get("channels", parsed_allocation.get("allocations", []))

            # Get user-selected channels and map to allowed Nielsen channel names
            user_channels = inputs.media_channels or []
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

                # Filter: Only include channels the user selected
                if allowed_nielsen_channels and nielsen_channel.upper() not in allowed_nielsen_channels:
                    logger.debug(f"[ExternalRunId {external_run_id}] Skipping channel {nielsen_channel} - not in user selection")
                    continue

                # Map Nielsen channel name to user-facing UI name
                ui_channel = map_nielsen_channel_to_ui(nielsen_channel)

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

            # Step 2: Normalize user_channels to canonical UI names for comparison
            # This handles cases where user selected "Online" which maps to "Digital"
            normalized_user_channels = set()
            for ch in user_channels:
                # Check if user channel is a Nielsen name that should be mapped
                nielsen_upper = ch.upper()
                if nielsen_upper in NIELSEN_TO_UI_CHANNEL_MAP:
                    # User used Nielsen name, map to UI name
                    normalized_user_channels.add(NIELSEN_TO_UI_CHANNEL_MAP[nielsen_upper])
                elif ch in UI_TO_NIELSEN_CHANNEL_MAP:
                    # User used UI name, keep as-is
                    normalized_user_channels.add(ch)
                else:
                    # Unknown channel, keep original
                    normalized_user_channels.add(ch)

            logger.info(f"[ExternalRunId {external_run_id}] User channels normalized: {user_channels} -> {normalized_user_channels}")

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
            allocated_channels = {a["channel"] for a in allocations}
            missing_channels = normalized_user_channels - allocated_channels

            if missing_channels:
                logger.warning(f"[ExternalRunId {external_run_id}] Missing user-selected channels: {missing_channels}")
                # Add missing channels with minimal allocation (5% each, taken proportionally from existing)
                for missing_ch in missing_channels:
                    min_allocation = 5.0
                    # Reduce existing allocations proportionally to make room
                    reduction_factor = (100.0 - min_allocation) / 100.0 if allocations else 1.0
                    for a in allocations:
                        a["share_pct"] = round(a["share_pct"] * reduction_factor, 2)

                    # Calculate budget for missing channel
                    missing_budget = round(total_budget * min_allocation / 100, 2) if total_budget else None

                    allocations.append({
                        "channel": missing_ch,
                        "share_pct": min_allocation,
                        "budget_gross_eur": missing_budget,
                        "reasoning": f"No competitor benchmark data available for {missing_ch}. Allocated minimum 5% as part of user's selected channel mix.",
                    })

                # Re-normalize to ensure exactly 100%
                total_share = sum(a["share_pct"] for a in allocations)
                if total_share > 0 and abs(total_share - 100.0) > 0.01:
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

            # Extract kpi_projection from LLM response - MUST NOT be null
            kpi_projection_raw = parsed_allocation.get("kpi_projection", parsed_allocation.get("kpiProjection"))
            kpi_projection = None
            if kpi_projection_raw is not None:
                try:
                    kpi_projection = float(kpi_projection_raw)
                except (TypeError, ValueError):
                    logger.warning(f"[ExternalRunId {external_run_id}] Could not parse kpi_projection: {kpi_projection_raw}")
                    kpi_projection = None

            # If LLM didn't return kpi_projection, estimate based on mode
            if kpi_projection is None:
                logger.warning(f"[ExternalRunId {external_run_id}] LLM did not return kpi_projection, defaulting to 0.0")
                kpi_projection = 0.0

            allocation_result = {
                "run_id": external_run_id,
                "allocations": allocations,
                "total_budget_eur": total_budget,
                "kpi_projection": kpi_projection,
                "reasoning_summary": parsed_allocation.get("summary", parsed_allocation.get("reasoning_summary", "")),
                "confidence_score": parsed_allocation.get("confidence", parsed_allocation.get("confidence_score", 0.85)),
                "warnings": parsed_allocation.get("warnings", []),
                "is_cached": False,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }

            # DEBUG: Save final result (after post-processing)
            if get_settings().stage1_debug_mode:
                with open(f"{debug_dir}/S2_final_result.json", "w", encoding="utf-8") as f:
                    json.dump(allocation_result, f, indent=2, ensure_ascii=False)
                logger.info(f"[ExternalRunId {external_run_id}] Debug files saved to {debug_dir}/")

                # Create ZIP archive of debug files
                zip_path = f"debug_output/run_{external_run_id}.zip"
                try:
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                        for root, dirs, files in os.walk(debug_dir):
                            for file in files:
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, debug_dir)
                                zipf.write(file_path, arcname)
                    # Delete the folder after successful ZIP creation
                    shutil.rmtree(debug_dir)
                    logger.info(f"[ExternalRunId {external_run_id}] Debug ZIP created: {zip_path}")
                except Exception as zip_error:
                    logger.warning(f"[ExternalRunId {external_run_id}] Failed to create debug ZIP: {zip_error}")

            # Store in ProjectVersionAiRun
            ai_run.allocationResult = allocation_result
            ai_run.status = "completed"
            ai_run.completedAt = datetime.utcnow()
            ai_run.updatedAt = datetime.utcnow()
            ai_run.progressPct = 100
            ai_run.stage = None
            await session.commit()

            logger.info(f"[ExternalRunId {external_run_id}] Pipeline completed successfully")

        except Exception as e:
            logger.error(f"[ExternalRunId {external_run_id}] Pipeline failed: {str(e)}", exc_info=True)
            try:
                await session.rollback()
                # Try to update status to failed
                query = select(PrismaProjectVersionAiRun).where(
                    PrismaProjectVersionAiRun.id == prisma_ai_run_id
                )
                result = await session.execute(query)
                ai_run = result.scalar_one_or_none()
                if ai_run:
                    await _update_ai_run_status(session, ai_run, "failed", error=str(e))
            except Exception as e2:
                logger.error(f"Failed to update status after error: {e2}")


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
        ai_run.errorMessage = error
    if status == "matching":
        ai_run.startedAt = datetime.utcnow()

    await session.commit()


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
        } if result.confirmed_brand else None,
        "yougov_sectors": result.yougov_sectors,
        "nielsen_sectors": result.nielsen_sectors,
    }


async def _run_stages_2_to_4_pipeline(
    prisma_ai_run_id: str,
    external_run_id: int,
):
    """Run Stages 2-4 after competitor confirmation.

    This is called from the confirm endpoint after user approves competitors.
    Stage 1 data is read from competitorSnapshot in the database.
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
                await _update_ai_run_status(session, ai_run, "failed", error="ProjectVersion not found")
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

            # Customer historical spend - not available in Stage 2-4 standalone pipeline
            customer_historical_spend = None

            # =================================================================
            # Stage 2: AI Allocation Generation
            # =================================================================
            await _update_ai_run_status(session, ai_run, "generating", stage="S2", progress_pct=40)

            llm_client = OpenAIClient()
            prompt_service = PromptAssemblyService(session)

            # Build prompt
            total_budget = Decimal(str(inputs.total_budget)) if inputs.total_budget else None

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
                additional_context=inputs.goal_text,
                goal_direction=inputs.direction,
                goal_text=inputs.goal_text,
                customer_historical_spend=customer_historical_spend,
            )

            assembled_prompt = await prompt_service.assemble_prompt(
                input_params=prompt_input,
                wirtschaftsgruppe=inputs.industry,
            )

            # DEBUG: Save prompt to file for debugging
            if get_settings().stage1_debug_mode:
                debug_dir = f"debug_output/run_{external_run_id}"
                os.makedirs(debug_dir, exist_ok=True)
                with open(f"{debug_dir}/S2_prompt.txt", "w", encoding="utf-8") as f:
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

            # DEBUG: Save raw LLM response
            if get_settings().stage1_debug_mode:
                debug_dir = f"debug_output/run_{external_run_id}"
                os.makedirs(debug_dir, exist_ok=True)
                with open(f"{debug_dir}/S2_llm_response.txt", "w", encoding="utf-8") as f:
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
                await _update_ai_run_status(session, ai_run, "failed", error=f"Failed to parse LLM response: {e}")
                return

            # DEBUG: Save parsed allocation
            if get_settings().stage1_debug_mode:
                with open(f"{debug_dir}/S2_parsed_raw.json", "w", encoding="utf-8") as f:
                    json.dump(parsed_allocation, f, indent=2, ensure_ascii=False)

            # Build allocation result (same logic as main pipeline)
            allocations = []
            channels_data = parsed_allocation.get("channels", parsed_allocation.get("allocations", []))

            user_channels = inputs.media_channels or []
            allowed_nielsen_channels = get_allowed_nielsen_channels(user_channels)

            total_budget_val = inputs.total_budget
            if not total_budget_val:
                llm_total = parsed_allocation.get("totalBudgetEur", parsed_allocation.get("total_budget_eur"))
                if llm_total:
                    total_budget_val = float(llm_total)

            for channel in channels_data:
                nielsen_channel = channel.get("name", channel.get("channel", "Unknown"))

                if allowed_nielsen_channels and nielsen_channel.upper() not in allowed_nielsen_channels:
                    continue

                ui_channel = map_nielsen_channel_to_ui(nielsen_channel)

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

            # Normalize user channels
            normalized_user_channels = set()
            for ch in user_channels:
                nielsen_upper = ch.upper()
                if nielsen_upper in NIELSEN_TO_UI_CHANNEL_MAP:
                    normalized_user_channels.add(NIELSEN_TO_UI_CHANNEL_MAP[nielsen_upper])
                elif ch in UI_TO_NIELSEN_CHANNEL_MAP:
                    normalized_user_channels.add(ch)
                else:
                    normalized_user_channels.add(ch)

            # Normalize shares to 100%
            total_share = sum(a["share_pct"] for a in allocations)
            if total_share > 0 and abs(total_share - 100.0) > 0.01:
                scale_factor = 100.0 / total_share
                for a in allocations:
                    a["share_pct"] = round(a["share_pct"] * scale_factor, 2)
                    if total_budget_val and a["share_pct"] > 0:
                        a["budget_gross_eur"] = round(total_budget_val * a["share_pct"] / 100, 2)

            # Add missing channels
            allocated_channels = {a["channel"] for a in allocations}
            missing_channels = normalized_user_channels - allocated_channels

            if missing_channels:
                for missing_ch in missing_channels:
                    min_allocation = 5.0
                    reduction_factor = (100.0 - min_allocation) / 100.0 if allocations else 1.0
                    for a in allocations:
                        a["share_pct"] = round(a["share_pct"] * reduction_factor, 2)

                    missing_budget = round(total_budget_val * min_allocation / 100, 2) if total_budget_val else None

                    allocations.append({
                        "channel": missing_ch,
                        "share_pct": min_allocation,
                        "budget_gross_eur": missing_budget,
                        "reasoning": f"No competitor benchmark data available for {missing_ch}. Allocated minimum 5%.",
                    })

                # Re-normalize
                total_share = sum(a["share_pct"] for a in allocations)
                if total_share > 0 and abs(total_share - 100.0) > 0.01:
                    scale_factor = 100.0 / total_share
                    for a in allocations:
                        a["share_pct"] = round(a["share_pct"] * scale_factor, 2)
                        if total_budget_val and a["share_pct"] > 0:
                            a["budget_gross_eur"] = round(total_budget_val * a["share_pct"] / 100, 2)

            # =================================================================
            # Stage 4: Store Results
            # =================================================================
            await _update_ai_run_status(session, ai_run, "completing", stage="S4", progress_pct=90)

            if not total_budget_val:
                budget_sum = sum(a["budget_gross_eur"] or 0 for a in allocations)
                if budget_sum > 0:
                    total_budget_val = budget_sum

            kpi_projection_raw = parsed_allocation.get("kpi_projection", parsed_allocation.get("kpiProjection"))
            kpi_projection = None
            if kpi_projection_raw is not None:
                try:
                    kpi_projection = float(kpi_projection_raw)
                except (TypeError, ValueError):
                    kpi_projection = None

            if kpi_projection is None:
                kpi_projection = 0.0

            allocation_result = {
                "run_id": external_run_id,
                "allocations": allocations,
                "total_budget_eur": total_budget_val,
                "kpi_projection": kpi_projection,
                "reasoning_summary": parsed_allocation.get("summary", parsed_allocation.get("reasoning_summary", "")),
                "confidence_score": parsed_allocation.get("confidence", parsed_allocation.get("confidence_score", 0.85)),
                "warnings": parsed_allocation.get("warnings", []),
                "is_cached": False,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }

            # DEBUG: Save final result and create ZIP
            if get_settings().stage1_debug_mode:
                with open(f"{debug_dir}/S2_final_result.json", "w", encoding="utf-8") as f:
                    json.dump(allocation_result, f, indent=2, ensure_ascii=False)
                logger.info(f"[ExternalRunId {external_run_id}] Debug files saved to {debug_dir}/")

                zip_path = f"debug_output/run_{external_run_id}.zip"
                try:
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                        for root, dirs, files in os.walk(debug_dir):
                            for file in files:
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, debug_dir)
                                zipf.write(file_path, arcname)
                    shutil.rmtree(debug_dir)
                    logger.info(f"[ExternalRunId {external_run_id}] Debug ZIP created: {zip_path}")
                except Exception as zip_error:
                    logger.warning(f"[ExternalRunId {external_run_id}] Failed to create debug ZIP: {zip_error}")

            # Store result
            ai_run.allocationResult = allocation_result
            ai_run.status = "completed"
            ai_run.completedAt = datetime.utcnow()
            ai_run.updatedAt = datetime.utcnow()
            ai_run.progressPct = 100
            ai_run.stage = None
            await session.commit()

            logger.info(f"[ExternalRunId {external_run_id}] Stage 2-4 pipeline completed successfully")

        except Exception as e:
            logger.error(f"[ExternalRunId {external_run_id}] Stage 2-4 pipeline failed: {str(e)}", exc_info=True)
            try:
                await session.rollback()
                query = select(PrismaProjectVersionAiRun).where(
                    PrismaProjectVersionAiRun.id == prisma_ai_run_id
                )
                result = await session.execute(query)
                ai_run = result.scalar_one_or_none()
                if ai_run:
                    await _update_ai_run_status(session, ai_run, "failed", error=str(e))
            except Exception as e2:
                logger.error(f"Failed to update status after error: {e2}")


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
    # Priority: frontend flag > auto-detection
    if run_request.definition_changed is not None:
        # Frontend explicitly told us
        can_skip_stage1 = not run_request.definition_changed
        skip_reason = "frontend flag" if can_skip_stage1 else "frontend flag (definition_changed=true)"
    else:
        # Fallback to auto-detection
        can_skip_stage1 = should_skip_stage1(inputs, ai_run)
        skip_reason = "auto-detection" if can_skip_stage1 else "auto-detection (inputs changed)"

    if can_skip_stage1:
        # Stage 1 SKIP: Only preference fields changed
        # Preserve: competitorSnapshot, confirmedCompetitors, chatSnapshot
        # Clear: allocationResult
        logger.info(f"[ExternalRunId {external_run_id}] Skipping Stage 1 - {skip_reason}")

        ai_run.status = "pending"
        ai_run.progressPct = 0
        ai_run.stage = None
        ai_run.errorMessage = None
        ai_run.allocationResult = None  # Clear old result
        # Preserve: competitorSnapshot, confirmedCompetitors, chatSnapshot
        ai_run.updatedAt = datetime.utcnow()

        # Save current inputs for future skip detection
        save_inputs_to_raw_payload(ai_run, inputs)
        await db.commit()

        # Start Stage 2-4 only pipeline in background
        background_tasks.add_task(
            _run_stages_2_to_4_pipeline,
            prisma_ai_run_id=ai_run.id,
            external_run_id=external_run_id,
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


@router.get(
    "/{run_id}/debug-zip",
    responses={
        200: {"description": "Debug ZIP file download", "content": {"application/zip": {}}},
        404: {"model": ErrorResponse, "description": "Debug ZIP not found"},
    },
)
async def download_debug_zip(
    request: Request,
    run_id: int,
):
    """Download the debug ZIP file for a run.

    The run_id is the externalRunId from ProjectVersionAiRun.
    Returns the debug ZIP file if it exists.
    Debug files are only created when STAGE1_DEBUG_MODE=True.
    """
    zip_path = f"debug_output/run_{run_id}.zip"

    if not os.path.exists(zip_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Debug ZIP for run {run_id} not found. Debug mode may be disabled or run hasn't completed.",
        )

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=f"run_{run_id}_debug.zip",
    )
