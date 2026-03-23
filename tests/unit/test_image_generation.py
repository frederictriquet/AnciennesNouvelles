# Tests unitaires — génération d'images [SPEC-2.3, docs/IMAGE_GENERATION.md]
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image


@pytest.fixture
def _event_mock():
    ev = MagicMock()
    ev.year = 1871
    ev.month = 3
    ev.day = 18
    ev.description = "La Commune de Paris est proclamée le 18 mars 1871."
    ev.source_lang = "fr"
    ev.image_url = None
    return ev


@pytest.fixture
def _config_mock():
    config = MagicMock()
    config.image.width = 1080
    config.image.height = 1350
    config.image.jpeg_quality = 85
    config.image.paper_texture = False
    config.image.paper_texture_intensity = 8
    config.image.masthead_text = "TEST JOURNAL"
    config.caption.source_template_fr = "Source : Wikipédia"
    config.caption.source_template_en = "Source : Wikipedia (EN)"
    return config


# ─── generate_image ──────────────────────────────────────────────────────────────

def test_generate_image_creates_file(_event_mock, _config_mock):
    """Image générée : fichier créé et non vide. [TESTING.md]"""
    from ancnouv.generator.image import generate_image

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "test_output.jpg"
        generate_image(_event_mock, _config_mock, output, thumbnail=None)
        assert output.exists()
        assert output.stat().st_size > 0


def test_generate_image_correct_dimensions(_event_mock, _config_mock):
    """Image générée : dimensions exactes 1080×1350 px. [TESTING.md]"""
    from ancnouv.generator.image import generate_image

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "test_dims.jpg"
        generate_image(_event_mock, _config_mock, output, thumbnail=None)
        img = Image.open(output)
        assert img.size == (1080, 1350)


def test_generate_image_valid_jpeg(_event_mock, _config_mock):
    """Image générée : format JPEG valide. [TESTING.md]"""
    from ancnouv.generator.image import generate_image

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "test_jpeg.jpg"
        generate_image(_event_mock, _config_mock, output, thumbnail=None)
        img = Image.open(output)
        assert img.format == "JPEG"


def test_generate_image_with_thumbnail(_event_mock, _config_mock):
    """Appel avec thumbnail PIL 320×213 px : pas d'exception, format 1080×1350. [TESTING.md]"""
    from ancnouv.generator.image import generate_image

    thumbnail = Image.new("RGB", (320, 213), color=(200, 100, 50))

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "test_thumb.jpg"
        generate_image(_event_mock, _config_mock, output, thumbnail=thumbnail)
        img = Image.open(output)
        assert img.size == (1080, 1350)


def test_generate_image_no_thumbnail(_event_mock, _config_mock):
    """Appel sans thumbnail : pas d'exception. [SPEC-3.2.4]"""
    from ancnouv.generator.image import generate_image

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "test_no_thumb.jpg"
        generate_image(_event_mock, _config_mock, output, thumbnail=None)
        assert output.exists()


# ─── fetch_thumbnail ─────────────────────────────────────────────────────────────

async def test_fetch_thumbnail_none():
    """fetch_thumbnail(None) → None sans exception. [TESTING.md]"""
    from ancnouv.generator.image import fetch_thumbnail
    result = await fetch_thumbnail(None)
    assert result is None


async def test_fetch_thumbnail_404(httpx_mock):
    """URL avec réponse HTTP 404 → None sans exception. [TESTING.md]"""
    from ancnouv.generator.image import fetch_thumbnail

    httpx_mock.add_response(
        url="https://example.com/image.jpg",
        status_code=404,
    )
    result = await fetch_thumbnail("https://example.com/image.jpg")
    assert result is None
