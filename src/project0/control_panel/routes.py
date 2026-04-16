"""HTTP routes for the control panel.

Each route group (home, profile, facts, toml, personas, env, usage) lives
in this one file for now. If it grows past ~400 lines it can be split by
concern. Responses are always HTML pages or redirects — never JSON.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from project0.control_panel.writes import atomic_write_text

router = APIRouter()


def _ctx(request: Request, **extra: object) -> dict[str, object]:
    sup = request.app.state.supervisor
    base: dict[str, object] = {
        "maas_state": sup.state,
        "maas_pid": sup.pid,
        "maas_last_exit_code": sup.last_exit_code,
    }
    base.update(extra)
    return base


@router.get("/")
async def home(request: Request) -> object:
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "home.html", _ctx(request))


@router.post("/maas/start")
async def maas_start(request: Request) -> RedirectResponse:
    await request.app.state.supervisor.start()
    return RedirectResponse(url="/", status_code=303)


@router.post("/maas/stop")
async def maas_stop(request: Request) -> RedirectResponse:
    await request.app.state.supervisor.stop()
    return RedirectResponse(url="/", status_code=303)


@router.post("/maas/restart")
async def maas_restart(request: Request) -> RedirectResponse:
    await request.app.state.supervisor.restart()
    return RedirectResponse(url="/", status_code=303)


@router.get("/profile")
async def profile_get(request: Request) -> object:
    templates = request.app.state.templates
    path = request.app.state.project_root / "data" / "user_profile.yaml"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return templates.TemplateResponse(request, "profile.html", _ctx(request, content=content))


@router.post("/profile")
async def profile_post(
    request: Request,
    content: str = Form(...),
) -> RedirectResponse:
    path = request.app.state.project_root / "data" / "user_profile.yaml"
    atomic_write_text(path, content)
    return RedirectResponse(url="/profile", status_code=303)
