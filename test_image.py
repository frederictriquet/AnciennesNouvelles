#!/usr/bin/env python3
"""Test de génération d'image en local.

Modes :
  python test_image.py                         # texte court, sans thumbnail
  python test_image.py --long                  # texte long (remplit l'image)
  python test_image.py --thumbnail             # thumbnail Wikipedia par défaut
  python test_image.py --thumbnail URL         # thumbnail depuis une URL custom
  python test_image.py --fetch                 # événement réel Wikipedia du jour
  python test_image.py --rss                   # article réel Le Monde (1er du flux)
  python test_image.py --rss URL               # article réel depuis ce flux RSS
  python test_image.py --all-sources           # toutes les sources en parallèle (Wikipedia + flux RSS)
  python test_image.py --text "..."            # description personnalisée
  python test_image.py --year 1789             # année personnalisée
  python test_image.py --story                 # générer aussi l'image Story
  python test_image.py --reel                  # générer aussi la vidéo Reel

L'image est sauvegardée dans data/test_output.jpg et ouverte automatiquement.
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from datetime import date
from pathlib import Path

# Thumbnail Wikipedia par défaut pour les tests avec photo
# (portrait sombre — cas le plus défavorable pour le layout)
_DEFAULT_THUMBNAIL = (
    "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/"
    "Archbishop_Oscar_Romero.jpg/440px-Archbishop_Oscar_Romero.jpg"
)

_DEFAULT_RSS = "https://www.lemonde.fr/rss/une.xml"

# Flux RSS de test pour --all-sources
_ALL_RSS_FEEDS = [
    ("lemonde",     "https://www.lemonde.fr/rss/une.xml"),
    ("franceinfo",  "https://www.francetvinfo.fr/titres.rss"),
    ("rfi",         "https://www.rfi.fr/fr/rss"),
]

_TEXT_SHORT = (
    "Mgr Óscar Romero, archevêque de San Salvador, est assassiné "
    "d'une balle dans le cœur alors qu'il célèbre la messe dans "
    "la chapelle de l'hôpital de la Divine Providence."
)

_TEXT_LONG = (
    "En Inde, le gouvernement britannique rétablit l'impôt sur le sel, "
    "ouvrant la voie à la célèbre Marche du Sel de Gandhi en 1930. "
    "Cette taxe coloniale, qui frappait durement les populations les plus "
    "pauvres, allait devenir le symbole de la résistance non-violente "
    "contre l'Empire britannique. Gandhi parcourut 388 kilomètres à pied "
    "jusqu'à la mer d'Arabie pour ramasser symboliquement du sel, "
    "défiant ainsi la loi coloniale. Des milliers d'Indiens le suivirent, "
    "et le mouvement de désobéissance civile qui s'ensuivit contribua "
    "à ébranler les fondements de la domination britannique sur le "
    "sous-continent indien. Cet épisode reste l'un des moments les plus "
    "marquants de l'histoire du mouvement indépendantiste indien."
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--long", action="store_true", help="Texte long (14+ lignes)")
    p.add_argument(
        "--thumbnail", nargs="?", const=_DEFAULT_THUMBNAIL, metavar="URL",
        help="Ajouter un thumbnail (URL optionnelle, sinon utilise le défaut)"
    )
    p.add_argument("--fetch", action="store_true", help="Événement réel Wikipedia du jour")
    p.add_argument(
        "--rss", nargs="?", const=_DEFAULT_RSS, metavar="URL",
        help=f"Article réel depuis un flux RSS (défaut : Le Monde)"
    )
    p.add_argument("--date", metavar="JJ/MM", help="Date à utiliser (ex: 14/07), défaut: aujourd'hui")
    p.add_argument("--text", metavar="DESC", help="Description personnalisée")
    p.add_argument("--year", type=int, default=1871, help="Année de l'événement (défaut: 1871)")
    p.add_argument("--all-sources", action="store_true", help="Générer depuis toutes les sources (Wikipedia + flux RSS)")
    p.add_argument("--story", action="store_true", help="Générer aussi l'image Story")
    p.add_argument("--reel", action="store_true", help="Générer aussi la vidéo Reel")
    p.add_argument("--no-open", action="store_true", help="Ne pas ouvrir l'image après génération")
    p.add_argument("--out", default="data/test_output.jpg", help="Chemin de sortie")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    from ancnouv.config import Config
    from ancnouv.generator.image import fetch_thumbnail, generate_image

    try:
        config = Config()
    except Exception as exc:
        print(f"Erreur config : {exc}", file=sys.stderr)
        return 1

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    today = _parse_date(args.date)

    if args.all_sources:
        return await _all_sources_generate(config, output_path, args, today)

    if args.rss:
        return await _rss_and_generate(config, output_path, args)

    if args.fetch:
        return await _fetch_and_generate(config, output_path, args, today)

    # --- Mode manuel ---
    if args.text:
        description = args.text
    elif args.long:
        description = _TEXT_LONG
    else:
        description = _TEXT_SHORT

    class _FakeEvent:
        source = "wikipedia"
        source_lang = "fr"
        event_type = "event"
        month = today.month
        day = today.day
        year = args.year
        title = None
        wikipedia_url = None
        image_url = None

    _FakeEvent.description = description

    thumbnail = None
    if args.thumbnail:
        print(f"  Téléchargement thumbnail : {args.thumbnail}")
        thumbnail = await fetch_thumbnail(args.thumbnail)
        if thumbnail is None:
            print("  ⚠ Thumbnail inaccessible — génération sans photo.", file=sys.stderr)

    try:
        generated = generate_image(_FakeEvent(), config, output_path=output_path, thumbnail=thumbnail)
        print(f"Image générée : {generated}")
        await _generate_extras(args, config, _FakeEvent(), output_path, thumbnail)
        _open_image(generated, args.no_open)
        return 0
    except Exception as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


def _parse_date(date_str: str | None) -> date:
    if not date_str:
        return date.today()
    try:
        day, month = (int(p) for p in date_str.split("/"))
        return date(date.today().year, month, day)
    except (ValueError, TypeError):
        print(f"Format de date invalide : {date_str!r} — attendu JJ/MM", file=sys.stderr)
        sys.exit(1)


async def _rss_and_generate(config, output_path: Path, args: argparse.Namespace) -> int:
    """Génère depuis le premier article d'un flux RSS réel."""
    import feedparser
    from datetime import datetime, timezone
    from ancnouv.db.models import RssArticle
    from ancnouv.generator.image import fetch_thumbnail, generate_image

    url = args.rss
    print(f"  Collecte RSS : {url}")
    feed = feedparser.parse(url)

    if not feed.entries:
        print("  Aucun article dans ce flux.", file=sys.stderr)
        return 1

    entry = feed.entries[0]
    title = entry.get("title", "").strip()
    summary = entry.get("summary", "").strip()
    feed_name = feed.feed.get("title", url)
    print(f"  Flux        : {feed_name}")
    print(f"  Article     : {title[:80]}")

    # Extraction image (même logique que le fetcher)
    image_url = None
    if entry.get("media_thumbnail"):
        image_url = entry.media_thumbnail[0].get("url") or entry.media_thumbnail[0].get("href")
    if not image_url and entry.get("media_content"):
        image_url = entry.media_content[0].get("url") or entry.media_content[0].get("href")
    if not image_url and entry.get("enclosures"):
        enc = entry.enclosures[0]
        if enc.get("type", "").startswith("image/"):
            image_url = enc.get("href") or enc.get("url")

    print(f"  Image URL   : {(image_url or 'aucune')[:80]}")

    # Parsing published_at (même logique que le fetcher)
    if entry.get("published_parsed"):
        published_at = datetime(*entry["published_parsed"][:6], tzinfo=timezone.utc)
    else:
        published_at = datetime.now(timezone.utc)

    # Construire un RssArticle transient (non persisté) pour que isinstance() passe
    article = RssArticle(
        feed_url=url,
        feed_name=feed_name,
        title=title,
        summary=summary or None,
        article_url=entry.get("link", ""),
        image_url=image_url or None,
        published_at=published_at,
        fetched_at=datetime.now(timezone.utc),
        status="available",
        published_count=0,
    )

    thumbnail = await fetch_thumbnail(image_url) if image_url else None
    print(f"  Thumbnail   : {'OK' if thumbnail else 'absent'}")

    try:
        generated = generate_image(article, config, output_path=output_path, thumbnail=thumbnail)
        print(f"Image générée : {generated}")
        await _generate_extras(args, config, article, output_path, thumbnail)
        _open_image(generated, args.no_open)
        return 0
    except Exception as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


async def _fetch_and_generate(config, output_path: Path, args: argparse.Namespace, today: date) -> int:
    from ancnouv.fetchers.wikipedia import WikipediaFetcher
    from ancnouv.generator.image import fetch_thumbnail, generate_image

    print(f"  Collecte Wikipedia pour le {today.day:02d}/{today.month:02d}...")

    fetcher = WikipediaFetcher(config)
    try:
        items = await fetcher.fetch(today)
    except Exception as exc:
        print(f"  Erreur fetch Wikipedia : {exc}", file=sys.stderr)
        return 1

    if not items:
        print("  Aucun événement Wikipedia trouvé pour aujourd'hui.", file=sys.stderr)
        return 1

    item = items[0]
    print(f"  Événement : {item.year} — {item.description[:80]}...")

    thumbnail = None
    if item.image_url:
        print(f"  Thumbnail : {item.image_url[:60]}...")
        thumbnail = await fetch_thumbnail(item.image_url)
        if thumbnail is None:
            print("  ⚠ Thumbnail inaccessible.", file=sys.stderr)

    class _RealEvent:
        source = "wikipedia"
        source_lang = item.lang if hasattr(item, "lang") else "fr"
        event_type = getattr(item, "event_type", "event")
        month = today.month
        day = today.day
        year = item.year
        description = item.description
        title = getattr(item, "title", None)
        wikipedia_url = getattr(item, "url", None)
        image_url = item.image_url

    try:
        generated = generate_image(_RealEvent(), config, output_path=output_path, thumbnail=thumbnail)
        print(f"Image générée : {generated}")
        await _generate_extras(args, config, _RealEvent(), output_path, thumbnail)
        _open_image(generated, args.no_open)
        return 0
    except Exception as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


async def _all_sources_generate(config, output_path: Path, args: argparse.Namespace, today: date) -> int:
    """Génère une image depuis chaque source (Wikipedia + tous les flux RSS de test)."""
    generated_files: list[Path] = []
    base = output_path.parent

    # --- Wikipedia ---
    print("\n=== Wikipedia ===")
    wiki_path = base / "test_wiki.jpg"
    wiki_args = argparse.Namespace(**vars(args))
    wiki_args.all_sources = False
    rc = await _fetch_and_generate(config, wiki_path, wiki_args, today)
    if rc == 0:
        generated_files.append(wiki_path)

    # --- Flux RSS ---
    for slug, url in _ALL_RSS_FEEDS:
        print(f"\n=== RSS : {slug} ===")
        rss_path = base / f"test_{slug}.jpg"
        rss_args = argparse.Namespace(**vars(args))
        rss_args.all_sources = False
        rss_args.rss = url
        rc = await _rss_and_generate(config, rss_path, rss_args)
        if rc == 0:
            generated_files.append(rss_path)

    print(f"\n{len(generated_files)} image(s) générée(s).")
    for f in generated_files:
        _open_image(f, args.no_open)
    return 0 if generated_files else 1


async def _generate_extras(args, config, source, output_path: Path, thumbnail) -> None:
    """Génère Story et/ou Reel si demandé."""
    import random
    from ancnouv.generator.image import generate_story_image, generate_shell_image
    from ancnouv.generator.video import generate_reel_video, get_reel_output_path, get_shell_output_path

    story_path = output_path.with_name(output_path.stem + "_story.jpg")
    shell_path = get_shell_output_path(output_path)
    reel_path = get_reel_output_path(output_path)

    if args.story or args.reel:
        try:
            generate_story_image(source, config, story_path, thumbnail=thumbnail)
            print(f"Story générée : {story_path}")
        except Exception as exc:
            print(f"Erreur Story : {exc}", file=sys.stderr)

    if args.reel:
        try:
            generate_shell_image(source, config, shell_path, thumbnail=thumbnail)
            audio_dir = Path("assets/audio")
            candidates = list(audio_dir.glob("*.mp3")) if audio_dir.exists() else []
            audio = random.choice(candidates) if candidates else None
            await generate_reel_video(
                output_path, reel_path,
                shell_image_path=shell_path,
                audio_path=audio,
                duration_seconds=config.reels.duration_seconds,
                fps=config.reels.fps,
            )
            print(f"Reel générée  : {reel_path}")
        except Exception as exc:
            print(f"Erreur Reel : {exc}", file=sys.stderr)


def _open_image(path: Path, skip: bool) -> None:
    if skip:
        return
    for cmd in (["open", str(path)], ["xdg-open", str(path)]):
        try:
            subprocess.call(cmd, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(asyncio.run(_run(args)))
