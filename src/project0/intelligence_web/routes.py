"""FastAPI routes for the Intelligence webapp (6e).

Kept thin — handlers read config via Depends, call into feedback/rendering
modules for real work, and delegate HTML generation to Jinja2 templates."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter()


@router.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"
