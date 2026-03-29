# Router config — GET /config, POST /config/set, POST /config/reset/{key} [DASHBOARD.md]
from __future__ import annotations

import json
import logging

import yaml
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from config_meta import (
    REGISTRY,
    REGISTRY_BY_KEY,
    SECTIONS,
    deserialize_value,
    serialize_value,
    validate_value,
    value_from_form,
    value_to_display,
)
from db import delete_override, get_all_overrides, get_db, get_scheduler_state, set_scheduler_state, upsert_override

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _load_baseline() -> dict:
    """Charge config.yml pour les valeurs par défaut. [DASH-W3]"""
    try:
        with open("config.yml") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _get_baseline_value(key: str, baseline: dict) -> object:
    """Lit une valeur depuis le dict YAML par dot-path. Retourne None si absent."""
    from config_meta import REGISTRY_BY_KEY
    parts = key.split(".")
    node = baseline
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            # Valeur absente du YAML → utilise le défaut du registre
            return REGISTRY_BY_KEY[key].default if key in REGISTRY_BY_KEY else None
        node = node[part]
    return node


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request, db: AsyncSession = Depends(get_db)):
    baseline = _load_baseline()
    overrides_raw = await get_all_overrides(db)
    overrides_map = {r["key"]: r for r in overrides_raw}

    restart_required = await get_scheduler_state(db, "config_restart_required") == "true"
    reload_pending = await get_scheduler_state(db, "config_reload_requested") == "true"

    # Construit la liste des settings avec valeur effective et source
    fields = []
    for meta in REGISTRY:
        if meta.key in overrides_map:
            raw = overrides_map[meta.key]["value"]
            try:
                effective = deserialize_value(raw, meta.value_type)
            except Exception as exc:
                logger.debug("config router : désérialisation override '%s' impossible (%s) — fallback default", meta.key, exc)
                effective = meta.default
            source = "override"
            updated_at = overrides_map[meta.key]["updated_at"]
        else:
            baseline_val = _get_baseline_value(meta.key, baseline)
            effective = baseline_val if baseline_val is not None else meta.default
            source = "défaut"
            updated_at = None

        fields.append({
            "meta": meta,
            "effective": effective,
            "display": value_to_display(effective, meta.value_type),
            "source": source,
            "updated_at": updated_at,
            "field_id": meta.key.replace(".", "-"),
        })

    # Groupe par section
    sections_data = []
    for section_key, section_label in SECTIONS:
        section_fields = [f for f in fields if f["meta"].section == section_key]
        if section_fields:
            sections_data.append({"key": section_key, "label": section_label, "fields": section_fields})

    return templates.TemplateResponse(request, "config.html", {
        "sections": sections_data,
        "restart_required": restart_required,
        "reload_pending": reload_pending,
    })


@router.post("/config/set", response_class=HTMLResponse)
async def config_set(
    request: Request,
    key: str = Form(...),
    value: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Écrit un override [DASHBOARD.md — POST /config/set]."""
    if key not in REGISTRY_BY_KEY:
        return HTMLResponse(f'<p class="error">Clé inconnue : {key}</p>', status_code=400)

    meta = REGISTRY_BY_KEY[key]

    try:
        typed_value = value_from_form(value, meta.value_type)
    except Exception as e:
        return _field_fragment(request, meta, value, source="override", error=str(e))

    error = validate_value(meta, typed_value)
    if error:
        return _field_fragment(request, meta, value, source="override", error=error)

    serialized = serialize_value(typed_value, meta.value_type)
    await upsert_override(db, key, serialized, meta.value_type)
    await set_scheduler_state(db, "config_reload_requested", "true")

    if meta.requires_restart:
        await set_scheduler_state(db, "config_restart_required", "true")

    return _field_fragment(
        request, meta,
        value_to_display(typed_value, meta.value_type),
        source="override", error=None,
    )


@router.post("/config/reset/{key:path}", response_class=HTMLResponse)
async def config_reset(
    request: Request,
    key: str,
    db: AsyncSession = Depends(get_db),
):
    """Supprime un override (retour valeur config.yml). [DASHBOARD.md — POST /config/reset/{key}]"""
    if key not in REGISTRY_BY_KEY:
        return HTMLResponse(f'<p class="error">Clé inconnue : {key}</p>', status_code=400)

    meta = REGISTRY_BY_KEY[key]
    await delete_override(db, key)
    await set_scheduler_state(db, "config_reload_requested", "true")

    # Vérifie si config_restart_required peut être levé
    overrides_raw = await get_all_overrides(db)
    remaining_restart = any(
        REGISTRY_BY_KEY.get(r["key"], object()) and REGISTRY_BY_KEY[r["key"]].requires_restart
        for r in overrides_raw
        if r["key"] in REGISTRY_BY_KEY
    )
    if not remaining_restart:
        await set_scheduler_state(db, "config_restart_required", "false")

    baseline = _load_baseline()
    baseline_val = _get_baseline_value(key, baseline)
    display = value_to_display(
        baseline_val if baseline_val is not None else meta.default, meta.value_type
    )
    return _field_fragment(request, meta, display, source="défaut", error=None)


def _field_fragment(
    request: Request,
    meta,
    display_value: str,
    source: str,
    error: str | None,
) -> HTMLResponse:
    """Retourne le fragment HTML d'un champ (htmx swap)."""
    content = templates.TemplateResponse(request, "_field.html", {
        "field": {
            "meta": meta,
            "display": display_value,
            "source": source,
            "updated_at": None,
            "field_id": meta.key.replace(".", "-"),
            "error": error,
        },
    })
    return content
