import "server-only";

import { apiRequest } from "./client";
import {
  type ConversationDetail,
  type ConversationSummary,
  isConversationDetail,
  isConversationList,
  isConversationSummary,
  isLlmStatus,
  type LlmStatus,
} from "./types";

export function listConversations() {
  return apiRequest<ConversationSummary[]>("/api/v1/conversations?limit=50", {
    guard: isConversationList,
  });
}

export function getConversation(id: string) {
  return apiRequest<ConversationDetail>(`/api/v1/conversations/${encodeURIComponent(id)}`, {
    guard: isConversationDetail,
  });
}

export function createConversation() {
  return apiRequest<ConversationSummary>("/api/v1/conversations", {
    method: "POST",
    body: {},
    guard: isConversationSummary,
    acceptStatuses: [201],
  });
}

export function deleteConversation(id: string) {
  return apiRequest<null>(`/api/v1/conversations/${encodeURIComponent(id)}`, {
    method: "DELETE",
    acceptStatuses: [204],
  });
}

export function getLlmStatus() {
  return apiRequest<LlmStatus>("/api/v1/llm/status", { guard: isLlmStatus });
}
