# Tests unitaires — génération vidéo Reels [SPEC-8.3]
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ancnouv.exceptions import GeneratorError


def test_get_reel_output_path():
    """image.jpg → image.reel.mp4."""
    from ancnouv.generator.video import get_reel_output_path
    assert get_reel_output_path(Path("/data/abc.jpg")) == Path("/data/abc.reel.mp4")


def test_get_shell_output_path():
    """image.jpg → image_shell.jpg."""
    from ancnouv.generator.video import get_shell_output_path
    assert get_shell_output_path(Path("/data/abc.jpg")) == Path("/data/abc_shell.jpg")


async def test_generate_reel_video_no_ffmpeg(tmp_path):
    """ffmpeg absent → GeneratorError. [SPEC-8.3]"""
    from ancnouv.generator.video import generate_reel_video

    img = tmp_path / "test.jpg"
    img.write_bytes(b"fake")

    with patch("shutil.which", return_value=None):
        with pytest.raises(GeneratorError, match="ffmpeg"):
            await generate_reel_video(img, tmp_path / "out.mp4")


async def test_generate_reel_video_ffmpeg_error(tmp_path):
    """ffmpeg retourne code != 0 → GeneratorError avec stderr. [SPEC-8.3]"""
    from ancnouv.generator.video import generate_reel_video

    img = tmp_path / "test.jpg"
    img.write_bytes(b"fake")

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"ffmpeg: invalid option"))

    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            with pytest.raises(GeneratorError, match="ffmpeg erreur"):
                await generate_reel_video(img, tmp_path / "out.mp4")


async def test_generate_reel_video_success_simple(tmp_path):
    """Image seule (sans shell) → commande avec -vf, retourne output_path."""
    from ancnouv.generator.video import generate_reel_video

    img = tmp_path / "test.jpg"
    img.write_bytes(b"fake")
    out = tmp_path / "out.mp4"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as mock_exec:
            result = await generate_reel_video(img, out)

    assert result == out
    cmd = mock_exec.call_args[0]
    assert "-vf" in cmd
    assert "-filter_complex" not in cmd


async def test_generate_reel_video_with_shell(tmp_path):
    """Avec shell_image_path → -filter_complex dans la commande (blend). [SPEC-8.3]"""
    from ancnouv.generator.video import generate_reel_video

    img = tmp_path / "test.jpg"
    shell = tmp_path / "shell.jpg"
    img.write_bytes(b"fake")
    shell.write_bytes(b"fake")
    out = tmp_path / "out.mp4"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as mock_exec:
            result = await generate_reel_video(img, out, shell_image_path=shell)

    assert result == out
    cmd = mock_exec.call_args[0]
    assert "-filter_complex" in cmd
    assert "-vf" not in cmd


async def test_generate_reel_video_with_audio(tmp_path):
    """Avec audio_path → -c:a aac -b:a 128k dans la commande. [SPEC-8.3]"""
    from ancnouv.generator.video import generate_reel_video

    img = tmp_path / "test.jpg"
    audio = tmp_path / "music.mp3"
    img.write_bytes(b"fake")
    audio.write_bytes(b"fake audio")
    out = tmp_path / "out.mp4"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as mock_exec:
            await generate_reel_video(img, out, audio_path=audio)

    cmd = mock_exec.call_args[0]
    assert "-c:a" in cmd
    assert "aac" in cmd
    assert "128k" in cmd


async def test_generate_reel_video_custom_duration(tmp_path):
    """duration_seconds personnalisé → -t correspondant dans la commande."""
    from ancnouv.generator.video import generate_reel_video

    img = tmp_path / "test.jpg"
    img.write_bytes(b"fake")
    out = tmp_path / "out.mp4"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as mock_exec:
            await generate_reel_video(img, out, duration_seconds=20)

    cmd = mock_exec.call_args[0]
    assert "20" in cmd
