"""Expected failures of the chat feature (ADR 0021). Raised before streaming starts."""

from __future__ import annotations

from nexus_api.core.errors import AppError


class ConversationNotFoundError(AppError):
    status_code = 404
    code = "conversation_not_found"


class ConversationBusyAppError(AppError):
    status_code = 409
    code = "conversation_busy"


class LlmNotConfiguredError(AppError):
    status_code = 503
    code = "llm_not_configured"


class ChatNotReadyError(AppError):
    status_code = 503
    code = "chat_not_ready"


class LlmBusyError(AppError):
    status_code = 429
    code = "llm_busy"


class LlmBudgetExceededError(AppError):
    status_code = 429
    code = "llm_budget_exceeded"
