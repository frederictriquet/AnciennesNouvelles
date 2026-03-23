# Commande generate-test-image [docs/CLI.md — section "generate-test-image"]
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ancnouv.config import Config


def generate_test_image(config: Config) -> int:
    """Génère une image de test et la sauvegarde dans data/test_output.jpg.

    [CLI-M9] Sur VPS headless, xdg-open échoue silencieusement — chemin affiché en stdout.
    """
    from ancnouv.generator.image import generate_image
    from datetime import date

    # Événement fictif pour le test
    class _FakeEvent:
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

    output_path = Path(config.data_dir) / "test_output.jpg"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        generated = generate_image(_FakeEvent(), config, output_path=output_path)
        print(f"Image générée : {generated}")
        # Tenter d'ouvrir dans le viewer système — silencieux si headless
        try:
            subprocess.call(["xdg-open", str(generated)])
        except Exception:
            pass
        return 0
    except Exception as exc:
        print(f"Erreur génération image : {exc}", file=sys.stderr)
        return 1
