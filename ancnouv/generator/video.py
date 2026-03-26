# Génération vidéo Reels — animation fade+reveal [SPEC-8.3]
# Input  : JPEG 1080×1350 (image complète) + JPEG 1080×1350 optionnel (shell)
# Output : MP4 H.264, 1080×1920, 30fps
# Animation :
#   0.0s – 0.5s  : fade in depuis le noir vers le shell (sans texte)
#   0.5s – 1.5s  : cross-dissolve shell → image complète (texte apparaît)
#   1.5s – (d-0.5s): maintien image complète
#   (d-0.5s) – d : fade out vers le noir
# Audio : optionnel — fichier CC0 depuis assets/audio/
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from ancnouv.exceptions import GeneratorError

logger = logging.getLogger(__name__)

# Durées fixes de l'animation (secondes)
_FADE_IN = 0.5
_TEXT_REVEAL = 1.0
_FADE_OUT = 0.5


async def generate_reel_video(
    image_path: Path,
    output_path: Path,
    shell_image_path: Path | None = None,
    audio_path: Path | None = None,
    duration_seconds: int = 15,
    fps: int = 30,
) -> Path:
    """Génère une vidéo Reel avec animation fade+reveal [SPEC-8.3].

    Si shell_image_path fourni : fade-in shell → cross-dissolve vers image complète → fade-out.
    Sinon : simple fade-in → maintien → fade-out.
    Retourne output_path. Lève GeneratorError si ffmpeg échoue ou est absent.
    """
    if shutil.which("ffmpeg") is None:
        raise GeneratorError(
            "ffmpeg non trouvé — installer ffmpeg pour générer les Reels."
        )

    fade_out_start = duration_seconds - _FADE_OUT

    # Padding 1080×1350 → 1080×1920 (285px haut/bas)
    scale_pad = "scale=1080:1350,pad=1080:1920:0:285:color=black,setsar=1"

    if shell_image_path is not None:
        # Expression de blend : 0 jusqu'à t=FADE_IN, puis montée linéaire sur TEXT_REVEAL
        mix = f"if(gte(T,{_FADE_IN}),min((T-{_FADE_IN})/{_TEXT_REVEAL},1),0)"
        filter_complex = (
            f"[0:v]{scale_pad}[shell];"
            f"[1:v]{scale_pad}[full];"
            f"[shell][full]blend=all_expr='A*(1-({mix}))+B*({mix})'[blended];"
            f"[blended]fade=t=in:st=0:d={_FADE_IN},"
            f"fade=t=out:st={fade_out_start}:d={_FADE_OUT}[vout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-t", str(duration_seconds), "-i", str(shell_image_path),
            "-loop", "1", "-t", str(duration_seconds), "-i", str(image_path),
            "-filter_complex", filter_complex,
            "-map", "[vout]",
        ]
        audio_input_idx = 2
    else:
        # Fallback : image unique, simple fade in/out
        vf = (
            f"{scale_pad},"
            f"fade=t=in:st=0:d={_FADE_IN},"
            f"fade=t=out:st={fade_out_start}:d={_FADE_OUT}"
        )
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-t", str(duration_seconds), "-i", str(image_path),
            "-vf", vf,
        ]
        audio_input_idx = 1

    if audio_path is not None:
        cmd += ["-i", str(audio_path), "-map", f"{audio_input_idx}:a", "-c:a", "aac", "-b:a", "128k"]

    cmd += [
        "-c:v", "libx264",
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]

    logger.debug("ffmpeg commande : %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise GeneratorError(
            f"ffmpeg erreur (code {proc.returncode}) : {stderr.decode()[-500:]}"
        )

    logger.info("Vidéo Reel générée : %s", output_path)
    return output_path


def get_reel_output_path(image_path: Path) -> Path:
    """Retourne le chemin de sortie de la vidéo Reel basé sur l'image d'entrée."""
    return image_path.with_suffix(".reel.mp4")


def get_shell_output_path(image_path: Path) -> Path:
    """Retourne le chemin de l'image shell (sans contenu) dérivé de l'image feed."""
    return image_path.with_name(image_path.stem + "_shell.jpg")
