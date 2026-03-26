# Commande generate-test-image [docs/CLI.md — section "generate-test-image"]
from __future__ import annotations

import asyncio
import platform
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from ancnouv.config import Config


# ─── Sources fictives ─────────────────────────────────────────────────────────

class _FakeWikiEvent:
    """Événement Wikipedia fictif pour les tests d'image feed."""
    source = "wikipedia"
    source_lang = "fr"
    event_type = "event"
    month = date.today().month
    day = date.today().day
    year = 1871
    description = (
        "Test de génération d'image — Anciennes Nouvelles. "
        "Cet événement fictif permet de valider les polices, "
        "la texture papier et le rendu visuel."
    )
    title = None
    wikipedia_url = None
    image_url = None


class _FakeRssArticle:
    """Article RSS fictif pour les tests d'image feed Mode B."""
    # RssArticle fields attendus par generate_image
    feed_url = "https://example.com/feed.rss"
    feed_name = "Le Figaro"
    title = "Test article RSS — Anciennes Nouvelles"
    summary = (
        "Ceci est un article de test pour valider le rendu visuel "
        "du mode RSS (Mode B) dans la génération d'image."
    )
    article_url = "https://example.com/article/test"
    published_at = datetime(1920, 6, 15, 10, 0)
    image_url = None

    # Pour que isinstance(source, RssArticle) retourne True on doit
    # passer un vrai objet RssArticle — on le construit dynamiquement.


class _FakeGallicaSource:
    """Source Gallica fictive pour les tests d'image feed Mode C."""
    # GallicaArticle utilise le chemin Event dans generate_image
    # (pas un RssArticle) — on duck-type les champs Event utilisés.
    source = "gallica"
    source_lang = "fr"
    year = 1914
    month = 3
    day = 26
    description = (
        "Les Nouvelles du Nord et du Pays de Valenciennes : "
        "organe républicain indépendant. Hebdomadaire. Paris, 1914."
    )
    title = None
    image_url = None


# ─── Entrée principale ────────────────────────────────────────────────────────

def generate_test_image(
    config: Config,
    source: str = "wiki",
    story: bool = False,
    reel: bool = False,
) -> int:
    """Génère image(s) et vidéo de test selon la source choisie.

    [CLI-M9] Sur VPS headless, xdg-open/open échouent silencieusement.
    """
    from ancnouv.generator.image import generate_image, generate_story_image

    output_path = Path(config.data_dir) / "test_output.jpg"
    story_path = Path(config.data_dir) / "test_output_story.jpg"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Construction de la source fictive
    src = _build_source(config, source)
    if src is None:
        return 1

    # Génération image feed
    try:
        generated = generate_image(src, config, output_path=output_path)
        print(f"Feed  : {generated}")
        _open_file(generated)
    except Exception as exc:
        print(f"Erreur génération feed : {exc}", file=sys.stderr)
        return 1

    # Génération image Story
    if story:
        try:
            generated_story = generate_story_image(src, config, output_path=story_path)
            print(f"Story : {generated_story}")
            _open_file(generated_story)
        except Exception as exc:
            print(f"Erreur génération story : {exc}", file=sys.stderr)

    # Génération vidéo Reel
    if reel:
        return asyncio.run(_generate_test_reel(config, output_path, src))

    return 0


async def _generate_test_reel(config: Config, feed_image: Path, source) -> int:
    """Génère une vidéo Reel de test avec animation fade+reveal. [SPEC-8.3]"""
    from ancnouv.generator.video import generate_reel_video, get_reel_output_path, get_shell_output_path
    from ancnouv.generator.image import generate_shell_image

    reel_out = get_reel_output_path(feed_image)
    audio: Path | None = None
    if config.reels.audio_file:
        audio = Path(config.reels.audio_file)
        if not audio.exists():
            print(f"Audio introuvable : {audio} — sans audio", file=sys.stderr)
            audio = None

    # Générer l'image shell (fond+chrome sans texte) pour l'animation
    shell_path = get_shell_output_path(feed_image)
    shell_image_path: Path | None = None
    try:
        generate_shell_image(source, config, shell_path)
        print(f"Shell : {shell_path}")
        shell_image_path = shell_path
    except Exception as exc:
        print(f"Shell non généré (fallback sans shell) : {exc}", file=sys.stderr)

    try:
        result = await generate_reel_video(
            feed_image,
            reel_out,
            shell_image_path=shell_image_path,
            audio_path=audio,
            duration_seconds=config.reels.duration_seconds,
            fps=config.reels.fps,
        )
        size_kb = result.stat().st_size // 1024
        print(f"Reel  : {result} ({size_kb} Ko)")
        _open_file(result)
    except Exception as exc:
        print(f"Erreur génération reel : {exc}", file=sys.stderr)
        return 1
    return 0


def _build_source(config: Config, source: str):
    """Construit la source fictive selon le type demandé."""
    if source == "wiki":
        return _FakeWikiEvent()

    if source == "rss":
        # Instancier un vrai RssArticle pour que isinstance() fonctionne
        try:
            from ancnouv.db.models import RssArticle
            article = RssArticle(
                feed_url="https://example.com/feed.rss",
                feed_name="Le Figaro",
                title="Test article RSS — Anciennes Nouvelles",
                summary=(
                    "Ceci est un article de test pour valider le rendu visuel "
                    "du mode RSS (Mode B) dans la génération d'image."
                ),
                article_url="https://example.com/article/test",
                published_at=datetime(1920, 6, 15, 10, 0),
                fetched_at=datetime(1920, 6, 15, 10, 0),
            )
            return article
        except Exception as exc:
            print(f"Erreur construction source RSS : {exc}", file=sys.stderr)
            return None

    if source == "gallica":
        return _FakeGallicaSource()

    print(f"Source inconnue : {source}", file=sys.stderr)
    return None


def _open_file(path: Path) -> None:
    """Ouvre le fichier dans le viewer système — silencieux si headless."""
    try:
        if platform.system() == "Darwin":
            subprocess.call(["open", str(path)])
        else:
            subprocess.call(["xdg-open", str(path)])
    except Exception:
        pass
