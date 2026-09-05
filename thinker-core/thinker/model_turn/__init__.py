"""Provider-neutral model-turn protocol for Thinker.

This package transports model semantics only.  Callers remain responsible for
tool authorization and execution, agent state, memory, and workspaces.
"""

from .errors import ModelTurnError, ModelTurnErrorCode
from .models import (
    Continuation,
    GenerationParameters,
    ModelMessage,
    ModelTurnRequest,
    ModelTurnResponse,
    ToolCall,
    ToolDefinition,
    ToolResult,
    Usage,
)
from .runtime import ModelTurnRuntime

__all__ = [
    "Continuation",
    "GenerationParameters",
    "ModelMessage",
    "ModelTurnError",
    "ModelTurnErrorCode",
    "ModelTurnRequest",
    "ModelTurnResponse",
    "ModelTurnRuntime",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
    "Usage",
]
