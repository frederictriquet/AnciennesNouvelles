# Router overview — GET / [DASHBOARD.md — GET /]
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db, get_meta_token_expiry, get_recent_posts, get_scheduler_state

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def overview(request: Request, db: AsyncSession = Depends(get_db)):
    paused = await get_scheduler_state(db, "paused") == "true"
    suspended = await get_scheduler_state(db, "publications_suspended") == "true"
    daily_count = await get_scheduler_state(db, "daily_post_count") or "0"
    escalation = await get_scheduler_state(db, "escalation_level") or "0"
    restart_required = await get_scheduler_state(db, "config_restart_required") == "true"
    reload_pending = await get_scheduler_state(db, "config_reload_requested") == "true"

    posts = await get_recent_posts(db, limit=5)
    token_expiry = await get_meta_token_expiry(db)

    # Calcul des jours restants pour chaque token
    token_days: dict[str, int | None] = {}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for kind, expires_at in token_expiry.items():
        if expires_at is None:
            token_days[kind] = None  # permanent
        else:
            try:
                exp = datetime.fromisoformat(str(expires_at))
                token_days[kind] = max(0, (exp - now).days)
            except Exception:
                token_days[kind] = None

    return templates.TemplateResponse("overview.html", {
        "request": request,
        "paused": paused,
        "suspended": suspended,
        "daily_count": daily_count,
        "escalation": escalation,
        "restart_required": restart_required,
        "reload_pending": reload_pending,
        "posts": posts,
        "token_days": token_days,
    })
