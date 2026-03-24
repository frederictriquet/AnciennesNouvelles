# Tests unitaires — utils/text_helpers [IMAGE_GENERATION.md]
from __future__ import annotations


def test_truncate_short():
    """Texte court → retourné intact."""
    from ancnouv.utils.text_helpers import truncate
    assert truncate("Court", 100) == "Court"


def test_truncate_long():
    """Texte > max_chars → tronqué avec suffix '...'."""
    from ancnouv.utils.text_helpers import truncate
    result = truncate("a" * 20, 10)
    assert len(result) == 10
    assert result.endswith("...")


def test_truncate_custom_suffix():
    """Suffix personnalisé. [IMAGE_GENERATION.md]"""
    from ancnouv.utils.text_helpers import truncate
    result = truncate("abcdefghij", 7, suffix="…")
    assert result.endswith("…")
    assert len(result) <= 7


def test_truncate_for_image_short():
    """Texte court → retourné intact. [IMAGE_GENERATION.md]"""
    from ancnouv.utils.text_helpers import truncate_for_image
    text = "Court texte"
    assert truncate_for_image(text, 100) == text


def test_truncate_for_image_long():
    """Texte > 500 chars → tronqué au dernier mot entier. [IMAGE_GENERATION.md]"""
    from ancnouv.utils.text_helpers import truncate_for_image
    text = ("mot " * 200).strip()  # 800 chars
    result = truncate_for_image(text, 20)
    assert len(result) <= 23  # ≤ 20 + "..."
    assert result.endswith("...")


def test_clean_text_strips_control_chars():
    """Caractères de contrôle supprimés, newlines conservés. [IMAGE_GENERATION.md]"""
    from ancnouv.utils.text_helpers import clean_text
    text = "abc\x01def\nghi"
    result = clean_text(text)
    assert "\x01" not in result
    assert "\n" in result


def test_clean_text_normalizes_spaces():
    """Espaces multiples normalisés en un seul. [IMAGE_GENERATION.md]"""
    from ancnouv.utils.text_helpers import clean_text
    result = clean_text("un   deux    trois")
    assert "  " not in result
    assert "un deux trois" in result


def test_clean_text_strips_edges():
    """Espaces en début/fin supprimés. [IMAGE_GENERATION.md]"""
    from ancnouv.utils.text_helpers import clean_text
    result = clean_text("  texte  ")
    assert result == "texte"
