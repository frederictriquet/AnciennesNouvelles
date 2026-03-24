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

# Palette de couleurs style gazette vintage [IMAGE_GENERATION.md]
COLORS: dict[str, str] = {
    "background":      "#F2E8D5",
    "background_dark": "#E8D9BC",
    "text_primary":    "#1A1008",
    "text_secondary":  "#4A3728",
    "accent":          "#8B2020",
    "border":          "#2C1810",
    "divider":         "#6B4C3B",
}

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


def _draw_decorative_border(draw: ImageDraw.ImageDraw, W: int, H: int) -> None:
    """Rectangle décoratif épaisseur 4px, couleur border. [IMAGE_GENERATION.md]"""
    draw.rectangle(
        [(MARGIN, MARGIN), (W - MARGIN, H - MARGIN)],
        outline=COLORS["border"],
        width=4,
    )


def _draw_masthead(
    draw: ImageDraw.ImageDraw, W: int, masthead_text: str, fonts: dict
) -> None:
    """Masthead centré dans la zone y=60–170. [IMG-10, IMG-4]

    [IMG-10] Centrage horizontal via draw.textbbox — plus précis que anchor='mt'.
    """
    font = fonts["masthead"]
    bbox = draw.textbbox((0, 0), masthead_text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (W - text_w) // 2
    y = 115 - text_h // 2  # Centré dans zone y=60–170 (centre=115)
    draw.text((x, y), masthead_text, fill=COLORS["accent"], font=font)


def _draw_date_banner(
    draw: ImageDraw.ImageDraw, W: int, time_ago: str, date_str: str, fonts: dict
) -> None:
    """Banner date : two lignes centrées dans zone y=180–260. [IMG-11]

    time_ago  : y=185, police date_large (40px)
    date_str  : y=230, police date_small (32px)
    [IMG-11] Les positions y=185 et y=230 garantissent l'absence de chevauchement.
    """
    for text, y, font_key in (
        (time_ago, 185, "date_large"),
        (date_str, 230, "date_small"),
    ):
        font = fonts[font_key]
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        x = (W - text_w) // 2
        draw.text((x, y), text, fill=COLORS["text_secondary"], font=font)


def _draw_divider(draw: ImageDraw.ImageDraw, W: int, y: int) -> None:
    """Filet horizontal divider. [IMAGE_GENERATION.md]"""
    draw.line([(PADDING, y), (W - PADDING, y)], fill=COLORS["divider"], width=1)


def _draw_thumbnail(
    img: Image.Image, thumbnail: Image.Image, y: int, W: int
) -> None:
    """Colle le thumbnail dans la zone W_INNER×300px à partir de y. [IMAGE_GENERATION.md]

    Traitement selon ratio :
    - Paysage (> 1.5) : letterbox centré verticalement
    - Portrait (< 0.7) : crop centré (fit en largeur, crop en hauteur)
    - Carré (0.7–1.5)  : resize direct (distorsion intentionnelle documentée)
    """
    TARGET_W = W - 2 * PADDING  # = W_INNER pour W=1080
    TARGET_H = 300

    orig_w, orig_h = thumbnail.size
    if orig_h == 0:
        return
    ratio = orig_w / orig_h

    if ratio > 1.5:
        # Letterbox : fit en largeur, bandes fond papier
        new_h = int(TARGET_W / ratio)
        resized = thumbnail.resize((TARGET_W, new_h), Image.LANCZOS)
        bg = Image.new("RGB", (TARGET_W, TARGET_H), color=COLORS["background"])
        y_offset = (TARGET_H - new_h) // 2
        bg.paste(resized, (0, max(0, y_offset)))
        img.paste(bg, (PADDING, y))
    elif ratio < 0.7:
        # Crop centré : fit en largeur [IMAGE_GENERATION.md — invariant new_h >= TARGET_H]
        new_w = TARGET_W
        new_h = int(TARGET_W / ratio)
        resized = thumbnail.resize((new_w, new_h), Image.LANCZOS)
        y_crop = (new_h - TARGET_H) // 2
        cropped = resized.crop((0, y_crop, TARGET_W, y_crop + TARGET_H))
        img.paste(cropped, (PADDING, y))
    else:
        # Carré : resize direct, distorsion intentionnelle documentée [IMAGE_GENERATION.md]
        resized = thumbnail.resize((TARGET_W, TARGET_H), Image.LANCZOS)
        img.paste(resized, (PADDING, y))


def _draw_event_text(
    draw: ImageDraw.ImageDraw,
    W: int,
    text: str,
    text_y: int,
    max_height: int,
    fonts: dict,
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
        draw.text((PADDING, y), line, fill=COLORS["text_primary"], font=font)
        y += line_height


def _draw_footer(
    draw: ImageDraw.ImageDraw, W: int, H: int, source_text: str, fonts: dict
) -> None:
    """Footer centré à y = H - 60 (adaptatif aux dimensions). [IMG-3]

    Zone footer : y=1250–1330. Position texte : y=1290 = H - 60.
    """
    font = fonts["footer"]
    bbox = draw.textbbox((0, 0), source_text, font=font)
    text_w = bbox[2] - bbox[0]
    x = (W - text_w) // 2
    y = H - 60  # = 1290 pour H=1350
    draw.text((x, y), source_text, fill=COLORS["text_secondary"], font=font)


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
    """
    if not image_url:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(image_url, timeout=5.0)
        if r.status_code != 200:
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
    """Séquence de rendu Pillow. [IMAGE_GENERATION.md — IMG-8]"""
    from ancnouv.db.models import RssArticle
    from ancnouv.utils.text_helpers import truncate_for_image

    W = config.image.width
    H = config.image.height

    # 1. Image de fond
    img = Image.new("RGB", (W, H), color=COLORS["background"])

    # 2. Texture papier [IMG-m7] réaffectation obligatoire
    if config.image.paper_texture:
        img = _draw_paper_texture(img, intensity=config.image.paper_texture_intensity)

    draw = ImageDraw.Draw(img)
    fonts = _load_fonts()

    # 3. Bordure décorative + masthead
    _draw_decorative_border(draw, W, H)
    _draw_masthead(draw, W, config.image.masthead_text, fonts)

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
        text = truncate_for_image(getattr(source, "description", "") or "")
        if getattr(source, "source_lang", "fr") == "en":
            source_text = config.caption.source_template_en
        else:
            source_text = config.caption.source_template_fr

    _draw_date_banner(draw, W, time_ago, date_str, fonts)

    # 5. Thumbnail + texte événement
    # Layout avec thumbnail : texte d'abord (teaser), puis photo en bas. [IMG-11]
    # Évite le pattern "une ligne + grande photo noire" pour les descriptions courtes.
    if thumbnail is not None:
        _draw_event_text(draw, W, text, text_y=285, max_height=588, fonts=fonts)
        _draw_thumbnail(img, thumbnail, y=920, W=W)
    else:
        _draw_event_text(draw, W, text, text_y=285, max_height=915, fonts=fonts)

    # 6. Séparateurs + footer (trois dividers selon la spec [IMAGE_GENERATION.md])
    # date_str est à y=230, police 32px — s'étend jusqu'à ~y=266.
    # Divider à y=275 pour laisser un espacement propre. [IMG-11]
    _draw_divider(draw, W, y=175)   # après masthead
    _draw_divider(draw, W, y=275)   # après date banner
    _draw_divider(draw, W, y=1230)  # avant footer
    _draw_footer(draw, W, H, source_text, fonts)

    # 7. Sauvegarde JPEG
    img.save(str(output_path), "JPEG", quality=config.image.jpeg_quality, optimize=True)
    return output_path


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
