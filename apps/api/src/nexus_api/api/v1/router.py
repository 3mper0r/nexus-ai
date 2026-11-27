from __future__ import annotations

from fastapi import APIRouter

from nexus_api.api.v1 import documents, health, search

API_V1_PREFIX = "/api/v1"

router = APIRouter(prefix=API_V1_PREFIX)
router.include_router(health.router)
router.include_router(documents.router)
router.include_router(search.router)
