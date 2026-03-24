#!/usr/bin/env python3
"""Test de génération d'image en local.

Modes :
  python test_image.py                    # texte court, sans thumbnail
  python test_image.py --long             # texte long (remplit l'image)
  python test_image.py --thumbnail        # thumbnail Wikipedia par défaut
  python test_image.py --thumbnail URL    # thumbnail depuis une URL custom
  python test_image.py --fetch            # événement réel Wikipedia du jour
  python test_image.py --text "..."       # description personnalisée
  python test_image.py --year 1789        # année personnalisée

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
    p.add_argument("--date", metavar="JJ/MM", help="Date à utiliser (ex: 14/07), défaut: aujourd'hui")
    p.add_argument("--text", metavar="DESC", help="Description personnalisée")
    p.add_argument("--year", type=int, default=1871, help="Année de l'événement (défaut: 1871)")
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

    # --- Mode --fetch : événement Wikipedia réel ---
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
        _open_image(generated, args.no_open)
        return 0
    except Exception as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


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
