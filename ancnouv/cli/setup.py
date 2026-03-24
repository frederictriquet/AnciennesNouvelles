# Commande setup fonts [docs/CLI.md — section "setup fonts"]
from __future__ import annotations

import sys
from pathlib import Path

# [CLI.md] URLs GitHub google/fonts — stables (pas de numéro de version).
# Playfair Display est une variable font depuis v38 ; la version statique Bold
# est dans le sous-répertoire static/.
_FONTS: list[tuple[str, str]] = [
    (
        "PlayfairDisplay-Bold.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/static/PlayfairDisplay-Bold.ttf",
    ),
    (
        "LibreBaskerville-Regular.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/librebaskerville/LibreBaskerville-Regular.ttf",
    ),
    (
        "LibreBaskerville-Italic.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/librebaskerville/LibreBaskerville-Italic.ttf",
    ),
    (
        "IMFellEnglish-Regular.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/imfellenglish/IMFellEnglish-Regular.ttf",
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
