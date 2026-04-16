"""FastAPI app factory for the control panel.

Construction is a factory (create_app) so tests can inject a fake
supervisor, a tmp project root, and a tmp Store. The real entry point
(__main__.py) constructs the production versions.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from project0.control_panel import routes
from project0.control_panel.rendering import build_templates
from project0.control_panel.supervisor import MAASSupervisor
from project0.store import Store

log = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).parent
_STATIC_DIR = _PACKAGE_DIR / "static"


def create_app(
    *,
    supervisor: MAASSupervisor,
    store: Store,
    project_root: Path,
) -> FastAPI:

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if supervisor.state == "running":
            log.info("panel shutting down — stopping MAAS child process")
            await supervisor.stop()

    app = FastAPI(
        title="MAAS Control Panel",
        description="Single-user control panel for Project 0 / MAAS.",
        lifespan=lifespan,
    )
    app.state.supervisor = supervisor
    app.state.store = store
    app.state.project_root = project_root
    app.state.templates = build_templates()
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(routes.router)
    return app
