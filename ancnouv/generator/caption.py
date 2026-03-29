# Formatage de légendes Instagram — Phase 3/11 [SPEC-2.3, SPEC-9.3, docs/IMAGE_GENERATION.md]
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from ancnouv.config import Config
from ancnouv.db.models import Event, RssArticle

if TYPE_CHECKING:
    from ancnouv.db.models import GallicaArticle

_FR_MONTHS = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

_MAX_CAPTION_CHARS = 2200  # Limite API Instagram [SPEC-2.3, IMG-14]


def truncate_caption(text: str, max_chars: int = 300) -> str:
    """Tronque au dernier mot entier + "...". [IMAGE_GENERATION.md — truncate_caption]"""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars - 3].rsplit(" ", 1)[0]
    return truncated + "..."


def _time_ago_int(year: int, month: int, day: int) -> str:
    """Formule temporelle depuis des entiers year/month/day. Gère les années négatives."""
    today = date.today()
    if year <= 0:
        # Av. J.-C. : abs(year) + today.year [IMAGE_GENERATION.md]
        delta = abs(year) + today.year
        return f"Il y a {delta} ans"
    if 1 <= year <= 9999:
        try:
            from ancnouv.utils.date_helpers import compute_time_ago
            return compute_time_ago(date(year, month, day))
        except (ValueError, OverflowError):
            pass
    # Fallback (année hors plage date Python)
    delta = today.year - year
    if delta <= 0:
        return "Il y a moins d'un an"
    return "Il y a 1 an" if delta == 1 else f"Il y a {delta} ans"


def _format_date_int(year: int, month: int, day: int) -> str:
    """Date formatée en français. Gère les années négatives (av. J.-C.)."""
    month_name = _FR_MONTHS[month]
    if year < 0:
        return f"{day} {month_name} {abs(year)} av. J.-C."
    return f"{day} {month_name} {year}"


def _time_ago_from_datetime(dt: datetime) -> str:
    """Formule temporelle depuis un datetime (articles RSS)."""
    today = date.today()
    pub = dt.date() if hasattr(dt, "date") else dt
    try:
        from ancnouv.utils.date_helpers import compute_time_ago
        return compute_time_ago(pub)
    except Exception:
        delta = (today - pub).days
        if delta < 30:
            return "Il y a moins d'un mois"
        months = delta // 30
        if months < 12:
            return f"Il y a {months} mois"
        years = months // 12
        return "Il y a 1 an" if years == 1 else f"Il y a {years} ans"


def format_caption(event: Event, config: Config) -> str:
    """Légende Instagram Mode A (Wikipedia). [SPEC-2.3]

    Format : formule temporelle + date + description tronquée + source + hashtags.
    Source template FR ou EN selon event.source_lang.
    """
    time_ago = _time_ago_int(event.year, event.month, event.day)
    date_str = _format_date_int(event.year, event.month, event.day)

    # Préfixe contextuel pour naissances et décès [SPEC-2.3]
    raw_desc = event.description
    if event.event_type == 'death':
        raw_desc = f'Décès : {raw_desc}'
    elif event.event_type == 'birth':
        raw_desc = f'Naissance : {raw_desc}'
    description = truncate_caption(raw_desc)

    if event.source_lang == "en":
        source = config.caption.source_template_en
    else:
        source = config.caption.source_template_fr

    url_line = ""
    if config.caption.include_wikipedia_url and event.wikipedia_url:
        url_line = f"\n{event.wikipedia_url}"

    body = f"{time_ago}, le {date_str} :\n\n{description}\n\n{source}{url_line}"

    hashtags_str = " ".join(config.caption.hashtags)
    caption = f"{body}{config.caption.hashtags_separator}{hashtags_str}"

    # [IMG-14] Vérification globale ≤ 2200 chars
    if len(caption) > _MAX_CAPTION_CHARS:
        caption = caption[:_MAX_CAPTION_CHARS]

    return caption


def format_caption_gallica(article: "GallicaArticle", config: Config) -> str:
    """Légende Instagram Mode C (BnF Gallica). [SPEC-9.3]

    Format : titre — description tronquée — date publication — source BnF Gallica — hashtags.
    """
    lines: list[str] = []

    # Titre de l'article
    lines.append(article.title)

    # Description si disponible (tronquée)
    if article.description:
        desc = truncate_caption(article.description, max_chars=300)
        lines.append(desc)

    # Date de publication historique
    if hasattr(article.date_published, "strftime"):
        month_name = _FR_MONTHS[article.date_published.month]
        date_str = f"{article.date_published.day} {month_name} {article.date_published.year}"
    else:
        date_str = str(article.date_published)
    lines.append(f"Publié le {date_str}")

    # Attribution source
    if article.source_name:
        lines.append(f"Source : BnF Gallica — {article.source_name}")
    else:
        lines.append("Source : BnF Gallica")

    hashtags_str = " ".join(config.caption.hashtags)
    separator = config.caption.hashtags_separator
    caption = separator.join(["\n".join(lines), hashtags_str])

    # [IMG-14] Vérification globale ≤ 2200 chars
    if len(caption) > _MAX_CAPTION_CHARS:
        caption = caption[:_MAX_CAPTION_CHARS]

    return caption


def format_caption_rss(article: RssArticle, config: Config) -> str:
    """Légende Instagram Mode B (RSS). Formule temporelle obligatoire. [IMG-7, SPEC-2.3]

    Format :
    {time_ago}, le {date_str} :

    {title}

    {summary tronqué}

    Source : {feed_name}
    {hashtags}
    """
    pub = article.published_at
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)

    time_ago = _time_ago_from_datetime(pub)
    date_str = f"{pub.day} {_FR_MONTHS[pub.month]} {pub.year}"

    summary = truncate_caption(article.summary or "")

    body_parts = [
        f"{time_ago}, le {date_str} :",
        article.title,
        summary,
        f"Source : {article.feed_name}",
    ]
    body = "\n\n".join(p for p in body_parts if p)

    hashtags_str = " ".join(config.caption.hashtags)
    caption = f"{body}{config.caption.hashtags_separator}{hashtags_str}"

    # [IMG-14] Vérification globale ≤ 2200 chars
    if len(caption) > _MAX_CAPTION_CHARS:
        caption = caption[:_MAX_CAPTION_CHARS]

    return caption
