"""Chat agent API endpoint.

Processes interactive chat messages for the allocation system.
Uses Prisma tables (ProjectVersionAiRun) for all state management.

Endpoints:
- POST /chat/message - Send a message to the chat agent
- GET /chat/{run_id}/history - Get chat history for a run
"""

import logging
from typing import Optional, List, Dict, Any, Union

from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.api.middleware import get_session_context, SessionContext
from src.db.models.prisma_tables import PrismaProjectVersionAiRun
from src.services.chat.agent import ChatAgent, AgentResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat-agent"])


# =============================================================================
# Request/Response Models
# =============================================================================

class ChatMessageRequest(BaseModel):
    """Request to send a chat message."""

    run_id: int = Field(..., description="externalRunId from ProjectVersionAiRun")
    message: str = Field(..., min_length=1, max_length=2000, description="User's message")
    project_id: Optional[Union[str, int]] = Field(None, description="Optional project ID (string or int)")
    version_id: Optional[Union[str, int]] = Field(None, description="Optional version ID (string or int)")


class ChatMessageResponse(BaseModel):
    """Response from the chat agent."""

    agent_response: str = Field(..., description="Agent's response text")
    tool_used: Optional[str] = Field(None, description="Tool that was used")
    updated_competitor_set: Optional[List[str]] = Field(None, description="Updated competitors if changed")
    updated_inputs: Optional[Dict[str, Any]] = Field(None, description="Updated inputs if changed")
    rerun_triggered: bool = Field(False, description="Whether a rerun was triggered")
    rerun_blocked_reason: Optional[str] = Field(None, description="Reason if rerun was blocked")
    chat_message_id: int = Field(0, description="ID of the saved message")
    new_run_id: Optional[int] = Field(None, description="New run ID if rerun created one")
    pending_changes: Optional[List[Dict[str, Any]]] = Field(None, description="Pending uncommitted changes")


class ChatHistoryMessage(BaseModel):
    """A single chat message.

    id is int for user/agent messages, string for system cards
    (e.g. "summary_0", "warning_0").
    """

    id: Union[int, str]
    role: str  # "user", "agent" or "system"
    content: str
    card_type: Optional[str] = None  # "allocation_summary" or "warning" for system cards
    tool_used: Optional[str] = None
    changes_made: List[Dict[str, Any]] = []
    created_at: str


class ChatHistoryResponse(BaseModel):
    """Response with chat history."""

    run_id: int
    messages: List[ChatHistoryMessage]
    total_count: int


# =============================================================================
# Helper Functions
# =============================================================================

async def get_ai_run_by_external_id(
    db: AsyncSession,
    external_run_id: int,
) -> Optional[PrismaProjectVersionAiRun]:
    """Look up ProjectVersionAiRun by externalRunId."""
    query = select(PrismaProjectVersionAiRun).where(
        PrismaProjectVersionAiRun.externalRunId == external_run_id
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


# =============================================================================
# API Endpoints
# =============================================================================

@router.post(
    "/message",
    response_model=ChatMessageResponse,
    responses={
        200: {"description": "Message processed successfully"},
        404: {"description": "Run not found"},
        400: {"description": "Invalid request"},
    },
)
async def send_chat_message(
    request: Request,
    chat_request: ChatMessageRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ChatMessageResponse:
    """Send a message to the chat agent.

    The chat agent can:
    - Add/remove competitors
    - Edit campaign inputs (budget, channels, KPI, etc.)
    - Trigger reruns when changes have been made

    The run_id is the externalRunId from ProjectVersionAiRun.
    """
    # Verify the run exists
    ai_run = await get_ai_run_by_external_id(db, chat_request.run_id)

    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with externalRunId {chat_request.run_id} not found",
        )

    # Create chat agent and process message
    agent = ChatAgent(session=db, background_tasks=background_tasks)

    try:
        result: AgentResponse = await agent.process_message(
            project_id=chat_request.project_id,
            run_id=chat_request.run_id,
            message=chat_request.message,
            version_id=chat_request.version_id,
        )

        # Commit the session to persist changes
        await db.commit()

        return ChatMessageResponse(
            agent_response=result.response_text,
            tool_used=result.tool_used,
            updated_competitor_set=result.updated_competitors,
            updated_inputs=result.updated_inputs,
            rerun_triggered=result.rerun_triggered,
            rerun_blocked_reason=result.rerun_blocked_reason,
            chat_message_id=result.chat_message_id,
            new_run_id=result.new_run_id,
            pending_changes=result.pending_changes,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error processing chat message: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing message: {str(e)}",
        )


@router.get(
    "/{run_id}/history",
    response_model=ChatHistoryResponse,
    responses={
        200: {"description": "Chat history retrieved"},
        404: {"description": "Run not found"},
    },
)
async def get_chat_history(
    request: Request,
    run_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> ChatHistoryResponse:
    """Get chat history for a run.

    The run_id is the externalRunId from ProjectVersionAiRun.
    Returns messages from the chatSnapshot JSON field.
    """
    ai_run = await get_ai_run_by_external_id(db, run_id)

    if not ai_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with externalRunId {run_id} not found",
        )

    # Extract messages from chatSnapshot
    chat_snapshot = ai_run.chatSnapshot or {"messages": []}
    messages_data = chat_snapshot.get("messages", [])

    # Convert to response format
    messages = []
    for msg in messages_data[-limit:]:
        messages.append(ChatHistoryMessage(
            id=msg.get("id", 0),
            role=msg.get("role", "system"),
            content=msg.get("content", ""),
            card_type=msg.get("card_type"),
            tool_used=msg.get("tool_used"),
            changes_made=msg.get("changes_made", []),
            created_at=msg.get("created_at", ""),
        ))

    return ChatHistoryResponse(
        run_id=run_id,
        messages=messages,
        total_count=len(messages_data),
    )
