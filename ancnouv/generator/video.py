# Génération vidéo Reels — effet Ken Burns [SPEC-8.3]
# Input : JPEG 1080×1350
# Output : MP4 H.264, 1080×1920, 30fps, 15s
# Effet : zoom progressif depuis le centre (Ken Burns via ffmpeg zoompan)
# Audio : optionnel — fichier CC0 depuis assets/audio/
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from ancnouv.exceptions import GeneratorError

logger = logging.getLogger(__name__)


async def generate_reel_video(
    image_path: Path,
    output_path: Path,
    audio_path: Path | None = None,
    duration_seconds: int = 15,
    fps: int = 30,
) -> Path:
    """Génère une vidéo Reel depuis une image JPEG (effet Ken Burns) [SPEC-8.3].

    Retourne output_path.
    Lève GeneratorError si ffmpeg échoue ou n'est pas installé.
    """
    # Vérification que ffmpeg est disponible sur le système
    if shutil.which("ffmpeg") is None:
        raise GeneratorError(
            "ffmpeg non trouvé — installer ffmpeg pour générer les Reels."
        )

    # Nombre total de frames pour la durée donnée
    d = duration_seconds * fps

    # Filtre vidéo Ken Burns : zoom progressif + padding noir pour format 9:16 [SPEC-8.3]
    # Image source 1080×1350 → padded à 1080×1920 (marges noires haut/bas)
    vf_filter = (
        f"scale=1080:1350,"
        f"zoompan=z='min(zoom+0.0015,1.5)':d={d}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1350,"
        f"pad=1080:1920:0:285:color=black,"
        f"setsar=1"
    )

    if audio_path is None:
        # Commande sans piste audio
        args = [
            "-loop", "1",
            "-i", str(image_path),
            "-vf", vf_filter,
            "-c:v", "libx264",
            "-r", str(fps),
            "-t", str(duration_seconds),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]
    else:
        # Commande avec piste audio (AAC 128k)
        args = [
            "-loop", "1",
            "-i", str(image_path),
            "-i", str(audio_path),
            "-vf", vf_filter,
            "-c:v", "libx264",
            "-c:a", "aac",
            "-b:a", "128k",
            "-r", str(fps),
            "-t", str(duration_seconds),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]

    logger.debug("ffmpeg commande : ffmpeg %s", " ".join(args))

    # Exécution asynchrone — pas de shell=True pour la sécurité [SPEC-8.3]
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        # Tronquer stderr à 500 chars pour éviter les messages trop longs
        raise GeneratorError(
            f"ffmpeg erreur (code {proc.returncode}) : {stderr.decode()[-500:]}"
        )

    logger.info("Vidéo Reel générée : %s", output_path)
    return output_path


def get_reel_output_path(image_path: Path) -> Path:
    """Retourne le chemin de sortie de la vidéo Reel basé sur l'image d'entrée."""
    return image_path.with_suffix(".reel.mp4")
