import "server-only";

import { apiGet } from "./client";
import { isLivenessResponse, isReadinessResponse } from "./types";

export function getLiveness(requestId?: string) {
  return apiGet("/api/v1/health/live", { guard: isLivenessResponse, requestId });
}

export function getReadiness(requestId?: string) {
  // 503 is a valid readiness answer ("not ready"), not a transport failure.
  return apiGet("/api/v1/health/ready", {
    guard: isReadinessResponse,
    acceptStatuses: [200, 503],
    requestId,
  });
}
