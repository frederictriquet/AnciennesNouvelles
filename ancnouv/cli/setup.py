# Commande setup fonts [docs/CLI.md — section "setup fonts"]
from __future__ import annotations

import sys
from pathlib import Path

# [CLI.md] URLs statiques Google Fonts (pas les variable fonts — incompatibles Pillow)
_FONTS: list[tuple[str, str]] = [
    (
        "PlayfairDisplay-Bold.ttf",
        "https://fonts.gstatic.com/s/playfairdisplay/v37/nuFiD-vYSZviVYUb_rj3ij__anPXJzDwcbmjWBN2PKdFvUDQ.ttf",
    ),
    (
        "LibreBaskerville-Regular.ttf",
        "https://fonts.gstatic.com/s/librebaskerville/v14/kmKnZrc3Hgbbcjq75U4uslyuy4kn0qNcaxYaDcs.ttf",
    ),
    (
        "LibreBaskerville-Italic.ttf",
        "https://fonts.gstatic.com/s/librebaskerville/v14/kmKhZrc3Hgbbcjq75U4uslyuy4kn0qNXaxMaDg.ttf",
    ),
    (
        "IMFellEnglish-Regular.ttf",
        "https://fonts.gstatic.com/s/imfellenglish/v17/Ktk1ALSLW8zDe0rthJysWo9SN7LhKbMXUQ.ttf",
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
