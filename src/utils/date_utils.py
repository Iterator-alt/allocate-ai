"""Date utilities including German month name parsing."""

from typing import Optional

# German month names to month numbers
GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
    # Short forms
    "jan": 1,
    "feb": 2,
    "mär": 3,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "nov": 11,
    "dez": 12,
}

# English month names (fallback)
ENGLISH_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def parse_german_month(month_str: str) -> Optional[int]:
    """Parse a German month name to a month number (1-12).

    Args:
        month_str: Month name in German (e.g., "Januar", "März", "Dezember")

    Returns:
        Month number (1-12) or None if not recognized
    """
    if not month_str:
        return None

    normalized = month_str.lower().strip()

    # Try German months first
    if normalized in GERMAN_MONTHS:
        return GERMAN_MONTHS[normalized]

    # Try English months as fallback
    if normalized in ENGLISH_MONTHS:
        return ENGLISH_MONTHS[normalized]

    # Try numeric parsing
    try:
        month_num = int(normalized)
        if 1 <= month_num <= 12:
            return month_num
    except ValueError:
        pass

    return None


def parse_year_month(value: str) -> Optional[tuple[int, int]]:
    """Parse a combined year-month string.

    Handles formats like:
    - "2023-01"
    - "01/2023"
    - "Januar 2023"
    - "2023 Januar"

    Args:
        value: Combined year-month string

    Returns:
        Tuple of (year, month) or None if not parseable
    """
    if not value:
        return None

    value = value.strip()

    # Try ISO format: 2023-01
    if "-" in value:
        parts = value.split("-")
        if len(parts) == 2:
            try:
                if len(parts[0]) == 4:
                    year = int(parts[0])
                    month = int(parts[1])
                else:
                    month = int(parts[0])
                    year = int(parts[1])
                if 1 <= month <= 12:
                    return (year, month)
            except ValueError:
                pass

    # Try slash format: 01/2023
    if "/" in value:
        parts = value.split("/")
        if len(parts) == 2:
            try:
                if len(parts[1]) == 4:
                    month = int(parts[0])
                    year = int(parts[1])
                else:
                    year = int(parts[0])
                    month = int(parts[1])
                if 1 <= month <= 12:
                    return (year, month)
            except ValueError:
                pass

    # Try text format: "Januar 2023" or "2023 Januar"
    parts = value.split()
    if len(parts) == 2:
        for i, part in enumerate(parts):
            month = parse_german_month(part)
            if month is not None:
                other_part = parts[1 - i]
                try:
                    year = int(other_part)
                    return (year, month)
                except ValueError:
                    pass

    return None
