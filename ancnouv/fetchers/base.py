# Dataclasses de transport et ABCs — Phase 2 [docs/ARCHITECTURE.md — fetchers/base.py]
# [TRANSVERSAL-1] EffectiveQueryParams ici (pas dans generator/) — décision ROADMAP Phase 0
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    pass


@dataclass
class RawContentItem:
    """Dataclass de transport Wikipedia → DB. [ARCHITECTURE.md — Interfaces]"""
    source: str
    source_lang: str
    event_type: str
    month: int
    day: int
    year: int
    description: str
    title: str | None = None
    wikipedia_url: str | None = None
    image_url: str | None = None


@dataclass
class RssFeedItem:
    """Dataclass de transport RSS → DB.

    NE PAS confondre avec ancnouv.db.models.RssArticle (ORM).
    feed_url ici s'appelle source_url (différence nominale documentée
    dans ARCHITECTURE.md — lexique des types).
    """
    source_url: str   # → RssArticle.feed_url lors de l'insertion
    title: str
    summary: str
    article_url: str
    published_at: datetime
    fetched_at: datetime
    image_url: str | None = None
    feed_name: str = ""  # fourni par config.content.rss.feeds[n].name


@dataclass
class EffectiveQueryParams:
    """Paramètres effectifs de collecte après application du niveau d'escalade.

    [TRANSVERSAL-1] Placé dans fetchers/base.py (décision Phase 0).
    L'escalade ne peut qu'assouplir la config de base, jamais la durcir. [DS-1.4c]
    """
    event_types: list[str] = field(default_factory=lambda: ["events"])
    use_fallback_en: bool = False
    dedup_policy: str = "never"
    dedup_window: int = 365
    min_age_years: int = 0  # 0 = pas de limite
    max_age_years: int = 0  # 0 = pas de limite


class BaseFetcher(ABC):
    """ABC pour les fetchers date-based (Wikipedia et futurs).

    RssFetcher n'hérite pas de BaseFetcher — interface différente (non date-based).
    [ARCHITECTURE.md — Interfaces, DS-6]
    """

    @abstractmethod
    async def fetch(self, target_date: date) -> list[RawContentItem]: ...

    @abstractmethod
    async def store(self, items: list[RawContentItem], session: AsyncSession) -> int: ...
