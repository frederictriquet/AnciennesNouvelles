# Commandes setup fonts / setup audio [docs/CLI.md]
from __future__ import annotations

import sys
from pathlib import Path

# [CLI.md] URLs GitHub google/fonts — stables (pas de numéro de version).
# Playfair Display et Libre Baskerville sont des variable fonts dans le dépôt
# google/fonts ; téléchargées sous les noms attendus par image.py.
# IM Fell English conserve ses fichiers statiques (renommés côté source).
_FONTS: list[tuple[str, str]] = [
    (
        "PlayfairDisplay-Bold.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
    ),
    (
        "LibreBaskerville-Regular.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/librebaskerville/LibreBaskerville%5Bwght%5D.ttf",
    ),
    (
        "LibreBaskerville-Italic.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/librebaskerville/LibreBaskerville-Italic%5Bwght%5D.ttf",
    ),
    (
        "IMFellEnglish-Regular.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/imfellenglish/IMFeENrm28P.ttf",
    ),
]

_FONTS_DIR = Path("assets") / "fonts"


def download_fonts() -> int:
    """Télécharge les polices manquantes dans assets/fonts/.

    Idempotent : si la police existe déjà, pas de re-téléchargement.
    Écriture atomique : fichier temp → déplacement. [IMG-m6]
    """
    import httpx

    _FONTS_DIR.mkdir(parents=True, exist_ok=True)
    errors = 0

    for filename, url in _FONTS:
        dest = _FONTS_DIR / filename
        if dest.exists():
            print(f"  ✓ {filename} (déjà présent)")
            continue

        print(f"  ↓ Téléchargement {filename}...")
        tmp = dest.with_suffix(".tmp")
        try:
            with httpx.Client(follow_redirects=True, timeout=30) as client:
                response = client.get(url)
                response.raise_for_status()
            tmp.write_bytes(response.content)
            tmp.rename(dest)
            print(f"  ✓ {filename}")
        except Exception as exc:
            print(f"  ✗ {filename} : {exc}", file=sys.stderr)
            if tmp.exists():
                tmp.unlink()
            errors += 1

    if errors:
        print(f"\n{errors} police(s) n'ont pas pu être téléchargées.", file=sys.stderr)
        return 1

    print("\nToutes les polices sont présentes.")
    return 0


# Fichiers audio CC0 pour les Reels [SPEC-8.3] — enregistrements du domaine public
# Source : Internet Archive (archive.org), licence CC0 1.0 Universal.
# Noms locaux : descriptifs pour faciliter l'identification.
_AUDIO: list[tuple[str, str]] = [
    (
        "souvenir_drdla_1917.mp3",
        "https://archive.org/download/souvenir_202505/Souvenir.mp3",
    ),
    (
        "herd_girls_dream_labitzky_1911.mp3",
        "https://archive.org/download/the-herd-girls-dream/The%20Herd%20Girl%27s%20Dream.mp3",
    ),
    (
        "piano_sonata_larsen.mp3",
        "https://archive.org/download/piano-sonata-no-7-scene-vi/PianoSonataNo7SceneVI.mp3",
    ),
]

_AUDIO_DIR = Path("assets") / "audio"


def download_audio() -> int:
    """Télécharge les fichiers audio CC0 manquants dans assets/audio/.

    Idempotent : si le fichier existe déjà, pas de re-téléchargement.
    Écriture atomique : fichier temp → déplacement.
    Les fichiers sont des enregistrements historiques en domaine public (CC0 1.0).
    Utilisés automatiquement lors de la génération des Reels [SPEC-8.3].
    """
    import httpx

    _AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    errors = 0

    for filename, url in _AUDIO:
        dest = _AUDIO_DIR / filename
        if dest.exists():
            print(f"  ✓ {filename} (déjà présent)")
            continue

        print(f"  ↓ Téléchargement {filename}...")
        tmp = dest.with_suffix(".tmp")
        try:
            with httpx.Client(follow_redirects=True, timeout=60) as client:
                response = client.get(url)
                response.raise_for_status()
            tmp.write_bytes(response.content)
            tmp.rename(dest)
            print(f"  ✓ {filename}")
        except Exception as exc:
            print(f"  ✗ {filename} : {exc}", file=sys.stderr)
            if tmp.exists():
                tmp.unlink()
            errors += 1

    if errors:
        print(f"\n{errors} fichier(s) audio n'ont pas pu être téléchargés.", file=sys.stderr)
        return 1

    print("\nTous les fichiers audio sont présents.")
    return 0
