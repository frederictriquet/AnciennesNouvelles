# Registre des settings exposés par le dashboard [DASHBOARD.md — Registre des settings]
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

# Valeurs par défaut du registre (reflète config.py de ancnouv)
# Doit rester en parité avec ancnouv/config.py [DASH-W2]

ValueType = Literal["str", "int", "float", "bool", "list", "dict"]


@dataclass
class SettingMeta:
    key: str
    label: str
    value_type: ValueType
    default: Any
    description: str = ""
    requires_restart: bool = False
    validation: dict = field(default_factory=dict)   # min, max, options, etc.
    section: str = ""


# ─── Registre complet ─────────────────────────────────────────────────────────────

REGISTRY: list[SettingMeta] = [
    # scheduler
    SettingMeta("scheduler.generation_cron", "Cron de génération", "str", "0 */4 * * *",
                description="Expression cron APScheduler (ex: 0 */4 * * *)",
                requires_restart=True, section="scheduler"),
    SettingMeta("scheduler.max_pending_posts", "Posts en attente max", "int", 1,
                validation={"min": 1}, section="scheduler"),
    SettingMeta("scheduler.approval_timeout_hours", "Timeout approbation (h)", "int", 48,
                validation={"min": 1, "max": 8760}, section="scheduler"),
    SettingMeta("scheduler.auto_publish", "Publication automatique", "bool", False,
                section="scheduler"),

    # content
    SettingMeta("content.prefetch_days", "Jours de prefetch", "int", 30,
                validation={"min": 1}, section="content"),
    SettingMeta("content.date_window_days", "Fenêtre de dates (±jours)", "int", 0,
                description="0 = uniquement aujourd'hui. N = MM/JJ d'aujourd'hui ±N jours.",
                validation={"min": 0, "max": 30}, section="content"),
    SettingMeta("content.event_min_age_years", "Ancienneté minimale (ans)", "int", 0,
                description="0 = pas de limite. Ex : 5 = ignorer les événements survenus il y a moins de 5 ans.",
                validation={"min": 0}, section="content"),
    SettingMeta("content.event_max_age_years", "Ancienneté maximale (ans)", "int", 0,
                description="0 = pas de limite. Ex : 200 = ignorer les événements survenus il y a plus de 200 ans.",
                validation={"min": 0}, section="content"),
    SettingMeta("content.wikipedia_event_types", "Types d'événements Wikipedia", "list",
                ["events"],
                description="Une valeur par ligne (ou virgules) : events, births, deaths, holidays, selected",
                section="content"),
    SettingMeta("content.wikipedia_min_events", "Minimum d'événements Wikipedia", "int", 3,
                validation={"min": 1}, section="content"),
    SettingMeta("content.deduplication_policy", "Politique de déduplication", "str", "never",
                validation={"options": ["never", "window", "always"]}, section="content"),
    SettingMeta("content.deduplication_window_days", "Fenêtre de déduplication (jours)", "int", 365,
                validation={"min": 1}, section="content"),
    SettingMeta("content.image_retention_days", "Rétention des images (jours)", "int", 7,
                validation={"min": 1}, section="content"),
    SettingMeta("content.low_stock_threshold", "Seuil stock bas", "int", 3,
                validation={"min": 1}, section="content"),
    SettingMeta("content.mix_ratio", "Ratio RSS/Wikipedia", "float", 0.2,
                validation={"min": 0.0, "max": 1.0}, section="content"),

    # content.rss
    SettingMeta("content.rss.enabled", "RSS activé", "bool", False, section="content.rss"),
    SettingMeta("content.rss.min_delay_days", "Délai minimum (jours)", "int", 90,
                validation={"min": 1}, section="content.rss"),
    SettingMeta("content.rss.max_age_days", "Âge maximum (jours)", "int", 180,
                validation={"min": 1}, section="content.rss"),
    SettingMeta("content.rss.feeds", "Flux RSS", "list", [],
                description="Liste de flux : [{\"url\": \"...\", \"name\": \"...\"}]",
                section="content.rss"),

    # image
    SettingMeta("image.jpeg_quality", "Qualité JPEG", "int", 95,
                validation={"min": 1, "max": 100}, section="image"),
    SettingMeta("image.paper_texture", "Texture papier", "bool", True, section="image"),
    SettingMeta("image.paper_texture_intensity", "Intensité texture", "int", 8,
                validation={"min": 0}, section="image"),
    SettingMeta("image.masthead_text", "Texte masthead", "str", "ANCIENNES NOUVELLES",
                section="image"),
    SettingMeta("image.force_template", "Template forcé", "str", "",
                description="Laisser vide pour auto. Valeurs : medieval, moderne, xix, xx_first, xx_second, xxi",
                validation={"options": ["", "medieval", "moderne", "xix", "xx_first", "xx_second", "xxi"]},
                section="image"),

    # caption
    SettingMeta("caption.hashtags", "Hashtags", "list", ["#histoire", "#onthisday"],
                description="Un hashtag par ligne, doit commencer par #",
                section="caption"),
    SettingMeta("caption.hashtags_separator", "Séparateur hashtags", "str", "\n\n",
                section="caption"),
    SettingMeta("caption.include_wikipedia_url", "Inclure URL Wikipedia", "bool", False,
                section="caption"),
    SettingMeta("caption.source_template_fr", "Template source (FR)", "str",
                "Source : Wikipédia", section="caption"),
    SettingMeta("caption.source_template_en", "Template source (EN)", "str",
                "Source : Wikipedia (EN)", section="caption"),

    # image_hosting
    SettingMeta("image_hosting.public_base_url", "URL publique du serveur d'images", "str", "",
                description="URL HTTPS sans slash final (ex: https://mondomaine.fr)",
                section="image_hosting"),
    SettingMeta("image_hosting.local_port", "Port serveur d'images", "int", 8765,
                validation={"min": 1024, "max": 65535}, requires_restart=True,
                section="image_hosting"),

    # instagram
    SettingMeta("instagram.enabled", "Instagram activé", "bool", False,
                requires_restart=True, section="instagram"),
    SettingMeta("instagram.max_daily_posts", "Posts par jour max", "int", 25,
                validation={"min": 1, "max": 50}, section="instagram"),

    # facebook
    SettingMeta("facebook.enabled", "Facebook activé", "bool", False,
                requires_restart=True, section="facebook"),

    # telegram
    SettingMeta("telegram.notification_debounce", "Debounce notifications (s)", "int", 2,
                validation={"min": 0}, section="telegram"),

    # stories
    SettingMeta("stories.enabled", "Stories activées", "bool", False, section="stories"),
    SettingMeta("stories.max_text_chars", "Caractères max (stories)", "int", 400,
                validation={"min": 50, "max": 1000}, section="stories"),

    # racine
    SettingMeta("log_level", "Niveau de log", "str", "INFO",
                validation={"options": ["DEBUG", "INFO", "WARNING", "ERROR"]},
                section="general"),
]

REGISTRY_BY_KEY: dict[str, SettingMeta] = {s.key: s for s in REGISTRY}

# Sections dans l'ordre d'affichage
SECTIONS = [
    ("general", "Général"),
    ("scheduler", "Scheduler"),
    ("content", "Contenu"),
    ("content.rss", "Contenu — RSS"),
    ("image", "Image"),
    ("caption", "Légende"),
    ("image_hosting", "Hébergement images"),
    ("instagram", "Instagram"),
    ("facebook", "Facebook"),
    ("stories", "Stories"),
    ("telegram", "Telegram"),
]


# ─── Sérialisation / désérialisation ─────────────────────────────────────────────

def serialize_value(value: Any, value_type: ValueType) -> str:
    """Encode une valeur Python en JSON pour stockage en DB."""
    return json.dumps(value)


def deserialize_value(raw: str, value_type: ValueType) -> Any:
    """Décode une chaîne JSON depuis la DB."""
    return json.loads(raw)


def value_from_form(raw_form: str, value_type: ValueType) -> Any:
    """Convertit la valeur brute du formulaire HTML en valeur Python typée."""
    if value_type == "bool":
        return raw_form.lower() in ("true", "1", "on", "yes")
    if value_type == "int":
        return int(raw_form)
    if value_type == "float":
        return float(raw_form)
    if value_type == "list":
        # Textarea : une valeur par ligne (ou virgule-séparé sur une ligne) → liste de strings
        # Accepter les deux formats pour éviter les erreurs silencieuses
        if "\n" in raw_form:
            lines = [l.strip() for l in raw_form.splitlines() if l.strip()]
        else:
            lines = [l.strip() for l in raw_form.split(",") if l.strip()]
        return lines
    if value_type == "dict":
        return json.loads(raw_form)
    return raw_form  # str


def value_to_display(value: Any, value_type: ValueType) -> str:
    """Convertit une valeur Python en représentation affichable dans le formulaire."""
    if value_type == "list":
        if isinstance(value, list):
            return "\n".join(str(v) for v in value)
        return str(value)
    if value_type in ("dict",):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value) if value is not None else ""


# ─── Validation ───────────────────────────────────────────────────────────────────

def validate_value(meta: SettingMeta, value: Any) -> str | None:
    """Retourne un message d'erreur si invalide, None si OK. [DASH-W2]"""
    v = meta.validation
    if "min" in v and value < v["min"]:
        return f"Valeur minimale : {v['min']}"
    if "max" in v and value > v["max"]:
        return f"Valeur maximale : {v['max']}"
    if "options" in v and value not in v["options"]:
        return f"Valeur invalide. Options : {', '.join(str(o) for o in v['options'])}"
    if meta.value_type == "list" and meta.key == "caption.hashtags":
        for item in value:
            if not item.startswith("#"):
                return f"Chaque hashtag doit commencer par # (invalide : {item!r})"
    if meta.key == "scheduler.generation_cron":
        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(value)
        except Exception as e:
            return f"Expression cron invalide : {e}"
    return None
