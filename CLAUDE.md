# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Environnement

Le projet utilise **uv** avec un virtualenv dans `.venv/` (**Python 3.12 obligatoire**) :

```bash
uv venv --python 3.12                            # créer le venv (Python 3.12 requis — voir note)
source .venv/bin/activate                        # activer le venv
uv pip install -r requirements-dev.txt           # installer toutes les dépendances (prod + test)
git config core.hooksPath .githooks              # activer les hooks git (pre-push lance les tests)
```

> **⚠️ Python 3.14 incompatible avec PTB 20.x (dev local uniquement)** — En Python 3.14,
> `AbstractAsyncContextManager` a reçu `__slots__ = ()`, supprimant le `__dict__` de fallback
> des sous-classes. Or PTB 20.8 assigne `self.__polling_cleanup_cb` dans `Updater.__init__` sans
> le déclarer dans `__slots__`, ce qui lève `AttributeError: 'Updater' object has no attribute
> '_Updater__polling_cleanup_cb'` au démarrage. Ce bug n'affecte pas la production (Dockerfile
> Python 3.12). **Ne pas upgrader le venv local au-delà de Python 3.13 sans vérifier PTB.**

`requirements-dev.txt` inclut `requirements.txt` + pytest, pytest-asyncio, pytest-cov, pytest-httpx, freezegun.

---

## Commandes essentielles

```bash
# Démarrer l'application
python -m ancnouv start

# Initialiser la base de données
python -m ancnouv db init

# Migrations Alembic
python -m ancnouv db migrate       # appliquer les migrations en attente
python -m ancnouv db status        # alembic current
# Rollback : alembic downgrade -1 directement (pas de commande db rollback)

# Authentification Meta (OAuth interactif — à faire une seule fois)
python -m ancnouv auth meta
python -m ancnouv auth test

# Prefetch Wikipedia (pour alimenter le cache)
python -m ancnouv fetch --prefetch

# Vérification de santé
python -m ancnouv health

# Générer une image de test (sans publier)
python -m ancnouv generate-test-image

# Serveur d'images (VPS uniquement, container séparé)
python -m ancnouv images-server [--port PORT]
```

### Tests

```bash
# Tous les tests
pytest

# Un fichier de test
pytest tests/integration/test_post_lifecycle.py

# Un test spécifique
pytest tests/unit/test_token_manager.py::test_days_until_expiry_future

# Avec verbosité
pytest -v
```

`asyncio_mode = auto` est configuré — toutes les fonctions `async def test_*` sont gérées automatiquement.

---

## Architecture

### Vue d'ensemble

Application Python autonome : collecte d'événements historiques (Wikipedia "On This Day") → génération d'image + légende → validation manuelle via Telegram → publication simultanée sur Instagram et Facebook (Meta Graph API).

**Deux modes de contenu :**
- **Mode A (défaut)** : événements Wikipedia (`fr.wikipedia.org`, fallback `en.wikipedia.org`) pour la date du jour (même MM/JJ, n'importe quelle année)
- **Mode B (optionnel, `rss.enabled: true`)** : articles RSS republiés avec délai configurable (défaut : 90 jours)

**Flux principal :**
1. `job_fetch_wiki` (JOB-1) collecte les événements Wikipedia
2. `job_generate` (JOB-3) sélectionne un événement, génère une image (Pillow, 1080×1350) + légende, envoie au bot Telegram pour validation
3. Handler Telegram (`handle_approve`) publie sur Instagram + Facebook en parallèle (`asyncio.gather`)

### Structure des modules

```
ancnouv/
├── __main__.py          # Entry point CLI (argparse) — _dispatch_inner() route les commandes
├── config.py            # Pydantic Settings — config.yml + .env
├── exceptions.py        # Hiérarchie des exceptions custom
├── scheduler/
│   ├── __init__.py      # APScheduler — create_scheduler(), main_async()
│   ├── jobs.py          # job_fetch_wiki, job_generate, check_and_increment_daily_count, …
│   └── context.py       # Singletons partagés : config, bot_app, engine — init_context(), set_engine()
├── db/
│   ├── models.py        # Modèles SQLAlchemy ORM (Event, RssArticle, Post, MetaToken)
│   ├── session.py       # init_db(), get_session()
│   ├── utils.py         # get_scheduler_state(), set_scheduler_state()
│   ├── cli.py           # Commandes db (init, migrate, status, backup, reset)
│   └── migrations/      # Scripts Alembic
├── fetchers/
│   ├── base.py          # RawContentItem, RssFeedItem (dataclasses transport) ; BaseFetcher ABC
│   ├── wikipedia.py     # WikipediaFetcher
│   └── rss.py           # RssFetcher (Mode B — n'hérite PAS de BaseFetcher, pas date-based)
├── generator/
│   ├── __init__.py      # generate_post() — orchestration complète
│   ├── selector.py      # select_event(), select_article(), get_effective_query_params()
│   ├── image.py         # Génération d'image Pillow
│   └── caption.py       # format_caption_wiki(), format_caption_rss()
├── bot/
│   ├── bot.py           # Instance Application PTB, setup_handlers()
│   ├── handlers.py      # handle_approve, handle_reject, commandes /status, /pause, …
│   └── notifications.py # notify_all(), send_approval_request()
├── publisher/
│   ├── __init__.py      # publish_to_all_platforms()
│   ├── instagram.py     # InstagramPublisher
│   ├── facebook.py      # FacebookPublisher
│   ├── token_manager.py # TokenManager, days_until_expiry(), get_alert_threshold()
│   └── image_hosting.py # upload_image(), serve_locally(), upload_to_remote(), run_image_server()
├── cli/                 # Commandes auth, fetch, health, setup, escalation, …
└── utils/
    ├── date_helpers.py  # compute_time_ago(), format_historical_date()
    ├── text_helpers.py  # Troncature, nettoyage de texte
    └── retry.py         # with_retry() — backoff exponentiel
```

### Points architecturaux critiques

**Contexte partagé (`scheduler/context.py`) :** APScheduler ne peut pas sérialiser des objets non-picklables en `kwargs`. `config`, `bot_app` et `engine` sont des singletons module-level initialisés via `init_context()` avant `scheduler.start()`. Règle absolue : ne jamais passer ces objets en `kwargs` de `scheduler.add_job()`.

**Sessions SQLAlchemy :** chaque coroutine (handler Telegram, job scheduler) doit obtenir sa propre session via `async with get_session() as session:`. Ne jamais partager une session entre coroutines concurrentes — deux `commit()` parallèles sur la même session corrompent silencieusement l'état ORM.

**Cycles d'importation :** `handlers.py` peut importer depuis `jobs.py`, jamais l'inverse. `scheduler/jobs.py` → `bot/notifications.py` est le seul lien scheduler→bot autorisé.

**`asyncio.run()`** n'est appelé qu'à deux endroits dans `__main__.py` : pour `start` et pour `auth meta`/`auth test`. Aucune autre fonction du projet ne l'appelle directement.

### Base de données

SQLite + SQLAlchemy 2.x async + aiosqlite ≥ 0.20.0. Trois PRAGMAs activés via event listener synchrone sur `engine.sync_engine, "connect"` : `foreign_keys = ON`, `journal_mode = WAL`, `busy_timeout = 10000`.

Table `scheduler_state` (non-ORM) : clés critiques `paused`, `daily_post_count`, `publications_suspended`, `escalation_level`, `token_alert_level`.

### Dépendances — versions incompatibles

| Bibliothèque | Version requise | Incompatible avec |
|---|---|---|
| apscheduler | ~3.10 | 4.x (SQLAlchemyJobStore supprimé) |
| python-telegram-bot | ~20.0 | 13.x (API async réécrite) ; Python 3.14+ (bug `__slots__` Updater, dev local) |
| sqlalchemy | ~2.0 | 1.x (API ORM changée) |
| aiosqlite | ≥0.20.0 | <0.20.0 (BEGIN EXCLUSIVE absent) |
| numpy | ≥1.24,<2 | 2.x (np.random.randint API modifiée) |

Python 3.12+ requis.

**Telegram Conflict crash loop (production)** — `telegram.error.Conflict: terminated by other
getUpdates request` en boucle au redémarrage Docker. Causes possibles : container précédent pas
encore terminé côté Telegram, ou autre instance locale active (ex: container Docker local
`ancnouv-local`). Fix appliqué dans `scheduler/__init__.py` :
- `drop_pending_updates=True` sur `start_polling()` pour invalider toute session active avant de
  démarrer le polling
- 60s de sleep avant shutdown sur `TelegramConflict` pour laisser Telegram expirer la session
- `scheduler.shutdown()` wrappé dans `try/except` dans `finally` pour éviter
  `SchedulerNotRunningError` si le scheduler n'a pas encore démarré

### Configuration

- `config.yml` : paramètres non-sensibles (versionnable)
- `.env` : secrets (`TELEGRAM_BOT_TOKEN`, `META_APP_ID`, `META_APP_SECRET`, `IMAGE_SERVER_TOKEN`)
- Chargé depuis le CWD au démarrage — lancer depuis la racine du projet

---

## Tests

**Principes :**
- Pas de mock sur la base de données — DB SQLite en mémoire (`:memory:`)
- Mocks uniquement sur les frontières réseau : Wikipedia API, Meta Graph API, Telegram, serveur d'images
- Tester les fonctions de job directement, pas le déclenchement APScheduler

**Fixtures clés (`conftest.py`) :**
- `db_engine` : engine SQLite en mémoire, scope `function`
- `db_engine_static` : engine avec `StaticPool` — **obligatoire** pour les tests de concurrence (`BEGIN EXCLUSIVE`). Sans `StaticPool`, chaque coroutine crée une connexion vers une DB `:memory:` distincte
- `db_session` : `async_sessionmaker` avec `expire_on_commit=False`
- `db_event` / `db_article` : Event / RssArticle valides pré-insérés

Toute fonction testée qui appelle `get_config()`, `get_engine()` ou `get_session()` nécessite `set_engine(test_engine)` dans le setup.

---

## Specs

`SPEC.md` est le document de référence. **Toujours lire la section de spec correspondante avant d'implémenter.** Chaque implémentation doit référencer le bloc `[SPEC-X.Y]` qu'elle implémente.

`REVIEW.md` contient un audit de 163 findings sur les specs (réalisé le 2026-03-22). Les items `[x]` marquent les incohérences identifiées — à consulter avant d'implémenter une fonctionnalité concernée.

Documentation détaillée dans `docs/` : `ARCHITECTURE.md`, `DATABASE.md`, `CONFIGURATION.md`, `TESTING.md`, `CLI.md`, `INSTAGRAM_API.md`, `TELEGRAM_BOT.md`, `SCHEDULER.md`, `IMAGE_GENERATION.md`, `DEPLOYMENT.md`, `DATA_SOURCES.md`.
