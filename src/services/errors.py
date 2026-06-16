"""Human-readable error messages for run failures.

Converts raw Python exceptions to clear, actionable messages for users.
"""


def humanize_error(error: str) -> str:
    """Convert exception string to human-readable error message.

    Args:
        error: Raw exception string (from str(e))

    Returns:
        Human-readable error message for display to user
    """
    error_lower = error.lower()

    # Timeout errors
    if "timeout" in error_lower or "timed out" in error_lower:
        return "AI service timed out. Please try running again."

    # Rate limiting
    if "rate limit" in error_lower or "429" in error_lower or "too many requests" in error_lower:
        return "AI service is busy. Please wait a moment and try again."

    # Brand/data not found
    if "brand" in error_lower and "not found" in error_lower:
        return "Could not find brand in database. Check the customer name and try again."

    if "no competitor" in error_lower or "competitors not found" in error_lower:
        return "Could not find competitor data. Try different competitors or industry settings."

    if "industry" in error_lower and ("not found" in error_lower or "could not resolve" in error_lower):
        return "Could not resolve industry category. Try a different industry description."

    # JSON/parsing errors
    if "json" in error_lower or "parse" in error_lower or "decode" in error_lower:
        return "Could not process AI response. Please try running again."

    # Database errors
    if "database" in error_lower or "connection" in error_lower or "sqlalchemy" in error_lower:
        return "Database error occurred. Please try again."

    if "integrity" in error_lower or "constraint" in error_lower:
        return "Data validation error. Please check your inputs and try again."

    # OpenAI/API errors
    if "openai" in error_lower or "api" in error_lower:
        if "key" in error_lower or "auth" in error_lower:
            return "AI service authentication failed. Please contact support."
        return "AI service error. Please try running again."

    # Network errors
    if "network" in error_lower or "connect" in error_lower or "refused" in error_lower:
        return "Network error occurred. Please check your connection and try again."

    # Generic fallback — keep it short and actionable
    return "An unexpected error occurred. Please try again or contact support."


def get_error_title(error: str) -> str:
    """Get a short title for the error type.

    Args:
        error: Raw exception string

    Returns:
        Short title for the error (e.g., "Timeout", "Not Found")
    """
    error_lower = error.lower()

    if "timeout" in error_lower or "timed out" in error_lower:
        return "Timeout"

    if "rate limit" in error_lower or "429" in error_lower:
        return "Service Busy"

    if "not found" in error_lower:
        return "Not Found"

    if "json" in error_lower or "parse" in error_lower:
        return "Processing Error"

    if "database" in error_lower or "connection" in error_lower:
        return "Database Error"

    if "network" in error_lower or "connect" in error_lower:
        return "Network Error"

    return "Run Failed"
