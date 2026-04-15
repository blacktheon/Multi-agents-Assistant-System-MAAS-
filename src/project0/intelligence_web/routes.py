"""FastAPI routes for the Intelligence webapp (6e)."""

from __future__ import annotations

from datetime import date
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from project0.intelligence.report import list_report_dates, read_report
from project0.intelligence_web.config import WebConfig
from project0.intelligence_web.feedback import load_thumbs_state_for
from project0.intelligence_web.rendering import build_report_context

router = APIRouter()


def _cfg(request: Request) -> WebConfig:
    return request.app.state.config  # type: ignore[no-any-return]


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


def _render_report_page(
    request: Request, cfg: WebConfig, target: date
) -> HTMLResponse:
    report_path = cfg.reports_dir / f"{target.isoformat()}.json"
    if not report_path.exists():
        return HTMLResponse(
            _templates(request).env.get_template("not_found.html").render(
                {"request": request, "missing_date": target.isoformat()}
            ),
            status_code=404,
        )
    report_dict = read_report(report_path)
    all_dates = list_report_dates(cfg.reports_dir)
    feedback_state = load_thumbs_state_for(target.isoformat(), cfg.feedback_dir)
    ctx = build_report_context(
        report_dict=report_dict,
        feedback_state=feedback_state,
        all_dates=all_dates,
        current=target,
        public_base_url=cfg.public_base_url,
    )
    return _templates(request).TemplateResponse(request, "report.html", ctx)


@router.get("/", response_class=HTMLResponse)
async def root(request: Request) -> HTMLResponse:
    cfg = _cfg(request)
    dates = list_report_dates(cfg.reports_dir)
    if not dates:
        return _templates(request).TemplateResponse(request, "empty.html", {})
    return _render_report_page(request, cfg, dates[0])


@router.get("/reports/{date_str}", response_class=HTMLResponse)
async def report_by_date(request: Request, date_str: str) -> HTMLResponse:
    cfg = _cfg(request)
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"bad date: {date_str}")
    return _render_report_page(request, cfg, target)


@router.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"
