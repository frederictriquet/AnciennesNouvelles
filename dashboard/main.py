# Dashboard ancnouv — FastAPI app [DASHBOARD.md — Phase 10.3]
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from routers import config, overview, posts

app = FastAPI(title="ancnouv dashboard", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(overview.router)
app.include_router(config.router)
app.include_router(posts.router)


@app.get("/health")
async def health():
    """Healthcheck [DASHBOARD.md — GET /health]."""
    return JSONResponse({"status": "ok"})
