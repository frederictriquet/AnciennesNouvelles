# Fetcher BnF Gallica (Mode C) [SPEC-9.3]
# API SRU: https://gallica.bnf.fr/SRU
# Interroge les archives de presse numérisées pour un jour/mois donné
# Format de réponse: XML Dublin Core
from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_SRU_BASE = "https://gallica.bnf.fr/SRU"

# Espaces de noms XML Dublin Core utilisés par Gallica
_NS = {
    "srw": "http://www.loc.gov/zing/srw/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# Années historiques sélectionnées — variété éditoriale [SPEC-9.3]
_DEFAULT_YEARS = [1900, 1914, 1925, 1938, 1950]


@dataclass
class GallicaItem:
    """Représente un article de presse numérisé BnF Gallica [SPEC-9.3]."""

    ark_id: str
    title: str
    description: str | None
    source_name: str | None
    date_published: date
    gallica_url: str
    content_hash: str


class GallicaFetcher:
    """Collecte les fascicules numérisés depuis l'API SRU de BnF Gallica [SPEC-9.3].

    Effectue au plus 5 requêtes HTTP (une par année de _DEFAULT_YEARS).
    Non-fatal : retourne une liste vide en cas d'erreur réseau ou serveur.
    """

    def __init__(self, max_results: int = 10) -> None:
        # Nombre max de résultats par requête SRU (maximumRecords)
        self._max_per_query = min(3, max_results)

    async def fetch(self, month: int, day: int) -> list[GallicaItem]:
        """Collecte les fascicules Gallica pour le jour/mois donné [SPEC-9.3].

        Interroge _DEFAULT_YEARS (5 années) — une requête HTTP par année.
        Déduplique par ark_id avant de retourner.
        Retourne [] si toutes les requêtes échouent (non-fatal).
        """
        items: list[GallicaItem] = []
        seen_arks: set[str] = set()

        for year in _DEFAULT_YEARS:
            year_items = await self._fetch_year(month, day, year)
            for item in year_items:
                if item.ark_id not in seen_arks:
                    seen_arks.add(item.ark_id)
                    items.append(item)

        logger.debug(
            "Gallica : %d fascicules collectés pour le %02d-%02d.",
            len(items),
            month,
            day,
        )
        return items

    async def _fetch_year(self, month: int, day: int, year: int) -> list[GallicaItem]:
        """Requête SRU pour une année spécifique. Retourne [] en cas d'erreur."""
        date_str = f"{year:04d}-{month:02d}-{day:02d}"
        query = (
            f'dc.type all "fascicule" and dc.date all "{date_str}"'
        )
        params = {
            "operation": "searchRetrieve",
            "version": "1.2",
            "query": query,
            "maximumRecords": str(self._max_per_query),
            "startRecord": "1",
        }

        logger.debug("Gallica SRU requête — date : %s", date_str)

        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; AnciennesNouvelles/1.0)"}
            async with httpx.AsyncClient(headers=headers) as client:
                response = await client.get(_SRU_BASE, params=params, timeout=15)
            response.raise_for_status()
            return self._parse_response(response.text, year, month, day)
        except Exception as exc:
            logger.error("Erreur Gallica SRU pour %s : %s", date_str, exc)
            return []

    def _parse_response(
        self, xml_text: str, year: int, month: int, day: int
    ) -> list[GallicaItem]:
        """Parse la réponse XML SRU et extrait les GallicaItem [SPEC-9.3]."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("Gallica : impossible de parser le XML : %s", exc)
            return []

        items: list[GallicaItem] = []

        # Parcours des <record> dans la réponse SRU
        for record in root.findall(".//srw:record", _NS):
            record_data = record.find(".//oai_dc:dc", _NS)
            if record_data is None:
                continue

            item = self._parse_record(record_data, year, month, day)
            if item is not None:
                items.append(item)

        return items

    def _parse_record(
        self,
        dc: ET.Element,
        year: int,
        month: int,
        day: int,
    ) -> GallicaItem | None:
        """Extrait un GallicaItem depuis un élément oai_dc:dc.

        Retourne None si aucun identifiant ARK Gallica n'est trouvé.
        Gallica retourne les ARK sous forme d'URL complètes :
        https://gallica.bnf.fr/ark:/12148/...
        """
        # Extraction des champs Dublin Core
        title_el = dc.find("dc:title", _NS)
        publisher_el = dc.find("dc:publisher", _NS)

        # [SPEC-9.3] Recherche du premier identifiant ARK parmi tous les dc:identifier
        # Gallica retourne des URLs complètes : https://gallica.bnf.fr/ark:/12148/...
        gallica_url: str | None = None
        ark_id: str | None = None
        for id_el in dc.findall("dc:identifier", _NS):
            val = (id_el.text or "").strip()
            if "gallica.bnf.fr/ark:" in val:
                gallica_url = val
                # Extraire la partie ark:/ depuis l'URL
                ark_start = val.find("ark:/")
                ark_id = val[ark_start:] if ark_start != -1 else None
                break

        if ark_id is None or gallica_url is None:
            return None

        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            return None

        # Description tronquée à 500 chars — on cherche la première description
        # non bibliographique (les notices BnF contiennent souvent
        # "Appartient à l'ensemble documentaire : ..." en premier).
        description: str | None = None
        for desc_el in dc.findall("dc:description", _NS):
            text_val = (desc_el.text or "").strip()
            if text_val and not text_val.startswith("Appartient à l"):
                description = text_val[:500]
                break

        source_name: str | None = None
        if publisher_el is not None and publisher_el.text:
            source_name = publisher_el.text.strip()

        # content_hash = SHA-256 de l'ark_id normalisé [SPEC-9.3]
        content_hash = hashlib.sha256(ark_id.strip().lower().encode()).hexdigest()

        try:
            date_published = date(year, month, day)
        except ValueError:
            # Combinaison jour/mois invalide pour cette année (ex: 29 février)
            return None

        return GallicaItem(
            ark_id=ark_id,
            title=title,
            description=description,
            source_name=source_name,
            date_published=date_published,
            gallica_url=gallica_url,
            content_hash=content_hash,
        )

    async def store(self, items: list[GallicaItem], session: AsyncSession) -> int:
        """Stocke les items en table gallica_articles (INSERT OR IGNORE) [SPEC-9.3].

        Retourne le nombre de nouveaux enregistrements insérés.
        """
        inserted = 0

        for item in items:
            result = await session.execute(
                text(
                    "INSERT OR IGNORE INTO gallica_articles "
                    "(ark_id, title, description, source_name, date_published, "
                    "gallica_url, content_hash, status, published_count) "
                    "VALUES (:ark_id, :title, :description, :source_name, "
                    ":date_published, :gallica_url, :content_hash, 'available', 0)"
                ),
                {
                    "ark_id": item.ark_id,
                    "title": item.title,
                    "description": item.description,
                    "source_name": item.source_name,
                    "date_published": item.date_published.isoformat(),
                    "gallica_url": item.gallica_url,
                    "content_hash": item.content_hash,
                },
            )
            inserted += result.rowcount

        await session.commit()
        return inserted
