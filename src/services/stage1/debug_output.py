"""Stage 1 Debug Output Module.

When STAGE1_DEBUG_MODE is True, saves intermediate results to /debug_output
for inspection and sharing with stakeholders.

Each step result is saved as a separate JSON file with timestamp and run_id.
When DEBUG_MODE is False, all functions are no-ops with zero performance impact.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import asdict, is_dataclass

from src.config import get_settings


# Global flag - controlled by STAGE1_DEBUG_MODE env var
def is_debug_mode() -> bool:
    """Check if Stage 1 debug mode is enabled."""
    return get_settings().stage1_debug_mode


def _get_debug_dir() -> Path:
    """Get or create the debug output directory."""
    # Use project root / debug_output
    debug_dir = Path(__file__).parent.parent.parent.parent / "debug_output"
    debug_dir.mkdir(exist_ok=True)
    return debug_dir


def _serialize_value(obj: Any) -> Any:
    """Serialize a value to JSON-compatible format."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if hasattr(obj, 'isoformat'):  # date objects
        return obj.isoformat()
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize_value(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _serialize_value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_value(item) for item in obj]
    if hasattr(obj, 'value'):  # Enum
        return obj.value
    return str(obj)


def _save_debug_file(
    run_id: str,
    step_name: str,
    data: Dict[str, Any],
    timestamp: Optional[datetime] = None,
    filename_override: Optional[str] = None,
) -> Optional[str]:
    """Save debug data to a JSON file.

    Args:
        run_id: Unique identifier for this run
        step_name: Name of the step (e.g., "Y1_industry_resolution")
        data: Dictionary of data to save
        timestamp: Optional timestamp (defaults to now)
        filename_override: If provided, use this as the filename instead of
            generating one from timestamp + step_name

    Returns:
        Path to saved file, or None if debug mode is off and not a production artifact
    """
    # Production artifact filenames (01_*.json … 05_*.json) are always saved
    if not is_debug_mode() and not filename_override:
        return None

    timestamp = timestamp or datetime.now()

    debug_dir = _get_debug_dir()

    # Create run-specific subdirectory
    run_dir = debug_dir / f"run_{run_id}"
    run_dir.mkdir(exist_ok=True)

    # Build filename - use override if provided, otherwise generate with timestamp
    if filename_override:
        filename = filename_override
    else:
        timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp_str}_{step_name}.json"
    filepath = run_dir / filename

    # Serialize and save
    serialized = _serialize_value(data)
    serialized["_meta"] = {
        "run_id": run_id,
        "step": step_name,
        "timestamp": timestamp.isoformat(),
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(serialized, f, ensure_ascii=False, indent=2)

    return str(filepath)


class Stage1DebugLogger:
    """Debug logger for Stage 1 pipeline.

    Captures intermediate results at each step when DEBUG_MODE is True.
    All methods are no-ops when DEBUG_MODE is False.
    """

    def __init__(self, run_id: str):
        """Initialize debug logger for a specific run.

        Args:
            run_id: Unique identifier for this run (e.g., from Run.id)
        """
        self.run_id = str(run_id)
        self.start_time = datetime.now()
        self.enabled = True  # Production run artifacts are always saved
        self._saved_files: List[str] = []

    def log_step_y1_industry_resolution(
        self,
        user_industry: str,
        available_yougov_sectors: List[str],
        available_nielsen_sectors: List[str],
        matched_yougov_sectors: List[str],
        matched_nielsen_sectors: List[str],
        confidence: float,
        reasoning: Optional[str] = None,
    ) -> Optional[str]:
        """Log Step Y1: Industry Resolution results.

        After Y1: which sector_labels were matched from the industry input
        """
        if not self.enabled:
            return None

        data = {
            "step": "Y1_Industry_Resolution",
            "description": "Map user industry input to YouGov sector_label and Nielsen Wirtschaftsgruppe",
            "input": {
                "user_industry": user_industry,
            },
            "available_options": {
                "yougov_sectors_count": len(available_yougov_sectors),
                "yougov_sectors": available_yougov_sectors[:50],  # First 50 for reference
                "nielsen_sectors_count": len(available_nielsen_sectors),
                "nielsen_sectors": available_nielsen_sectors[:50],
            },
            "output": {
                "matched_yougov_sectors": matched_yougov_sectors,
                "matched_nielsen_sectors": matched_nielsen_sectors,
                "confidence": confidence,
                "reasoning": reasoning,
            },
        }

        filepath = _save_debug_file(
            self.run_id, "Y1_industry_resolution", data, self.start_time,
            filename_override="01_industry_resolution.json"
        )
        if filepath:
            self._saved_files.append(filepath)
        return filepath

    def log_step_y2_brand_and_competitors(
        self,
        user_brand: str,
        yougov_sectors: List[str],
        available_brands: List[str],
        matched_customer_brand: Optional[str],
        brand_match_confidence: float,
        brand_match_reasoning: Optional[str],
        suggested_competitors: List[str],
        competitor_suggestion_reasoning: Optional[str],
    ) -> Optional[str]:
        """Log Step Y2: Brand matching and competitor suggestion.

        After Y2: full brand list from YouGov, which brand was matched as customer,
        which competitors were suggested by LLM
        """
        if not self.enabled:
            return None

        data = {
            "step": "Y2_Brand_And_Competitors",
            "description": "Match customer brand and suggest direct competitors via LLM",
            "input": {
                "user_brand": user_brand,
                "yougov_sectors": yougov_sectors,
            },
            "available_brands": {
                "count": len(available_brands),
                "brands": available_brands,  # Full list for debugging
            },
            "customer_brand_match": {
                "matched_brand": matched_customer_brand,
                "confidence": brand_match_confidence,
                "reasoning": brand_match_reasoning,
            },
            "competitor_suggestion": {
                "suggested_competitors": suggested_competitors,
                "reasoning": competitor_suggestion_reasoning,
            },
        }

        filepath = _save_debug_file(
            self.run_id, "Y2_brand_and_competitors", data, self.start_time,
            filename_override="02_brand_competitors.json"
        )
        if filepath:
            self._saved_files.append(filepath)
        return filepath

    def log_step_y3_yougov_filtered_data(
        self,
        customer_brand: str,
        competitors: List[str],
        selected_kpi: str,
        yougov_data: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Log Step Y3: Filtered YouGov data slice.

        After Y3: filtered YouGov slice (date, brand_label, metric, score) for approved brands
        """
        if not self.enabled:
            return None

        all_brands = [customer_brand] + competitors

        data = {
            "step": "Y3_YouGov_Filtered_Data",
            "description": "Filtered YouGov KPI data for customer and competitors",
            "filter_criteria": {
                "customer_brand": customer_brand,
                "competitors": competitors,
                "all_brands": all_brands,
                "selected_kpi": selected_kpi,
            },
            "filtered_data": {
                "row_count": len(yougov_data),
                "data": yougov_data,  # Full data for inspection
            },
            "summary": {
                "brands_with_data": list(set(row.get("brand_label") for row in yougov_data if row.get("brand_label"))),
                "date_range": {
                    "min": min((row.get("date") for row in yougov_data if row.get("date")), default=None),
                    "max": max((row.get("date") for row in yougov_data if row.get("date")), default=None),
                },
            },
        }

        filepath = _save_debug_file(
            self.run_id, "Y3_yougov_filtered_data", data, self.start_time,
            filename_override="03_yougov_filter.json"
        )
        if filepath:
            self._saved_files.append(filepath)
        return filepath

    def log_step_n1_nielsen_produktmarke_filtering(
        self,
        brand_produktmarke_details: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Log Step N1: Nielsen Produktmarke filtering for all brands.

        After N1: for each brand — which Marke was matched in Nielsen,
        full Produktmarke list, which were marked relevant vs excluded by LLM

        Args:
            brand_produktmarke_details: List of dicts, each containing:
                - brand_label: YouGov brand name
                - nielsen_marke: Matched Nielsen Marke
                - all_produktmarke: Full list of Produktmarke
                - relevant_produktmarke: LLM-selected relevant ones
                - excluded_produktmarke: LLM-excluded ones
                - reasoning: Dict of Produktmarke -> reason
        """
        if not self.enabled:
            return None

        data = {
            "step": "N1_Nielsen_Produktmarke_Filtering",
            "description": "For each brand: Nielsen Marke match and Produktmarke filtering by LLM",
            "brands": brand_produktmarke_details,
            "summary": {
                "total_brands": len(brand_produktmarke_details),
                "brands_with_nielsen_match": sum(
                    1 for b in brand_produktmarke_details
                    if b.get("nielsen_match_found")
                ),
                "brands_without_nielsen_match": sum(
                    1 for b in brand_produktmarke_details
                    if not b.get("nielsen_match_found")
                ),
                "brands_with_produktmarke_filtering": sum(
                    1 for b in brand_produktmarke_details
                    if b.get("produktmarke_filtering_applied")
                ),
            },
        }

        filepath = _save_debug_file(
            self.run_id, "N1_nielsen_produktmarke_filtering", data, self.start_time,
            filename_override="04_nielsen_filter.json"
        )
        if filepath:
            self._saved_files.append(filepath)
        return filepath

    def log_final_filtered_data(
        self,
        customer_brand: str,
        competitors: List[str],
        yougov_slice: List[Dict[str, Any]],
        nielsen_slice: List[Dict[str, Any]],
        competitor_data: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Log final output: Complete T | Filtered Data for Stage 2.

        Final output: the complete T | Filtered Data that gets passed to Stage 2
        """
        if not self.enabled:
            return None

        data = {
            "step": "FINAL_Filtered_Data_For_Stage2",
            "description": "Complete filtered dataset (T | Filtered Data) passed to Stage 2",
            "brands_included": {
                "customer": customer_brand,
                "competitors": competitors,
            },
            "yougov_slice": {
                "description": "date, brand_label, metric, score per brand per month",
                "row_count": len(yougov_slice),
                "data": yougov_slice,
            },
            "nielsen_slice": {
                "description": "date, brand, Mediengruppe, TEuro per channel per brand per month",
                "row_count": len(nielsen_slice),
                "data": nielsen_slice,
            },
            "competitor_details": {
                "description": "Detailed YouGov and Nielsen data per competitor",
                "competitors": competitor_data,
            },
            "summary": {
                "yougov_brands": list(set(row.get("brand_label") for row in yougov_slice if row.get("brand_label"))),
                "nielsen_brands": list(set(row.get("marke") or row.get("brand") for row in nielsen_slice if row.get("marke") or row.get("brand"))),
                "total_yougov_rows": len(yougov_slice),
                "total_nielsen_rows": len(nielsen_slice),
            },
        }

        filepath = _save_debug_file(
            self.run_id, "FINAL_filtered_data", data, self.start_time,
            filename_override="05_filtered_data.json"
        )
        if filepath:
            self._saved_files.append(filepath)
        return filepath

    def log_error(
        self,
        step: str,
        error: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Log an error that occurred during processing."""
        if not self.enabled:
            return None

        data = {
            "step": f"ERROR_{step}",
            "error": error,
            "context": context or {},
        }

        filepath = _save_debug_file(self.run_id, f"ERROR_{step}", data, self.start_time)
        if filepath:
            self._saved_files.append(filepath)
        return filepath

    def get_saved_files(self) -> List[str]:
        """Get list of all saved debug files for this run."""
        return self._saved_files.copy()

    def get_run_directory(self) -> Optional[str]:
        """Get the debug output directory for this run."""
        if not self.enabled:
            return None
        return str(_get_debug_dir() / f"run_{self.run_id}")

    def save_y1_prompt(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """Save Y1 (Industry Resolution) AI prompt."""
        if not self.enabled:
            return None
        run_dir = _get_debug_dir() / f"run_{self.run_id}"
        run_dir.mkdir(exist_ok=True)
        filepath = run_dir / "Y1_prompt.txt"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("=== SYSTEM PROMPT ===\n")
            f.write(system_prompt)
            f.write("\n\n=== USER PROMPT ===\n")
            f.write(user_prompt)
        self._saved_files.append(str(filepath))
        return str(filepath)

    def save_y2_prompt(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """Save Y2 (Brand and Competitor Suggestion) AI prompt."""
        if not self.enabled:
            return None
        run_dir = _get_debug_dir() / f"run_{self.run_id}"
        run_dir.mkdir(exist_ok=True)
        filepath = run_dir / "Y2_prompt.txt"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("=== SYSTEM PROMPT ===\n")
            f.write(system_prompt)
            f.write("\n\n=== USER PROMPT ===\n")
            f.write(user_prompt)
        self._saved_files.append(str(filepath))
        return str(filepath)

    def save_n1_prompt(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """Save N1 (Produktmarke Filtering) AI prompt."""
        if not self.enabled:
            return None
        run_dir = _get_debug_dir() / f"run_{self.run_id}"
        run_dir.mkdir(exist_ok=True)
        filepath = run_dir / "N1_prompt.txt"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("=== SYSTEM PROMPT ===\n")
            f.write(system_prompt)
            f.write("\n\n=== USER PROMPT ===\n")
            f.write(user_prompt)
        self._saved_files.append(str(filepath))
        return str(filepath)
