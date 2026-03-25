# Router posts — GET /posts [DASHBOARD.md — GET /posts]
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db, get_recent_posts

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/posts", response_class=HTMLResponse)
async def posts_page(
    request: Request,
    limit: int = 20,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if limit > 100:
        limit = 100
    posts = await get_recent_posts(db, limit=limit, status=status or None)
    return templates.TemplateResponse("posts.html", {
        "request": request,
        "posts": posts,
        "limit": limit,
        "status_filter": status,
    })
