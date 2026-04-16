"""HTTP routes for the control panel.

Each route group (home, profile, facts, toml, personas, env, usage) lives
in this one file for now. If it grows past ~400 lines it can be split by
concern. Responses are always HTML pages or redirects — never JSON.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from project0.control_panel.paths import ALLOWED_AGENT_NAMES, persona_path, toml_path
from project0.control_panel.writes import atomic_write_text
from project0.store import UserFactsReader, UserFactsWriter

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


@router.get("/facts")
async def facts_list(
    request: Request,
    show_inactive: int = 0,
) -> object:
    templates = request.app.state.templates
    store = request.app.state.store
    reader = UserFactsReader("human", store.conn)
    if show_inactive:
        facts = reader.all_including_inactive()
    else:
        facts = reader.active(limit=500)
    return templates.TemplateResponse(
        request, "facts.html",
        _ctx(request, facts=facts, show_inactive=bool(show_inactive)),
    )


@router.post("/facts")
async def facts_add(
    request: Request,
    fact_text: str = Form(...),
    topic: str = Form(""),
) -> RedirectResponse:
    store = request.app.state.store
    writer = UserFactsWriter("human", store.conn)
    writer.add(fact_text, topic=topic or None)
    return RedirectResponse(url="/facts", status_code=303)


@router.post("/facts/{fact_id}/edit")
async def facts_edit(
    request: Request,
    fact_id: int,
    fact_text: str = Form(...),
    topic: str = Form(""),
) -> RedirectResponse:
    store = request.app.state.store
    writer = UserFactsWriter("human", store.conn)
    writer.edit(fact_id, fact_text, topic or None)
    return RedirectResponse(url="/facts", status_code=303)


@router.post("/facts/{fact_id}/deactivate")
async def facts_deactivate(request: Request, fact_id: int) -> RedirectResponse:
    store = request.app.state.store
    writer = UserFactsWriter("human", store.conn)
    writer.deactivate(fact_id)
    return RedirectResponse(url="/facts", status_code=303)


@router.post("/facts/{fact_id}/reactivate")
async def facts_reactivate(request: Request, fact_id: int) -> RedirectResponse:
    store = request.app.state.store
    writer = UserFactsWriter("human", store.conn)
    writer.reactivate(fact_id)
    return RedirectResponse(url="/facts?show_inactive=1", status_code=303)


@router.post("/facts/{fact_id}/delete")
async def facts_delete(request: Request, fact_id: int) -> RedirectResponse:
    store = request.app.state.store
    writer = UserFactsWriter("human", store.conn)
    writer.delete(fact_id)
    return RedirectResponse(url="/facts", status_code=303)


@router.get("/toml")
async def toml_list(request: Request) -> object:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "toml_list.html",
        _ctx(request, names=ALLOWED_AGENT_NAMES),
    )


@router.get("/toml/{name}")
async def toml_edit_get(request: Request, name: str) -> object:
    templates = request.app.state.templates
    try:
        path = toml_path(name, project_root=request.app.state.project_root)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return templates.TemplateResponse(
        request, "toml_edit.html",
        _ctx(request, name=name, content=content),
    )


@router.post("/toml/{name}")
async def toml_edit_post(
    request: Request, name: str, content: str = Form(...),
) -> RedirectResponse:
    try:
        path = toml_path(name, project_root=request.app.state.project_root)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    atomic_write_text(path, content)
    return RedirectResponse(url=f"/toml/{name}", status_code=303)


@router.get("/personas")
async def personas_list(request: Request) -> object:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "personas_list.html",
        _ctx(request, names=ALLOWED_AGENT_NAMES),
    )

@router.get("/personas/{name}")
async def personas_edit_get(request: Request, name: str) -> object:
    templates = request.app.state.templates
    try:
        path = persona_path(name, project_root=request.app.state.project_root)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return templates.TemplateResponse(
        request, "personas_edit.html",
        _ctx(request, name=name, content=content),
    )

@router.post("/personas/{name}")
async def personas_edit_post(
    request: Request, name: str, content: str = Form(...),
) -> RedirectResponse:
    try:
        path = persona_path(name, project_root=request.app.state.project_root)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    atomic_write_text(path, content)
    return RedirectResponse(url=f"/personas/{name}", status_code=303)


@router.get("/env")
async def env_get(request: Request) -> object:
    templates = request.app.state.templates
    path = request.app.state.project_root / ".env"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return templates.TemplateResponse(request, "env.html", _ctx(request, content=content))


@router.post("/env")
async def env_post(
    request: Request,
    content: str = Form(...),
) -> RedirectResponse:
    path = request.app.state.project_root / ".env"
    atomic_write_text(path, content)
    return RedirectResponse(url="/env", status_code=303)
