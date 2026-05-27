"""Chat agent API endpoint - Placeholder for Prisma-only mode.

The chat agent requires Python tables which are not available in Prisma-only mode.
This is a placeholder that returns a helpful error message.
"""

import logging
from fastapi import APIRouter, HTTPException, status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat-agent"])


@router.post("/message")
async def send_chat_message():
    """Chat endpoint - not available in Prisma-only mode."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Chat agent is not available in Prisma-only mode. "
               "The system is running without Python backend tables.",
    )
