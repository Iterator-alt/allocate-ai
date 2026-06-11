"""Chat preference extraction for Stage 2 prompt enrichment.

Before Stage 2 prompt assembly, reads the run's chatSnapshot messages and
extracts the user's net cumulative allocation preferences and goal adjustments
(e.g., "TV +10%, Radio +10%" from a sequence of chat messages).

Two kinds of output:
- Free-text preferences/constraints -> appended to the Stage 2 prompt
- Explicit numeric channel adjustments (e.g. "increase TV by 5%") -> enforced
  deterministically after the LLM allocation via apply_channel_adjustments(),
  using the previous run's shares as the baseline

Fail-open: any error returns None and the pipeline proceeds without chat context.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from src.services.llm_gateway.client import OpenAIClient
from src.services.stage1.debug_output import _save_debug_file

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"
MAX_MESSAGES = 40

PREFERENCE_EXTRACTION_SYSTEM_PROMPT = """You are a preference extractor for a media budget allocation tool.

You will receive a chat transcript between a user and an assistant about a media budget allocation. Your job is to extract the user's NET CUMULATIVE allocation preferences and goal adjustments, to be applied to the next allocation run.

NET CUMULATIVE INTENT:
Later messages override or adjust earlier ones. Combine them into the final net preference.
Example: "increase TV by 10%" then "increase Radio by 20%" then "reduce Radio by 10%" yields a net of: TV +10pp, Radio +10pp.

EXTRACT (when expressed by the USER):
- Explicit numeric channel share adjustments: "increase TV by 10%", "reduce Radio by 5 percent" -> these go into channel_adjustments as NET percentage-point deltas
- Non-numeric channel weighting preferences: "more of", "less of", "prioritize", "de-emphasize" specific channels -> these go into preferences (free text only)
- Strategic constraints: e.g. "don't over-invest in Print", "keep Digital strong"
- Goal text adjustments or instructions: e.g. "the goal should focus more on younger audiences"

CHANNEL NAMES:
For channel_adjustments, use EXACTLY the channel names from the campaign context channel list (match the user's wording to the closest campaign channel).

DO NOT EXTRACT:
- Total budget amounts or changes (e.g., "change budget to 2 million")
- KPI metric choice or changes (e.g., "use aided awareness instead")
- Adding or removing channels from the channel selection
- Competitor additions/removals or changes
- Questions, clarifications, small talk, confirmations
- Anything stated only by the assistant, not the user

Respond in JSON format:
{
    "has_preferences": true|false,
    "preferences": ["free-text preference or constraint", "..."],
    "channel_adjustments": [
        {"channel": "TV", "delta_pp": 10.0},
        {"channel": "Radio", "delta_pp": -5.0}
    ],
    "summary": "one-sentence summary of the net preferences"
}

channel_adjustments must contain ONLY explicit numeric share requests (net cumulative). Do not duplicate them as free text in preferences.
If the chat contains no relevant preferences or goal adjustments, return:
{"has_preferences": false, "preferences": [], "channel_adjustments": [], "summary": ""}"""


@dataclass
class ChatPreferences:
    """Extracted net preferences from chat."""

    preferences: list[str] = field(default_factory=list)   # free-text preferences/constraints
    channel_adjustments: list[dict] = field(default_factory=list)  # [{"channel": "TV", "delta_pp": 5.0}]
    summary: str = ""


async def extract_chat_preferences(
    messages: list[dict],
    campaign_context: dict,
    external_run_id: int,
    since_timestamp: Optional[str] = None,
) -> Optional[ChatPreferences]:
    """Extract net cumulative allocation preferences from chat messages.

    Args:
        messages: chatSnapshot["messages"] list
        campaign_context: customer_name, brand_kpi, channels, goal_mode
        external_run_id: External run ID (for logging/debug output)
        since_timestamp: ISO timestamp (naive UTC) of the previous run's
            completion. Messages created at or before it are excluded, so
            preferences already applied by a previous rerun are not
            re-applied. None = consider all messages.

    Returns:
        ChatPreferences with free-text preferences and explicit numeric channel
        adjustments, or None if no preferences found, chat is empty, or
        extraction fails (fail-open).
    """
    try:
        # Filter to user/agent messages (exclude system cards), keep last N.
        # Only consider messages newer than the previous completed run -
        # earlier preferences were already applied to that run's result.
        relevant = [
            m for m in (messages or [])
            if m.get("role") in ("user", "agent")
            and (
                since_timestamp is None
                or not m.get("created_at")
                or str(m["created_at"]) > since_timestamp
            )
        ][-MAX_MESSAGES:]

        user_count = sum(1 for m in relevant if m.get("role") == "user")
        if user_count == 0:
            logger.info(
                f"[ExternalRunId {external_run_id}] No user chat messages - skipping preference extraction"
            )
            return None

        # Build transcript
        transcript_lines = []
        for m in relevant:
            role = "User" if m.get("role") == "user" else "Assistant"
            content = m.get("content") or ""
            transcript_lines.append(f"{role}: {content}")
        transcript = "\n".join(transcript_lines)

        context_lines = []
        for key, label in [
            ("customer_name", "Customer"),
            ("brand_kpi", "KPI"),
            ("channels", "Channels"),
            ("goal_mode", "Goal mode"),
        ]:
            value = campaign_context.get(key)
            if value:
                if isinstance(value, list):
                    value = ", ".join(str(v) for v in value)
                context_lines.append(f"- {label}: {value}")
        context_block = "\n".join(context_lines)

        user_prompt = f"""Campaign context:
{context_block}

Chat transcript:
{transcript}

Extract the user's net cumulative allocation preferences and goal adjustments."""

        llm_client = OpenAIClient(model=MODEL)
        response = await llm_client.generate(
            system_prompt=PREFERENCE_EXTRACTION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=600,
            json_mode=True,
        )

        parsed = response.parsed_json
        if not parsed:
            logger.warning(
                f"[ExternalRunId {external_run_id}] Failed to parse preference extraction response"
            )
            return None

        has_preferences = bool(parsed.get("has_preferences"))
        preferences = [str(p) for p in (parsed.get("preferences") or [])]
        summary = str(parsed.get("summary") or "")

        # Validate channel adjustments
        channel_adjustments = []
        for adj in (parsed.get("channel_adjustments") or []):
            channel = str(adj.get("channel") or "").strip()
            try:
                delta_pp = float(adj.get("delta_pp"))
            except (TypeError, ValueError):
                continue
            if channel and delta_pp != 0:
                channel_adjustments.append({"channel": channel, "delta_pp": delta_pp})

        result: Optional[ChatPreferences] = None
        if has_preferences and (preferences or channel_adjustments):
            result = ChatPreferences(
                preferences=preferences,
                channel_adjustments=channel_adjustments,
                summary=summary,
            )

        _save_debug_file(str(external_run_id), "S2_chat_preferences", {
            "total_messages": len(messages or []),
            "since_timestamp": since_timestamp,
            "relevant_messages": len(relevant),
            "user_messages": user_count,
            "raw_llm_response": parsed,
            "has_preferences": has_preferences,
            "preferences": preferences,
            "channel_adjustments": channel_adjustments,
        })

        if result:
            logger.info(
                f"[ExternalRunId {external_run_id}] Extracted chat preferences: "
                f"{len(preferences)} free-text, {len(channel_adjustments)} explicit adjustment(s)"
            )
        else:
            logger.info(
                f"[ExternalRunId {external_run_id}] No allocation preferences found in chat"
            )

        return result

    except Exception as e:
        logger.warning(
            f"[ExternalRunId {external_run_id}] Chat preference extraction failed (proceeding without): {e}"
        )
        return None


def build_preference_prompt_text(
    prefs: Optional[ChatPreferences],
    previous_shares: Optional[dict],
) -> Optional[str]:
    """Build the prompt text for the Stage 2 'User Preferences from Chat' section.

    Includes free-text preferences and, for explicit numeric adjustments,
    the computed target shares relative to the previous run so the LLM
    rationale matches the enforced result.
    """
    if not prefs:
        return None

    lines = [f"- {p}" for p in prefs.preferences]

    if prefs.channel_adjustments:
        prev_lower = {str(k).lower(): v for k, v in (previous_shares or {}).items()}
        target_lines = []
        for adj in prefs.channel_adjustments:
            channel = adj["channel"]
            delta = adj["delta_pp"]
            prev = prev_lower.get(channel.lower())
            if prev is not None:
                target = max(0.0, min(100.0, float(prev) + delta))
                target_lines.append(
                    f"- {channel}: previous run share {prev}%, user requested {delta:+g}pp -> "
                    f"the new {channel} share MUST be {round(target, 2)}%"
                )
            else:
                target_lines.append(
                    f"- {channel}: user requested {delta:+g}pp vs the previous allocation"
                )
        if target_lines:
            lines.append(
                "\nExplicit share adjustments requested by the user (relative to the previous run). "
                "These are mandatory - rebalance the other channels accordingly:"
            )
            lines.extend(target_lines)

    if not lines:
        return None

    text = "\n".join(lines)
    if prefs.summary:
        text = f"{text}\n\nSummary: {prefs.summary}"
    return text


def apply_channel_adjustments(
    allocations: list[dict],
    adjustments: list[dict],
    previous_shares: Optional[dict],
    external_run_id: int,
) -> None:
    """Deterministically enforce explicit channel share adjustments.

    For each adjustment, target = previous run share + delta_pp (falls back to
    the current LLM share if no previous share exists). Adjusted channels are
    pinned to their targets; all other channels are scaled proportionally so
    shares sum to exactly 100. Mutates `allocations` in place (share_pct only -
    budgets are recalculated by the reconciliation pass afterwards).

    Fail-open: skips silently (with a warning) if targets are infeasible.
    """
    try:
        if not allocations or not adjustments:
            return

        by_channel = {str(a["channel"]).lower(): a for a in allocations}
        prev_lower = {str(k).lower(): v for k, v in (previous_shares or {}).items()}

        targets: dict[str, float] = {}
        applied_info = []
        for adj in adjustments:
            channel = str(adj.get("channel") or "").strip()
            try:
                delta = float(adj.get("delta_pp"))
            except (TypeError, ValueError):
                continue
            key = channel.lower()
            if key not in by_channel:
                logger.warning(
                    f"[ExternalRunId {external_run_id}] Adjustment channel '{channel}' not in allocations - skipping"
                )
                continue
            base = prev_lower.get(key)
            base_source = "previous_run"
            if base is None:
                base = by_channel[key]["share_pct"]
                base_source = "current_llm"
            target = max(0.0, min(100.0, float(base) + delta))
            targets[key] = target
            applied_info.append({
                "channel": by_channel[key]["channel"],
                "delta_pp": delta,
                "base_share": float(base),
                "base_source": base_source,
                "target_share": round(target, 2),
            })

        if not targets:
            return

        others = [a for a in allocations if str(a["channel"]).lower() not in targets]
        fixed_total = sum(targets.values())
        if fixed_total >= 100 or not others:
            logger.warning(
                f"[ExternalRunId {external_run_id}] Adjustment targets infeasible "
                f"(fixed total {fixed_total}%, {len(others)} other channels) - skipping enforcement"
            )
            return

        # Pin adjusted channels, scale the rest proportionally into the remainder
        remaining = 100.0 - fixed_total
        others_total = sum(a["share_pct"] for a in others)
        for a in others:
            if others_total > 0:
                a["share_pct"] = round(a["share_pct"] / others_total * remaining, 2)
            else:
                a["share_pct"] = round(remaining / len(others), 2)
        for key, target in targets.items():
            by_channel[key]["share_pct"] = round(target, 2)

        # Put rounding residue on the largest non-adjusted channel
        residue = round(100.0 - sum(a["share_pct"] for a in allocations), 2)
        if residue:
            largest_other = max(others, key=lambda a: a["share_pct"])
            largest_other["share_pct"] = round(largest_other["share_pct"] + residue, 2)

        logger.info(
            f"[ExternalRunId {external_run_id}] Enforced {len(targets)} explicit channel adjustment(s): "
            + ", ".join(f"{i['channel']} {i['base_share']}%{i['delta_pp']:+g}pp -> {i['target_share']}%" for i in applied_info)
        )

        _save_debug_file(str(external_run_id), "S2_chat_adjustments_applied", {
            "adjustments": applied_info,
            "final_allocations": [
                {"channel": a["channel"], "share_pct": a["share_pct"]} for a in allocations
            ],
        })

    except Exception as e:
        logger.warning(
            f"[ExternalRunId {external_run_id}] Failed to apply channel adjustments (proceeding without): {e}"
        )
