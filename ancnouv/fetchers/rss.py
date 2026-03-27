# Fetcher RSS (Mode B) — Phase 2 [docs/DATA_SOURCES.md — DS-2, RF-3.1.3]
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.error import URLError  # [DS-2.3] import obligatoire — NameError sinon

import feedparser
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ancnouv.config import Config
from ancnouv.fetchers.base import RssFeedItem

logger = logging.getLogger(__name__)


class RssFetcher:
    """Collecte les articles RSS (Mode B — optionnel). N'hérite pas de BaseFetcher. [DS-2, DS-6]"""

    async def fetch_all(self, config: Config) -> list[RssFeedItem]:
        """Collecte séquentielle de tous les flux RSS configurés. [DS-2.3, JOB-2]

        Séquentiel (pas asyncio.gather) — évite la saturation du thread pool [JOB-2].
        Si un flux échoue, passage au suivant avec un WARNING.
        published_parsed validé en PREMIER — avant tout mapping [DS-2.3].
        """
        all_items: list[RssFeedItem] = []
        now = datetime.now(timezone.utc)
        max_age_cutoff = now - timedelta(days=config.content.rss.max_age_days)

        for feed_conf in config.content.rss.feeds:
            try:
                feed = await asyncio.to_thread(feedparser.parse, feed_conf.url)
            except Exception as exc:
                logger.warning("Erreur réseau flux RSS %s : %s", feed_conf.url, exc)
                continue

            # [DS-2.3] Ignorer uniquement si erreur critique de connexion (pas sur bozo seul)
            if feed.get("bozo") and isinstance(
                feed.get("bozo_exception"), (URLError, ConnectionError)
            ):
                logger.warning(
                    "Flux RSS inaccessible %s : %s",
                    feed_conf.url,
                    feed.get("bozo_exception"),
                )
                continue

            for entry in feed.get("entries", []):
                # [DS-2.3] Valider published_parsed AVANT tout mapping — évite IntegrityError NOT NULL
                if entry.get("published_parsed") is None:
                    continue

                title = entry.get("title", "").strip()
                # [DS-2.4] Filtrage titre vide
                if len(title) < 5:
                    continue

                article_url = entry.get("link", "").strip()
                if not article_url:
                    continue

                # Conversion time.struct_time (UTC) → datetime timezone-aware
                published_at = datetime(
                    *entry["published_parsed"][:6], tzinfo=timezone.utc
                )

                # [DS-2.4] Filtrage par ancienneté
                if published_at < max_age_cutoff:
                    continue

                # [DS-2.3] Extraction image_url en trois passes
                image_url: str | None = None
                thumbnails = entry.get("media_thumbnail", [])
                if thumbnails:
                    image_url = thumbnails[0].get("url") or thumbnails[0].get("href")
                if not image_url:
                    media_content = entry.get("media_content", [])
                    if media_content:
                        image_url = media_content[0].get("url") or media_content[0].get("href")
                if not image_url:
                    enclosures = entry.get("enclosures", [])
                    if enclosures and enclosures[0].get("type", "").startswith("image/"):
                        image_url = enclosures[0].get("href") or enclosures[0].get("url")

                all_items.append(
                    RssFeedItem(
                        source_url=feed_conf.url,  # → RssArticle.feed_url [DS-2.3]
                        title=title,
                        summary=entry.get("summary", ""),
                        article_url=article_url,
                        published_at=published_at,
                        fetched_at=now,
                        image_url=image_url or None,
                        feed_name=feed_conf.name,  # NOT NULL en DB [DS-2.3, DS-8]
                    )
                )

        return all_items

    async def store(self, articles: list[RssFeedItem], session: AsyncSession) -> int:
        """INSERT OR IGNORE via contrainte UNIQUE(article_url). Retourne le count. [DS-2.3]"""
        inserted = 0

        for article in articles:
            result = await session.execute(
                text(
                    "INSERT OR IGNORE INTO rss_articles "
                    "(feed_url, feed_name, title, summary, article_url, image_url, "
                    "published_at, fetched_at, status, published_count) "
                    "VALUES (:feed_url, :feed_name, :title, :summary, :article_url, "
                    ":image_url, :published_at, :fetched_at, 'available', 0)"
                ),
                {
                    "feed_url": article.source_url,
                    "feed_name": article.feed_name,
                    "title": article.title,
                    "summary": article.summary or "",
                    "article_url": article.article_url,
                    "image_url": article.image_url,
                    "published_at": article.published_at,
                    "fetched_at": article.fetched_at,
                },
            )
            inserted += result.rowcount

        await session.commit()
        return inserted
