"""Data Validation Service.

Validates data quality requirements:
- Minimum 12 data points required
- Data age: 2-3 years ideal, 4-5 years acceptable, >5 years rejected
- Search order: YouGov first, then Nielsen
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from enum import Enum


class DataQuality(Enum):
    """Data quality classification based on age."""
    IDEAL = "ideal"           # 2-3 years old
    ACCEPTABLE = "acceptable"  # 4-5 years old
    REJECTED = "rejected"      # >5 years old
    INSUFFICIENT = "insufficient"  # <12 data points


class DataFreshness(Enum):
    """Data freshness classification."""
    FRESH = "fresh"           # Within last 2 years
    RECENT = "recent"         # 2-3 years old
    AGING = "aging"           # 4-5 years old
    STALE = "stale"           # >5 years old


# Configuration constants
MIN_DATA_POINTS = 12          # Minimum required data points
IDEAL_MAX_AGE_YEARS = 3       # 2-3 years is ideal
ACCEPTABLE_MAX_AGE_YEARS = 5  # 4-5 years is acceptable
MAX_AGE_YEARS = 5             # >5 years is rejected


@dataclass
class DataPointInfo:
    """Information about a single data point."""
    year: int
    month: int
    value: Any
    age_years: float = 0.0

    def __post_init__(self):
        """Calculate age of data point."""
        now = datetime.now()
        data_date = datetime(self.year, self.month, 1)
        self.age_years = (now - data_date).days / 365.25


@dataclass
class DataValidationResult:
    """Result of data validation."""
    is_valid: bool
    quality: DataQuality
    freshness: DataFreshness
    total_points: int
    valid_points: int
    rejected_points: int
    oldest_year: Optional[int]
    newest_year: Optional[int]
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    data_points: List[DataPointInfo] = field(default_factory=list)


@dataclass
class BrandDataValidation:
    """Validation result for a brand's data."""
    brand_name: str
    source: str  # "yougov" or "nielsen"
    yougov_validation: Optional[DataValidationResult] = None
    nielsen_validation: Optional[DataValidationResult] = None
    has_sufficient_data: bool = False
    recommendation: str = ""


class DataValidationService:
    """Service for validating data quality and freshness.

    Rules:
    - Minimum 12 data points required
    - Data age: 2-3 years ideal, 4-5 years okay, >5 years rejected
    - Search order: YouGov FIRST, then Nielsen
    """

    def __init__(self):
        self.current_year = datetime.now().year
        self.current_month = datetime.now().month

    def get_valid_year_range(self) -> Tuple[int, int]:
        """Get the valid year range for data (last 5 years only).

        Returns:
            Tuple of (min_year, max_year) for valid data
        """
        max_year = self.current_year
        min_year = self.current_year - MAX_AGE_YEARS
        return min_year, max_year

    def calculate_data_age(self, year: int, month: int = 6) -> float:
        """Calculate age of data in years.

        Args:
            year: Data year
            month: Data month (defaults to middle of year)

        Returns:
            Age in years as float
        """
        data_date = datetime(year, month, 1)
        now = datetime.now()
        return (now - data_date).days / 365.25

    def classify_data_freshness(self, age_years: float) -> DataFreshness:
        """Classify data freshness based on age.

        Args:
            age_years: Age of data in years

        Returns:
            DataFreshness classification
        """
        if age_years <= 2:
            return DataFreshness.FRESH
        elif age_years <= IDEAL_MAX_AGE_YEARS:
            return DataFreshness.RECENT
        elif age_years <= ACCEPTABLE_MAX_AGE_YEARS:
            return DataFreshness.AGING
        else:
            return DataFreshness.STALE

    def is_data_point_valid(self, year: int, month: int = 6) -> bool:
        """Check if a data point is within valid age range.

        Args:
            year: Data year
            month: Data month

        Returns:
            True if data is within 5 years, False otherwise
        """
        age = self.calculate_data_age(year, month)
        return age <= MAX_AGE_YEARS

    def validate_time_series(
        self,
        data_points: List[Dict[str, Any]],
        min_points: int = MIN_DATA_POINTS,
    ) -> DataValidationResult:
        """Validate a time series dataset.

        Args:
            data_points: List of dicts with 'year', 'month', 'value' keys
            min_points: Minimum required data points (default 12)

        Returns:
            DataValidationResult with validation details
        """
        if not data_points:
            return DataValidationResult(
                is_valid=False,
                quality=DataQuality.INSUFFICIENT,
                freshness=DataFreshness.STALE,
                total_points=0,
                valid_points=0,
                rejected_points=0,
                oldest_year=None,
                newest_year=None,
                errors=["No data points provided"],
            )

        warnings = []
        errors = []
        valid_data_points = []
        rejected_count = 0

        # Process each data point
        for dp in data_points:
            year = dp.get("year")
            month = dp.get("month", 6)
            value = dp.get("value")

            if year is None or value is None:
                continue

            point_info = DataPointInfo(year=year, month=month, value=value)

            if self.is_data_point_valid(year, month):
                valid_data_points.append(point_info)
            else:
                rejected_count += 1

        # Sort by date (newest first for analysis)
        valid_data_points.sort(key=lambda x: (x.year, x.month), reverse=True)

        total_points = len(valid_data_points)

        # Check minimum points requirement
        if total_points < min_points:
            errors.append(
                f"Insufficient data: {total_points} points found, "
                f"minimum {min_points} required"
            )
            quality = DataQuality.INSUFFICIENT
        else:
            # Determine quality based on data age distribution
            if valid_data_points:
                avg_age = sum(p.age_years for p in valid_data_points) / len(valid_data_points)
                if avg_age <= IDEAL_MAX_AGE_YEARS:
                    quality = DataQuality.IDEAL
                elif avg_age <= ACCEPTABLE_MAX_AGE_YEARS:
                    quality = DataQuality.ACCEPTABLE
                    warnings.append(
                        f"Data is aging (avg {avg_age:.1f} years old). "
                        "Consider requesting fresher data."
                    )
                else:
                    quality = DataQuality.REJECTED
                    errors.append(
                        f"Data too old (avg {avg_age:.1f} years). "
                        "Data older than 5 years is not usable."
                    )
            else:
                quality = DataQuality.INSUFFICIENT

        # Determine freshness from newest data point
        if valid_data_points:
            newest = valid_data_points[0]
            oldest = valid_data_points[-1]
            freshness = self.classify_data_freshness(newest.age_years)
            oldest_year = oldest.year
            newest_year = newest.year
        else:
            freshness = DataFreshness.STALE
            oldest_year = None
            newest_year = None

        # Add warning if rejected points
        if rejected_count > 0:
            warnings.append(
                f"{rejected_count} data points rejected (older than {MAX_AGE_YEARS} years)"
            )

        is_valid = (
            total_points >= min_points and
            quality in [DataQuality.IDEAL, DataQuality.ACCEPTABLE]
        )

        return DataValidationResult(
            is_valid=is_valid,
            quality=quality,
            freshness=freshness,
            total_points=total_points,
            valid_points=total_points,  # All points in list are valid (rejected ones filtered)
            rejected_points=rejected_count,
            oldest_year=oldest_year,
            newest_year=newest_year,
            warnings=warnings,
            errors=errors,
            data_points=valid_data_points,
        )

    def filter_valid_data_points(
        self,
        data_points: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Filter data points to only include valid ones (within 5 years).

        Args:
            data_points: List of dicts with 'year', 'month', 'value' keys

        Returns:
            Filtered list with only valid data points
        """
        valid_points = []
        for dp in data_points:
            year = dp.get("year")
            month = dp.get("month", 6)

            if year and self.is_data_point_valid(year, month):
                valid_points.append(dp)

        # Sort by date (oldest first for time series)
        valid_points.sort(key=lambda x: (x.get("year", 0), x.get("month", 0)))

        return valid_points

    def get_recommended_year_range(self) -> Tuple[int, int]:
        """Get recommended year range for optimal data quality.

        Returns:
            Tuple of (start_year, end_year) for 2-3 year ideal range
        """
        end_year = self.current_year
        start_year = self.current_year - IDEAL_MAX_AGE_YEARS
        return start_year, end_year

    def format_validation_summary(self, result: DataValidationResult) -> str:
        """Format validation result as human-readable summary.

        Args:
            result: DataValidationResult to format

        Returns:
            Formatted string summary
        """
        lines = []

        status = "VALID" if result.is_valid else "INVALID"
        lines.append(f"Data Validation: {status}")
        lines.append(f"  Quality: {result.quality.value}")
        lines.append(f"  Freshness: {result.freshness.value}")
        lines.append(f"  Total Points: {result.total_points} (min required: {MIN_DATA_POINTS})")

        if result.oldest_year and result.newest_year:
            lines.append(f"  Date Range: {result.oldest_year} - {result.newest_year}")

        if result.rejected_points > 0:
            lines.append(f"  Rejected (>5 years old): {result.rejected_points}")

        if result.warnings:
            lines.append("  Warnings:")
            for w in result.warnings:
                lines.append(f"    - {w}")

        if result.errors:
            lines.append("  Errors:")
            for e in result.errors:
                lines.append(f"    - {e}")

        return "\n".join(lines)


# Singleton instance for easy import
data_validator = DataValidationService()
