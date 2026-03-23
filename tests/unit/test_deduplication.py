# Tests unitaires — déduplication [DS-13]
# Vecteurs canoniques pour compute_content_hash : NFKC → strip → lowercase → SHA-256

from ancnouv.db.utils import compute_content_hash


def test_hash_normalizes_whitespace():
    # Les espaces de début/fin sont supprimés avant le hash
    assert compute_content_hash("Napoléon ") == compute_content_hash("Napoléon")
    assert compute_content_hash("  Napoléon  ") == compute_content_hash("Napoléon")


def test_hash_normalizes_case():
    # La mise en minuscule fait partie de la normalisation
    assert compute_content_hash("NAPOLÉON") == compute_content_hash("napoléon")
    assert compute_content_hash("Napoléon") == compute_content_hash("napoléon")


def test_hash_different_text():
    # Des textes distincts produisent des hashs distincts
    assert compute_content_hash("événement A") != compute_content_hash("événement B")


def test_hash_canonical_vector():
    # "A " → NFKC + strip + lower → "a" → sha256 connu
    # sha256("a") = ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb
    assert (
        compute_content_hash("A ")
        == "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
    )


def test_hash_is_deterministic():
    # Même entrée → même hash à chaque appel
    text = "Bataille de Waterloo"
    assert compute_content_hash(text) == compute_content_hash(text)
