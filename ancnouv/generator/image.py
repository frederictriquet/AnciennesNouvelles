# Génération d'images style gazette vintage — Phase 3 [SPEC-2.3, SPEC-3.2.3, docs/IMAGE_GENERATION.md]
from __future__ import annotations

import logging
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import numpy as np  # [IMG-C7] import au niveau module — fail-fast si absent ou mauvaise version
from PIL import Image, ImageDraw, ImageFont

from ancnouv.exceptions import GeneratorError

if TYPE_CHECKING:
    from ancnouv.config import Config
    from ancnouv.db.models import Event, RssArticle

logger = logging.getLogger(__name__)

# [IMAGE_GENERATION.md] Constantes de mise en page
MARGIN = 20        # marge extérieure (bordure décorative)
PADDING = 40       # padding intérieur (texte depuis la bordure)
W_INNER = 1000     # = 1080 - 2 * PADDING

# Chemin des polices : projet root / assets / fonts [IMAGE_GENERATION.md]
# .parent.parent.parent = ancnouv/generator/image.py → ancnouv/generator → ancnouv → projet root
FONTS_DIR = Path(__file__).parent.parent.parent / "assets" / "fonts"

# Palettes par époque [SPEC-7bis, RF-7bis.2]
TEMPLATES: dict[str, dict[str, str]] = {
    # Antiquité / Moyen Âge (<1500) — parchemin, enluminure
    "medieval": {
        "background":      "#F5E6C0",
        "background_dark": "#E8CC90",
        "text_primary":    "#2A1505",
        "text_secondary":  "#6B3A12",
        "accent":          "#8B1A00",
        "border":          "#3D1F08",
        "divider":         "#8B5A2B",
    },
    # Époque moderne (1500–1799) — gazette imprimée, sépia, crème cassé
    "moderne": {
        "background":      "#F2E8D5",
        "background_dark": "#E8D9BC",
        "text_primary":    "#1A1008",
        "text_secondary":  "#4A3728",
        "accent":          "#8B2020",
        "border":          "#2C1810",
        "divider":         "#6B4C3B",
    },
    # XIXe siècle (1800–1899) — journal noir & blanc, gravures
    "xix": {
        "background":      "#F5F3EE",
        "background_dark": "#E8E4DC",
        "text_primary":    "#1A1A1A",
        "text_secondary":  "#4A4A4A",
        "accent":          "#2A2A2A",
        "border":          "#1A1A1A",
        "divider":         "#6A6A6A",
    },
    # Première moitié XXe (1900–1959) — presse illustrée, Art Déco
    "xx_first": {
        "background":      "#FAFAF0",
        "background_dark": "#EEEEDC",
        "text_primary":    "#0A0A0A",
        "text_secondary":  "#3A3A3A",
        "accent":          "#1A1A1A",
        "border":          "#0A0A0A",
        "divider":         "#5A5A5A",
    },
    # Deuxième moitié XXe (1960–1999) — journal moderne, couleurs froides
    "xx_second": {
        "background":      "#FAFAFA",
        "background_dark": "#EEEEEE",
        "text_primary":    "#0D0D1A",
        "text_secondary":  "#1A2560",
        "accent":          "#C8001A",
        "border":          "#1A2560",
        "divider":         "#8090C0",
    },
    # XXIe siècle (2000+) — épuré, typographie contemporaine
    "xxi": {
        "background":      "#FFFFFF",
        "background_dark": "#F0F0F0",
        "text_primary":    "#111111",
        "text_secondary":  "#555555",
        "accent":          "#0066CC",
        "border":          "#222222",
        "divider":         "#AAAAAA",
    },
}

def _get_template_for_year(year: int | None, force_template: str | None = None) -> dict[str, str]:
    """Retourne la palette de couleurs selon l'époque de l'événement. [SPEC-7bis, RF-7bis.1, RF-7bis.4]

    force_template : clé dans TEMPLATES — prioritaire sur la détection automatique.
    year None ou négatif → medieval.
    RSS (year absent) → appelé avec year=None → medieval par défaut, mais l'appelant
    passe force explicitement "xxi" pour les articles RSS [RF-7bis.1].
    """
    if force_template and force_template in TEMPLATES:
        return TEMPLATES[force_template]
    if year is None or year < 1500:
        return TEMPLATES["medieval"]
    if year < 1800:
        return TEMPLATES["moderne"]
    if year < 1900:
        return TEMPLATES["xix"]
    if year < 1960:
        return TEMPLATES["xx_first"]
    if year < 2000:
        return TEMPLATES["xx_second"]
    return TEMPLATES["xxi"]


_FR_MONTHS = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


# ---------------------------------------------------------------------------
# Utilitaires de chargement de polices
# ---------------------------------------------------------------------------

def load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    """Charge une police TTF. Fallback sur la police par défaut avec WARNING. [IMAGE_GENERATION.md]"""
    if not path.exists():
        logger.warning("Police manquante : %s — utilisation police par défaut", path.name)
        return ImageFont.load_default()
    return ImageFont.truetype(str(path), size)


def _load_fonts() -> dict:
    """Charge toutes les polices nécessaires. [IMG-13]

    Polices requises absentes → ERROR (rendu incorrect).
    Police italique absente → WARNING (non utilisée activement en v1).
    """
    required = {
        "masthead":   ("PlayfairDisplay-Bold.ttf",      72),
        "date_large": ("IMFellEnglish-Regular.ttf",     40),
        "date_small": ("IMFellEnglish-Regular.ttf",     32),
        "body":       ("LibreBaskerville-Regular.ttf",  28),
        "footer":     ("LibreBaskerville-Regular.ttf",  24),
    }
    optional = {
        "body_italic": ("LibreBaskerville-Italic.ttf", 28),
    }
    fonts: dict = {}
    for key, (filename, size) in required.items():
        path = FONTS_DIR / filename
        if not path.exists():
            logger.error(
                "Police requise manquante : %s — run `python -m ancnouv setup fonts`", filename
            )
        fonts[key] = load_font(path, size)
    for key, (filename, size) in optional.items():
        path = FONTS_DIR / filename
        if not path.exists():
            logger.warning("Police optionnelle manquante : %s", filename)
        fonts[key] = load_font(path, size)
    return fonts


# ---------------------------------------------------------------------------
# Utilitaires de texte (wrap)
# ---------------------------------------------------------------------------

def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Word-wrap pixel-based. Préserve les sauts de ligne forcés. [IMG-12, IMAGE_GENERATION.md]

    Mot unique dépassant max_width → placé seul sur sa ligne (pas de césure). [IMG-12]
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = [w for w in paragraph.split(" ") if w]
        if not words:
            lines.append("")  # Ligne vide intentionnelle préservée
            continue
        current = ""
        for word in words:
            candidate = (current + " " + word).lstrip()
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                # [IMG-12] Mot trop long → seul sur sa ligne
                if draw.textlength(word, font=font) > max_width:
                    lines.append(word)
                    current = ""
                else:
                    current = word
        if current:
            lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# Fonctions de rendu
# ---------------------------------------------------------------------------

def _draw_paper_texture(img: Image.Image, intensity: int = 8) -> Image.Image:
    """Bruit aléatoire numpy sur les pixels. Retourne une NOUVELLE image. [IMAGE_GENERATION.md]

    [IMG-m7] Réaffectation obligatoire : `img = _draw_paper_texture(img, intensity)`.
    numpy>=1.24,<2 requis — np.random.randint API modifiée en v2. [IMAGE_GENERATION.md]
    """
    arr = np.array(img, dtype=np.int16)
    noise = np.random.randint(-intensity, intensity + 1, arr.shape, dtype=np.int16)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _draw_decorative_border(draw: ImageDraw.ImageDraw, W: int, H: int, colors: dict) -> None:
    """Rectangle décoratif épaisseur 4px, couleur border. [IMAGE_GENERATION.md]"""
    draw.rectangle(
        [(MARGIN, MARGIN), (W - MARGIN, H - MARGIN)],
        outline=colors["border"],
        width=4,
    )


def _draw_masthead(
    draw: ImageDraw.ImageDraw,
    W: int,
    masthead_text: str,
    fonts: dict,
    colors: dict,
    center_y: int = 115,
) -> None:
    """Masthead centré horizontalement, ancré sur center_y. [IMG-10, IMG-4]

    [IMG-10] Centrage horizontal via draw.textbbox — plus précis que anchor='mt'.
    center_y = 115 pour le feed (zone y=60–170) ; ajustable pour la Story [SPEC-7].
    """
    font = fonts["masthead"]
    bbox = draw.textbbox((0, 0), masthead_text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (W - text_w) // 2
    y = center_y - text_h // 2
    draw.text((x, y), masthead_text, fill=colors["accent"], font=font)


def _draw_date_banner(
    draw: ImageDraw.ImageDraw,
    W: int,
    time_ago: str,
    date_str: str,
    fonts: dict,
    colors: dict,
    y_time_ago: int = 185,
    y_date_str: int = 230,
) -> None:
    """Banner date : deux lignes centrées. [IMG-11]

    y_time_ago=185 / y_date_str=230 pour le feed ; ajustables pour la Story [SPEC-7].
    [IMG-11] L'écart 45px garantit l'absence de chevauchement (date_large 40px).
    """
    for text, y, font_key in (
        (time_ago, y_time_ago, "date_large"),
        (date_str, y_date_str, "date_small"),
    ):
        font = fonts[font_key]
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        x = (W - text_w) // 2
        draw.text((x, y), text, fill=colors["text_secondary"], font=font)


def _draw_divider(draw: ImageDraw.ImageDraw, W: int, y: int, colors: dict) -> None:
    """Filet horizontal divider. [IMAGE_GENERATION.md]"""
    draw.line([(PADDING, y), (W - PADDING, y)], fill=colors["divider"], width=1)


def _draw_thumbnail(
    img: Image.Image, thumbnail: Image.Image, y: int, W: int
) -> None:
    """Colle le thumbnail dans la zone W_INNER×300px à partir de y. [IMAGE_GENERATION.md]

    Letterbox : zoom au ratio le plus petit (fit), centré dans la zone.
    L'image entière est visible — le fond papier remplit les bandes restantes.
    """
    TARGET_W = W - 2 * PADDING  # = W_INNER pour W=1080
    TARGET_H = 300

    orig_w, orig_h = thumbnail.size
    if orig_h == 0 or orig_w == 0:
        return

    scale = min(TARGET_W / orig_w, TARGET_H / orig_h)
    new_w = int(orig_w * scale)
    new_h = int(orig_h * scale)
    resized = thumbnail.resize((new_w, new_h), Image.LANCZOS)
    x_offset = PADDING + (TARGET_W - new_w) // 2
    y_offset = y + (TARGET_H - new_h) // 2
    img.paste(resized, (x_offset, y_offset))


def _draw_event_text(
    draw: ImageDraw.ImageDraw,
    W: int,
    text: str,
    text_y: int,
    max_height: int,
    fonts: dict,
    colors: dict,
) -> None:
    """Rendu du texte principal. Limite 18 lignes max avec tronc. "...". [IMG-2]

    Interlignage : 28px * 1.5 = 42px.
    Arrêt à effective_max = min(18, max_height // line_height). [IMG-2]
    """
    font = fonts["body"]
    line_height = 42  # 28px * 1.5

    max_by_height = max_height // line_height
    effective_max = min(18, max_by_height)

    inner_w = W - 2 * PADDING
    all_lines = _wrap_text(draw, text, font, inner_w)

    needs_ellipsis = len(all_lines) > effective_max
    display_lines = all_lines[:effective_max] if needs_ellipsis else all_lines

    if needs_ellipsis and display_lines:
        # Tronquer la dernière ligne pour ajouter "..."
        last = display_lines[-1]
        ellipsis = "..."
        while last and draw.textlength(last + ellipsis, font=font) > inner_w:
            if " " in last:
                last = last.rsplit(" ", 1)[0]
            else:
                last = last[:-1]
        display_lines[-1] = last + ellipsis

    y = text_y
    for line in display_lines:
        draw.text((PADDING, y), line, fill=colors["text_primary"], font=font)
        y += line_height


def _draw_footer(
    draw: ImageDraw.ImageDraw,
    W: int,
    H: int,
    source_text: str,
    fonts: dict,
    colors: dict,
    footer_y: int | None = None,
) -> None:
    """Footer centré. [IMG-3]

    footer_y explicite (Story) ou calculé comme H - 60 (feed, défaut).
    Zone footer feed : y=1290 = 1350 - 60.
    """
    font = fonts["footer"]
    bbox = draw.textbbox((0, 0), source_text, font=font)
    text_w = bbox[2] - bbox[0]
    x = (W - text_w) // 2
    y = footer_y if footer_y is not None else H - 60
    draw.text((x, y), source_text, fill=colors["text_secondary"], font=font)


# ---------------------------------------------------------------------------
# Extraction des métadonnées source
# ---------------------------------------------------------------------------

def _compute_time_ago_int(year: int, month: int, day: int) -> str:
    """Formule temporelle pour le banner image. Gère les années négatives."""
    today = date.today()
    if year <= 0:
        delta = abs(year) + today.year
        return f"Il y a {delta} ans"
    if 1 <= year <= 9999:
        try:
            from ancnouv.utils.date_helpers import compute_time_ago
            return compute_time_ago(date(year, month, day))
        except (ValueError, OverflowError):
            pass
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


def _format_datetime_fr(dt: datetime) -> str:
    """Formate un datetime en français (ex : '21 décembre 2025')."""
    return f"{dt.day} {_FR_MONTHS[dt.month]} {dt.year}"


# ---------------------------------------------------------------------------
# Génération principale
# ---------------------------------------------------------------------------

async def fetch_thumbnail(image_url: str | None) -> Image.Image | None:
    """Télécharge le thumbnail. Retourne None sur échec ou URL absente. [IMAGE_GENERATION.md]

    import httpx au niveau module — fail-fast si absent. [IMG-9]
    Timeout 5s. Retourne None si HTTP != 200 ou toute exception.
    User-Agent obligatoire : Wikimedia retourne 403 sans en-tête. [IMG-14]
    """
    if not image_url:
        return None
    headers = {"User-Agent": "AnciennesNouvelles/1.0 (https://github.com/anciennesnouv)"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(image_url, timeout=5.0, headers=headers)
        if r.status_code != 200:
            logger.warning("fetch_thumbnail HTTP %d pour %s", r.status_code, image_url)
            return None
        return Image.open(BytesIO(r.content)).convert("RGB")
    except Exception as exc:
        logger.warning("fetch_thumbnail échoué pour %s : %s", image_url, exc)
        return None


def _generate_image_inner(
    source: "Event | RssArticle",
    config: "Config",
    output_path: Path,
    thumbnail: Image.Image | None = None,
) -> Path:
    """Séquence de rendu Pillow. [IMAGE_GENERATION.md — IMG-8, SPEC-7bis]"""
    from ancnouv.db.models import RssArticle
    from ancnouv.utils.text_helpers import truncate_for_image

    W = config.image.width
    H = config.image.height

    # Sélection du template selon l'époque [SPEC-7bis, RF-7bis.1]
    # RSS → toujours "xxi" ; Event → déterminé par year
    if isinstance(source, RssArticle):
        colors = _get_template_for_year(None, config.image.force_template or "xxi")
    else:
        event_year = getattr(source, "year", None)
        colors = _get_template_for_year(event_year, config.image.force_template)

    # 1. Image de fond
    img = Image.new("RGB", (W, H), color=colors["background"])

    # 2. Texture papier [IMG-m7] réaffectation obligatoire
    if config.image.paper_texture:
        img = _draw_paper_texture(img, intensity=config.image.paper_texture_intensity)

    draw = ImageDraw.Draw(img)
    fonts = _load_fonts()

    # 3. Bordure décorative + masthead
    _draw_decorative_border(draw, W, H, colors)
    _draw_masthead(draw, W, config.image.masthead_text, fonts, colors)

    # 4. Banner date — extraction selon le type de source
    if isinstance(source, RssArticle):
        time_ago = "ACTUALITÉ RSS"
        date_str = _format_datetime_fr(source.published_at)
        text = truncate_for_image(f"{source.title}\n\n{source.summary or ''}")
        source_text = f"Source : {source.feed_name}"
    else:
        # Event (ou duck-typed Event pour les tests)
        year = getattr(source, "year", None)
        if year is None:
            time_ago = "Date inconnue"
            date_str = "Date inconnue"
        else:
            time_ago = _compute_time_ago_int(
                source.year, source.month, source.day
            )
            date_str = _format_date_int(source.year, source.month, source.day)
        raw_desc = getattr(source, "description", "") or ""
        event_type = getattr(source, "event_type", "event")
        if event_type == "deaths":
            raw_desc = f"Décès : {raw_desc}"
        elif event_type == "births":
            raw_desc = f"Naissance : {raw_desc}"
        text = truncate_for_image(raw_desc, max_chars=1200)
        text = text[:1].upper() + text[1:] if text else text
        if getattr(source, "source_lang", "fr") == "en":
            source_text = config.caption.source_template_en
        else:
            source_text = config.caption.source_template_fr

    _draw_date_banner(draw, W, time_ago, date_str, fonts, colors)

    # 5. Thumbnail + texte événement
    # Layout avec thumbnail : texte d'abord (teaser), puis photo en bas. [IMG-11]
    # Évite le pattern "une ligne + grande photo noire" pour les descriptions courtes.
    if thumbnail is not None:
        _draw_event_text(draw, W, text, text_y=285, max_height=588, fonts=fonts, colors=colors)
        _draw_thumbnail(img, thumbnail, y=920, W=W)
    else:
        _draw_event_text(draw, W, text, text_y=285, max_height=915, fonts=fonts, colors=colors)

    # 6. Séparateurs + footer (trois dividers selon la spec [IMAGE_GENERATION.md])
    # date_str est à y=230, police 32px — s'étend jusqu'à ~y=266.
    # Divider à y=275 pour laisser un espacement propre. [IMG-11]
    _draw_divider(draw, W, y=175, colors=colors)   # après masthead
    _draw_divider(draw, W, y=275, colors=colors)   # après date banner
    _draw_divider(draw, W, y=1230, colors=colors)  # avant footer
    _draw_footer(draw, W, H, source_text, fonts, colors)

    # 7. Sauvegarde JPEG
    img.save(str(output_path), "JPEG", quality=config.image.jpeg_quality, optimize=True)
    return output_path


# ---------------------------------------------------------------------------
# Story (9:16) — zones de sécurité [SPEC-7, SPEC-7.4]
# ---------------------------------------------------------------------------

# Pixels masqués par l'UI Stories (interface utilisateur Instagram/Facebook)
STORY_H = 1920
STORY_SAFE_TOP = 270   # ~250px spec + marge
STORY_SAFE_BOT = 400   # ~400px spec


def _generate_story_inner(
    source: "Event | RssArticle",
    config: "Config",
    output_path: Path,
    thumbnail: Image.Image | None = None,
) -> Path:
    """Séquence de rendu Pillow 1080×1920 pour Story. [SPEC-7, SPEC-7.1, SPEC-7.4]

    Design distinctif du feed [RF-7bis.3] :
    - Bandeau masthead en haut (petite police)
    - time_ago en hero (PlayfairDisplay 80px, accent, centré)
    - date_str centré
    - Texte condensé gauche-aligné
    - Source centré en pied

    Tout le contenu dans y=[270, 1520] (zones de sécurité Stories).
    """
    from ancnouv.db.models import RssArticle
    from ancnouv.utils.text_helpers import truncate_for_image

    W = config.image.width   # 1080
    H = STORY_H              # 1920

    safe_top = STORY_SAFE_TOP      # y=270
    safe_bot = H - STORY_SAFE_BOT  # y=1520

    # Sélection du template époque [SPEC-7bis, RF-7bis.3]
    if isinstance(source, RssArticle):
        colors = _get_template_for_year(None, config.image.force_template or "xxi")
    else:
        event_year = getattr(source, "year", None)
        colors = _get_template_for_year(event_year, config.image.force_template)

    # 1. Fond + texture
    img = Image.new("RGB", (W, H), color=colors["background"])
    if config.image.paper_texture:
        img = _draw_paper_texture(img, intensity=config.image.paper_texture_intensity)

    draw = ImageDraw.Draw(img)
    fonts = _load_fonts()

    # Police héros : PlayfairDisplay-Bold 80px (chargée à la demande — spécifique Story)
    hero_font = load_font(FONTS_DIR / "PlayfairDisplay-Bold.ttf", 80)

    # 2. Extraction du contenu source
    if isinstance(source, RssArticle):
        time_ago = "ACTUALITÉ RSS"
        date_str = _format_datetime_fr(source.published_at)
        text = truncate_for_image(
            f"{source.title}\n\n{source.summary or ''}",
            max_chars=config.stories.max_text_chars,
        )
        source_text = f"Source : {source.feed_name}"
    else:
        year = getattr(source, "year", None)
        if year is None:
            time_ago = "Date inconnue"
            date_str = "Date inconnue"
        else:
            time_ago = _compute_time_ago_int(source.year, source.month, source.day)
            date_str = _format_date_int(source.year, source.month, source.day)
        text = truncate_for_image(
            getattr(source, "description", "") or "",
            max_chars=config.stories.max_text_chars,
        )
        text = text[:1].upper() + text[1:] if text else text
        if getattr(source, "source_lang", "fr") == "en":
            source_text = config.caption.source_template_en
        else:
            source_text = config.caption.source_template_fr

    # ── Layout ──────────────────────────────────────────────────────────────
    # Bandeau masthead : deux filets encadrant le titre réduit
    _draw_divider(draw, W, safe_top + 22, colors)
    mast_font = fonts["footer"]
    mast_bbox = draw.textbbox((0, 0), config.image.masthead_text, font=mast_font)
    mast_w = mast_bbox[2] - mast_bbox[0]
    draw.text(
        ((W - mast_w) // 2, safe_top + 30),
        config.image.masthead_text,
        fill=colors["text_secondary"],
        font=mast_font,
    )
    _draw_divider(draw, W, safe_top + 62, colors)

    # Hero : time_ago en grand (centré, max 2 lignes, couleur accent)
    hero_lines = _wrap_text(draw, time_ago, hero_font, W - 2 * PADDING)[:2]
    y_hero = safe_top + 110
    for line in hero_lines:
        bbox = draw.textbbox((0, 0), line, font=hero_font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        draw.text(((W - lw) // 2, y_hero), line, fill=colors["accent"], font=hero_font)
        y_hero += lh + 12
    y_after_hero = y_hero + 20

    # Date centrée
    date_font = fonts["date_small"]
    date_bbox = draw.textbbox((0, 0), date_str, font=date_font)
    draw.text(
        ((W - (date_bbox[2] - date_bbox[0])) // 2, y_after_hero),
        date_str,
        fill=colors["text_secondary"],
        font=date_font,
    )
    y_after_date = y_after_hero + (date_bbox[3] - date_bbox[1]) + 30

    # Filet séparateur
    _draw_divider(draw, W, y_after_date, colors)

    # Thumbnail (si disponible) : letterbox entre séparateur et texte
    y_text_start = y_after_date + 20
    if thumbnail is not None:
        thumb_y = y_text_start
        _draw_thumbnail(img, thumbnail, y=thumb_y, W=W)
        y_text_start = thumb_y + 320

    # Texte condensé
    y_footer_div = safe_bot - 70
    _draw_event_text(
        draw, W, text,
        text_y=y_text_start,
        max_height=y_footer_div - y_text_start - 10,
        fonts=fonts,
        colors=colors,
    )

    # Filet + source en pied
    _draw_divider(draw, W, y_footer_div, colors)
    src_font = fonts["footer"]
    src_bbox = draw.textbbox((0, 0), source_text, font=src_font)
    draw.text(
        ((W - (src_bbox[2] - src_bbox[0])) // 2, y_footer_div + 18),
        source_text,
        fill=colors["text_secondary"],
        font=src_font,
    )

    # 3. Sauvegarde
    img.save(str(output_path), "JPEG", quality=config.image.jpeg_quality, optimize=True)
    return output_path


def generate_story_image(
    source: "Event | RssArticle",
    config: "Config",
    output_path: Path,
    thumbnail: Image.Image | None = None,
) -> Path:
    """Génère une image Story 1080×1920. [SPEC-7, SPEC-7.1]

    Wrapper : exceptions Pillow réemballées dans GeneratorError.
    """
    try:
        return _generate_story_inner(source, config, output_path, thumbnail)
    except GeneratorError:
        raise
    except Exception as exc:
        source_id = getattr(source, "id", "unknown")
        raise GeneratorError(
            f"Échec génération Story pour id={source_id} : {exc}"
        ) from exc


def generate_image(
    source: "Event | RssArticle",
    config: "Config",
    output_path: Path,
    thumbnail: Image.Image | None = None,
) -> Path:
    """Génère une image 1080×1350 style gazette vintage. [SPEC-3.2.3, IMG-18]

    Wrapper : toutes les exceptions Pillow sont réemballées dans GeneratorError.
    Appel depuis generate_post (async) : synchrone, sans await. [IMAGE_GENERATION.md]
    """
    try:
        return _generate_image_inner(source, config, output_path, thumbnail)
    except GeneratorError:
        raise
    except Exception as exc:
        source_id = getattr(source, "id", "unknown")
        raise GeneratorError(
            f"Échec génération image pour id={source_id} : {exc}"
        ) from exc


def generate_shell_image(
    source: "Event | RssArticle",
    config: "Config",
    output_path: Path,
    thumbnail: "Image.Image | None" = None,
) -> Path:
    """Génère l'image chrome sans texte contenu pour l'animation Reel [SPEC-8.3].

    Contient : fond, texture papier, bordure décorative, masthead, séparateurs,
               et la miniature si disponible (photo révélée en phase 1).
    Exclut : date banner, texte événement, footer.
    La miniature apparaît dès le fade-in — seul le texte est révélé en phase 2.
    """
    try:
        from ancnouv.db.models import RssArticle

        W = config.image.width
        H = config.image.height

        if isinstance(source, RssArticle):
            colors = _get_template_for_year(None, config.image.force_template or "xxi")
        else:
            event_year = getattr(source, "year", None)
            colors = _get_template_for_year(event_year, config.image.force_template)

        img = Image.new("RGB", (W, H), color=colors["background"])
        if config.image.paper_texture:
            img = _draw_paper_texture(img, intensity=config.image.paper_texture_intensity)

        draw = ImageDraw.Draw(img)
        fonts = _load_fonts()

        _draw_decorative_border(draw, W, H, colors)
        _draw_masthead(draw, W, config.image.masthead_text, fonts, colors)

        # Miniature dessinée dans le shell — visible dès la phase 1 du Reel
        if thumbnail is not None:
            _draw_thumbnail(img, thumbnail, y=920, W=W)

        # Séparateurs horizontaux — structure visuelle sans contenu
        _draw_divider(draw, W, y=175, colors=colors)
        _draw_divider(draw, W, y=275, colors=colors)
        _draw_divider(draw, W, y=1230, colors=colors)

        img.save(str(output_path), "JPEG", quality=config.image.jpeg_quality, optimize=True)
        return output_path
    except GeneratorError:
        raise
    except Exception as exc:
        raise GeneratorError(f"Échec génération shell image : {exc}") from exc
