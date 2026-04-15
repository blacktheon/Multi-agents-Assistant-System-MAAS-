"""FastAPI app factory for the Intelligence webapp (6e).

Construction is deferred into a factory (`create_app(config)`) so tests can
build isolated app instances with tmp directories, and so composition-root
(`main.py`) has a single entry point to call after loading `WebConfig`."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from project0.intelligence_web import routes
from project0.intelligence_web.config import WebConfig
from project0.intelligence_web.rendering import (
    format_time,
    groupby_month,
    sort_by_importance,
)

_PACKAGE_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


def create_app(config: WebConfig) -> FastAPI:
    app = FastAPI(
        title="Intelligence Webapp",
        description="Reading surface for Intelligence daily reports (6e).",
    )

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    # Filters need the user's tz closed over
    templates.env.filters["sort_by_importance"] = sort_by_importance
    templates.env.filters["groupby_month"] = groupby_month
    templates.env.filters["format_time"] = lambda s: format_time(
        s, user_tz=config.user_tz, now=None
    )

    app.state.config = config
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(routes.router)
    return app


def _dev_factory() -> FastAPI:
    """Zero-arg factory for `uvicorn --factory` dev mode (scripts/dev_web.sh).

    Builds an app pointing at the real `data/intelligence/reports/` directory
    with dev-friendly defaults. Not used in production — `main.py` constructs
    its own `WebConfig` from `prompts/intelligence.toml`."""
    from zoneinfo import ZoneInfo

    cfg = WebConfig(
        public_base_url="http://localhost:8081",
        bind_host="127.0.0.1",
        bind_port=8081,
        reports_dir=Path("data/intelligence/reports"),
        feedback_dir=Path("data/intelligence/feedback"),
        user_tz=ZoneInfo("Asia/Shanghai"),
    )
    return create_app(cfg)
