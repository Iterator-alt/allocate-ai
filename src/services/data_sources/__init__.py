"""Data source abstraction layer.

Provides a unified interface for accessing YouGov and Nielsen data,
whether from PostgreSQL (current) or future API integrations.
"""

from src.services.data_sources.base import DataSourceInterface
from src.services.data_sources.database import DatabaseDataSource

__all__ = [
    "DataSourceInterface",
    "DatabaseDataSource",
]
