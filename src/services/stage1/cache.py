"""Stage 1 Caching Service.

Caches DISTINCT value lists from database with 24hr TTL.
These lists are used by AI for industry and brand resolution.

Cache Keys (from design doc):
- distinct:yougov_sectors - All distinct sector_label values
- distinct:nielsen_sectors - All distinct Wirtschaftsgruppe values
- distinct:yougov_brands:{sector} - Distinct brand_label per sector
- distinct:nielsen_brands:{sector} - Distinct Marke per Wirtschaftsgruppe
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class CacheEntry:
    """A single cache entry with TTL."""
    value: Any
    expires_at: datetime

    def is_expired(self) -> bool:
        return datetime.now() > self.expires_at


class Stage1Cache:
    """In-memory cache for Stage 1 DISTINCT value lists.

    TTL: 24 hours (lists only change when new data is ingested).
    Thread-safe using asyncio locks.

    Future: Replace with Redis in Phase 2.
    """

    DEFAULT_TTL_HOURS = 24

    def __init__(self, ttl_hours: int = DEFAULT_TTL_HOURS):
        self._cache: Dict[str, CacheEntry] = {}
        self._ttl = timedelta(hours=ttl_hours)
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._cache[key]
                return None
            return entry.value

    async def set(self, key: str, value: Any, ttl_hours: Optional[int] = None) -> None:
        """Set value in cache with TTL."""
        ttl = timedelta(hours=ttl_hours) if ttl_hours else self._ttl
        async with self._lock:
            self._cache[key] = CacheEntry(
                value=value,
                expires_at=datetime.now() + ttl
            )

    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def clear(self) -> None:
        """Clear all cache entries."""
        async with self._lock:
            self._cache.clear()

    async def clear_expired(self) -> int:
        """Clear all expired entries. Returns count of removed entries."""
        async with self._lock:
            expired_keys = [
                k for k, v in self._cache.items() if v.is_expired()
            ]
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)

    # Convenience methods for Stage 1 cache keys

    @staticmethod
    def key_yougov_sectors() -> str:
        return "distinct:yougov_sectors"

    @staticmethod
    def key_nielsen_sectors() -> str:
        return "distinct:nielsen_sectors"

    @staticmethod
    def key_yougov_brands(sector: str) -> str:
        return f"distinct:yougov_brands:{sector}"

    @staticmethod
    def key_nielsen_brands(wirtschaftsgruppe: str) -> str:
        return f"distinct:nielsen_brands:{wirtschaftsgruppe}"

    async def get_yougov_sectors(self) -> Optional[List[str]]:
        return await self.get(self.key_yougov_sectors())

    async def set_yougov_sectors(self, sectors: List[str]) -> None:
        await self.set(self.key_yougov_sectors(), sectors)

    async def get_nielsen_sectors(self) -> Optional[List[str]]:
        return await self.get(self.key_nielsen_sectors())

    async def set_nielsen_sectors(self, sectors: List[str]) -> None:
        await self.set(self.key_nielsen_sectors(), sectors)

    async def get_yougov_brands(self, sector: str) -> Optional[List[str]]:
        return await self.get(self.key_yougov_brands(sector))

    async def set_yougov_brands(self, sector: str, brands: List[str]) -> None:
        await self.set(self.key_yougov_brands(sector), brands)

    async def get_nielsen_brands(self, wirtschaftsgruppe: str) -> Optional[List[str]]:
        return await self.get(self.key_nielsen_brands(wirtschaftsgruppe))

    async def set_nielsen_brands(self, wirtschaftsgruppe: str, brands: List[str]) -> None:
        await self.set(self.key_nielsen_brands(wirtschaftsgruppe), brands)


# Global cache instance
stage1_cache = Stage1Cache()
