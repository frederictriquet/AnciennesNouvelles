# Tests d'intégration — GallicaFetcher [SPEC-9.3]
from __future__ import annotations

from datetime import date

import pytest

# ─── Fixtures XML ────────────────────────────────────────────────────────────────

_XML_ONE_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<srw:searchRetrieveResponse
    xmlns:srw="http://www.loc.gov/zing/srw/"
    xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
    xmlns:dc="http://purl.org/dc/elements/1.1/">
  <srw:records>
    <srw:record>
      <srw:recordData>
        <oai_dc:dc>
          <dc:title>Le Figaro du 15 mars 1900</dc:title>
          <dc:publisher>Le Figaro</dc:publisher>
          <dc:description>Un article sur la politique française.</dc:description>
          <dc:identifier>https://gallica.bnf.fr/ark:/12148/bpt6k123456</dc:identifier>
        </oai_dc:dc>
      </srw:recordData>
    </srw:record>
  </srw:records>
</srw:searchRetrieveResponse>"""

_XML_NO_TITLE = """<?xml version="1.0" encoding="UTF-8"?>
<srw:searchRetrieveResponse
    xmlns:srw="http://www.loc.gov/zing/srw/"
    xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
    xmlns:dc="http://purl.org/dc/elements/1.1/">
  <srw:records>
    <srw:record>
      <srw:recordData>
        <oai_dc:dc>
          <dc:title></dc:title>
          <dc:identifier>https://gallica.bnf.fr/ark:/12148/bpt6k999</dc:identifier>
        </oai_dc:dc>
      </srw:recordData>
    </srw:record>
  </srw:records>
</srw:searchRetrieveResponse>"""

_XML_NO_ARK = """<?xml version="1.0" encoding="UTF-8"?>
<srw:searchRetrieveResponse
    xmlns:srw="http://www.loc.gov/zing/srw/"
    xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
    xmlns:dc="http://purl.org/dc/elements/1.1/">
  <srw:records>
    <srw:record>
      <srw:recordData>
        <oai_dc:dc>
          <dc:title>Article sans ARK</dc:title>
          <dc:identifier>https://example.com/not-an-ark</dc:identifier>
        </oai_dc:dc>
      </srw:recordData>
    </srw:record>
  </srw:records>
</srw:searchRetrieveResponse>"""

_XML_EMPTY_RECORDS = """<?xml version="1.0" encoding="UTF-8"?>
<srw:searchRetrieveResponse xmlns:srw="http://www.loc.gov/zing/srw/">
  <srw:records/>
</srw:searchRetrieveResponse>"""

_XML_DESCRIPTION_APPARTIENT = """<?xml version="1.0" encoding="UTF-8"?>
<srw:searchRetrieveResponse
    xmlns:srw="http://www.loc.gov/zing/srw/"
    xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
    xmlns:dc="http://purl.org/dc/elements/1.1/">
  <srw:records>
    <srw:record>
      <srw:recordData>
        <oai_dc:dc>
          <dc:title>Journal officiel</dc:title>
          <dc:description>Appartient à l'ensemble documentaire : presse française</dc:description>
          <dc:description>Compte rendu des séances parlementaires.</dc:description>
          <dc:identifier>https://gallica.bnf.fr/ark:/12148/bpt6k777</dc:identifier>
        </oai_dc:dc>
      </srw:recordData>
    </srw:record>
  </srw:records>
</srw:searchRetrieveResponse>"""


# ─── Tests _parse_response ────────────────────────────────────────────────────────

def test_parse_response_valid_item():
    """XML valide avec un item complet → GallicaItem correctement extrait."""
    from ancnouv.fetchers.gallica import GallicaFetcher
    fetcher = GallicaFetcher()
    items = fetcher._parse_response(_XML_ONE_ITEM, 1900, 3, 15)
    assert len(items) == 1
    item = items[0]
    assert item.title == "Le Figaro du 15 mars 1900"
    assert item.source_name == "Le Figaro"
    assert item.description == "Un article sur la politique française."
    assert item.ark_id == "ark:/12148/bpt6k123456"
    assert item.gallica_url == "https://gallica.bnf.fr/ark:/12148/bpt6k123456"
    assert item.date_published == date(1900, 3, 15)
    assert len(item.content_hash) == 64  # SHA-256 hexdigest


def test_parse_response_no_ark_returns_empty():
    """Identifiant sans ARK Gallica → aucun item retourné."""
    from ancnouv.fetchers.gallica import GallicaFetcher
    items = GallicaFetcher()._parse_response(_XML_NO_ARK, 1900, 3, 15)
    assert items == []


def test_parse_response_empty_title_returns_empty():
    """Titre vide → item ignoré."""
    from ancnouv.fetchers.gallica import GallicaFetcher
    items = GallicaFetcher()._parse_response(_XML_NO_TITLE, 1900, 3, 15)
    assert items == []


def test_parse_response_empty_records():
    """XML sans record → liste vide."""
    from ancnouv.fetchers.gallica import GallicaFetcher
    items = GallicaFetcher()._parse_response(_XML_EMPTY_RECORDS, 1900, 3, 15)
    assert items == []


def test_parse_response_invalid_xml():
    """XML malformé → liste vide (non-fatal)."""
    from ancnouv.fetchers.gallica import GallicaFetcher
    items = GallicaFetcher()._parse_response("pas du xml", 1900, 3, 15)
    assert items == []


def test_parse_response_invalid_date():
    """29 février en année non bissextile (1900) → item ignoré."""
    from ancnouv.fetchers.gallica import GallicaFetcher
    # 1900 n'est pas bissextile (divisible par 100 mais pas par 400)
    items = GallicaFetcher()._parse_response(_XML_ONE_ITEM, 1900, 2, 29)
    assert items == []


def test_parse_response_skips_appartient_description():
    """Description commençant par 'Appartient à l' ignorée — la suivante utilisée."""
    from ancnouv.fetchers.gallica import GallicaFetcher
    items = GallicaFetcher()._parse_response(_XML_DESCRIPTION_APPARTIENT, 1900, 3, 15)
    assert len(items) == 1
    assert items[0].description == "Compte rendu des séances parlementaires."


def test_content_hash_deterministic():
    """Même ark_id → content_hash identique."""
    from ancnouv.fetchers.gallica import GallicaFetcher
    fetcher = GallicaFetcher()
    items1 = fetcher._parse_response(_XML_ONE_ITEM, 1900, 3, 15)
    items2 = fetcher._parse_response(_XML_ONE_ITEM, 1900, 3, 15)
    assert items1[0].content_hash == items2[0].content_hash


# ─── Tests fetch (HTTP mocké) ─────────────────────────────────────────────────────

async def test_fetch_returns_items(httpx_mock):
    """fetch() retourne les items parsés depuis chaque année. [SPEC-9.3]"""
    from ancnouv.fetchers.gallica import GallicaFetcher, _DEFAULT_YEARS

    for _ in _DEFAULT_YEARS:
        httpx_mock.add_response(text=_XML_ONE_ITEM)

    fetcher = GallicaFetcher()
    items = await fetcher.fetch(3, 15)
    # Tous les XML retournent le même ark_id → dédupliqué en 1 seul item
    assert len(items) == 1


async def test_fetch_deduplicates_by_ark_id(httpx_mock):
    """Même ark_id retourné par plusieurs années → un seul item [SPEC-9.3]."""
    from ancnouv.fetchers.gallica import GallicaFetcher, _DEFAULT_YEARS

    for _ in _DEFAULT_YEARS:
        httpx_mock.add_response(text=_XML_ONE_ITEM)

    items = await GallicaFetcher().fetch(3, 15)
    assert len(items) == 1


async def test_fetch_http_error_returns_empty(httpx_mock):
    """Erreur réseau → [] (non-fatal). [SPEC-9.3]"""
    from ancnouv.fetchers.gallica import GallicaFetcher, _DEFAULT_YEARS

    for _ in _DEFAULT_YEARS:
        httpx_mock.add_exception(Exception("timeout simulé"))

    items = await GallicaFetcher().fetch(3, 15)
    assert items == []


async def test_fetch_http_error_partial(httpx_mock):
    """Une erreur sur 5 requêtes → les autres retournées normalement."""
    from ancnouv.fetchers.gallica import GallicaFetcher, _DEFAULT_YEARS

    httpx_mock.add_exception(Exception("timeout"))
    for _ in _DEFAULT_YEARS[1:]:
        httpx_mock.add_response(text=_XML_EMPTY_RECORDS)

    items = await GallicaFetcher().fetch(3, 15)
    assert items == []  # XML vide pour les autres années


# ─── Tests store ─────────────────────────────────────────────────────────────────

async def test_store_inserts_items(db_session):
    """store() insère un item et retourne 1. [SPEC-9.3]"""
    from ancnouv.fetchers.gallica import GallicaFetcher

    fetcher = GallicaFetcher()
    items = fetcher._parse_response(_XML_ONE_ITEM, 1900, 3, 15)
    count = await fetcher.store(items, db_session)
    assert count == 1


async def test_store_ignores_duplicate(db_session):
    """INSERT OR IGNORE : deuxième store() du même ark_id → 0. [SPEC-9.3]"""
    from ancnouv.fetchers.gallica import GallicaFetcher

    fetcher = GallicaFetcher()
    items = fetcher._parse_response(_XML_ONE_ITEM, 1900, 3, 15)

    first = await fetcher.store(items, db_session)
    second = await fetcher.store(items, db_session)
    assert first == 1
    assert second == 0


async def test_store_empty_list_returns_zero(db_session):
    """store() avec liste vide → 0."""
    from ancnouv.fetchers.gallica import GallicaFetcher

    count = await GallicaFetcher().store([], db_session)
    assert count == 0
