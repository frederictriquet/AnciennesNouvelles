# ROADMAP — Anciennes Nouvelles

> Document de suivi d'implémentation. Référence les specs [SPEC-X.Y] et les issues d'audit [REVIEW.md].
> Statuts : `[ ]` À faire · `[~]` En cours · `[x]` Terminé
>
> **Audit des specs complété le 2026-03-22.** Toutes les contradictions et ambiguïtés de DATABASE.md, DATA_SOURCES.md, INSTAGRAM_API.md, TELEGRAM_BOT.md, SCHEDULER.md, IMAGE_GENERATION.md, CONFIGURATION.md, ARCHITECTURE.md, DEPLOYMENT.md, CLI.md, TESTING.md et SPEC.md ont été résolues (163 issues + 7 transversaux). Les specs sont prêtes pour l'implémentation.

---

## Phase 0 — Résolution des blockers pré-implémentation

> Ces points doivent être résolus **avant** d'écrire du code. Ils impactent les choix d'architecture.

### Blockers transversaux [REVIEW.md]

- [x] **[TRANSVERSAL-1]** Trancher la signature de `select_event` : inclure `EffectiveQueryParams` en paramètre pour que les niveaux d'escalade 3 et 4 (déduplication `window`) soient opérationnels
- [x] **[TRANSVERSAL-2]** Décider de la stratégie `queued` en v1 : désactiver la transition `approved→queued` en v1 OU documenter la résolution manuelle (`UPDATE posts SET status='approved' WHERE status='queued'`) et ajouter une commande CLI de secours [SC-15]
- [x] **[TRANSVERSAL-3]** Définir `recover_pending_posts` : signature complète, module d'appartenance (`scheduler/jobs.py`), contexte d'appel (appelée depuis `main_async`, pas depuis un job APScheduler)
- [x] **[TRANSVERSAL-4]** Choisir la source de vérité pour l'anti-doublon des alertes token : `MetaToken.last_alert_days_threshold` (INSTAGRAM_API.md) OU `scheduler_state.token_alert_level` (TELEGRAM_BOT.md) — supprimer le doublon dans les specs
- [x] **[TRANSVERSAL-5]** Fixer la clé canonique de `image_retention_days` : `scheduler.image_retention_days`, valeur par défaut **7j** (SPEC [RF-3.4.8] fait foi — corriger la valeur 30j dans SCHEDULER.md)
- [x] **[TRANSVERSAL-6]** Définir le pattern d'injection de session SQLAlchemy dans les handlers PTB (injection via `context.bot_data["engine"]` recommandé)
- [x] **[TRANSVERSAL-7]** Rédiger `requirements.txt` avec versions épinglées : `apscheduler~=3.10`, `python-telegram-bot~=20.0`, `sqlalchemy~=2.0`, `aiosqlite>=0.20.0`, `numpy>=1.24,<2`, `pillow`, `feedparser`, `httpx`, `aiohttp`, `pydantic-settings`

### Corrections de specs contradictoires

- [x] **[D-01]** Amender SPEC [C-4.1.5] : la contrainte NAT s'applique au bot Telegram (polling sortant) uniquement — le serveur d'images nécessite une URL HTTPS publique
- [x] **[CONF-09]** Corriger l'exemple `public_base_url` dans CONFIGURATION.md : `"https://dev.example.com:8765"` contient `"example"` et est rejeté par `validate_image_hosting`
- [x] **[CONF-08]** Ajouter `@model_validator` dans `RssConfig` : `min_delay_days < max_age_days`
- [x] **[IMG-7]** Ajouter la formule temporelle dans `format_caption_rss` (requis par [SPEC-2.3])
- [x] **[DB-16]** Définir la requête SQL `select_article` complète dans DATABASE.md (actuellement renvoyée à DATA_SOURCES.md qui renvoie à DATABASE.md)

---

## Phase 1 — Infrastructure de base [SPEC-3.6, SPEC-4.1]

### Structure du projet

- [x] Créer la structure de modules `ancnouv/` selon ARCHITECTURE.md
- [x] `ancnouv/exceptions.py` — hiérarchie : `AncNouvError`, `FetcherError`, `GeneratorError`, `PublisherError`, `ImageHostingError`, `DatabaseError` [ARCH-21]
- [x] `requirements.txt` épinglé [TRANSVERSAL-7, ARCH-01]
- [x] `requirements-dev.txt` : `pytest>=8.0`, `pytest-asyncio>=0.23`, `pytest-cov`, `respx` (mock httpx) [T-13 — ne pas dupliquer `feedparser`]
- [x] `assets/fonts/.gitkeep` (polices exclues du VCS)
- [x] `data/` et `logs/` créés automatiquement au démarrage [ARCH-16]
- [x] `config.yml.example` + `.env.example`

### Configuration [SPEC-3.6, docs/CONFIGURATION.md]

- [x] `ancnouv/config.py` — Pydantic Settings
  - [x] `SchedulerConfig` : `generation_cron`, `timezone`, `approval_timeout_hours` (`ge=1, le=8760`) [CONF-07], `misfire_grace_time`, `max_pending_posts`
  - [x] `ContentConfig` : `wikipedia_min_events`, `prefetch_days` (`ge=1`) [CONF-03], `low_stock_threshold` (`ge=1`) [CONF-04], `deduplication_policy`, `deduplication_window_days` (`ge=1`) [CONF-02], `mix_ratio`, `image_retention_days` (`ge=1`, défaut 7) [TRANSVERSAL-5]
  - [x] `RssConfig` : `enabled`, `feeds[]`, `min_delay_days`, `max_age_days` + validator `min < max` [CONF-08]
  - [x] `RssFeedConfig` : `name`, `url` avec validation format URL [CONF-12]
  - [x] `ImageConfig` : `width`, `height`, `jpeg_quality` (`ge=1, le=100`) [CONF-01], `paper_texture_intensity`, `masthead_text`, `force_template`
  - [x] `ImageHostingConfig` : `backend`, `public_base_url` (obligatoire via validator, pas `Field(...)`) [CONF-14], `remote_upload_url` + `validate_image_hosting` [CONF-09, CONF-10]
  - [x] `InstagramConfig` : `enabled`, `user_id`, `api_version`
  - [x] `FacebookConfig` : `enabled`, `page_id` — `api_version` partagé depuis `InstagramConfig` [CONF-11]
  - [x] `TelegramConfig` : `authorized_user_ids`
  - [x] `DatabaseConfig` : `backup_keep` (`ge=1`) [CONF-05]
  - [x] `Config` : `validate_meta`, `validate_telegram` (séparé de `validate_meta`) [CONF-15], `validate_cron` (dépendance APScheduler dans config documentée) [CONF-16]
  - [x] `backup_keep ge=1` [CONF-05], `notification_debounce ge=0` [CONF-06]

### Base de données [docs/DATABASE.md, SPEC-3.2]

- [x] `ancnouv/db/models.py` — modèles SQLAlchemy
  - [x] Table `events` : `id`, `source`, `source_lang`, `event_type` CHECK('event','birth','death','holiday','selected') [DS-14], `month`, `day`, `year`, `description`, `title`, `wikipedia_url`, `image_url`, `content_hash`, `status`, `published_count`, `last_used_at`, `fetched_at`, `created_at`, `updated_at`
  - [x] Table `posts` : `id`, `event_id` FK, `image_path`, `caption`, `status` CHECK complet (machine à états), `instagram_post_id`, `facebook_post_id`, `instagram_error`, `facebook_error`, `error_message`, `image_public_url`, `telegram_message_ids` (JSON, `server_default=text("'{}'")`) [DB-13], `retry_count`, `published_at`, `approved_at`, `created_at`, `updated_at`
  - [x] Table `rss_articles` : `id`, `feed_url` NOT NULL, `feed_name` NOT NULL, `article_url` UNIQUE, `title`, `summary`, `image_url`, `published_at`, `fetched_at`, `status`, `published_count`, `last_used_at`, `created_at`, `updated_at`
  - [x] Table `meta_tokens` : `id`, `token_kind`, `access_token`, `expires_at`, `ig_user_id`, `ig_username`, `fb_page_id`, `fb_page_name`, `last_alert_days_threshold` [TRANSVERSAL-4]
  - [x] Table `scheduler_state` : `key` PK, `value`, `updated_at`
  - [x] Index composite `(month, day, status, published_count)` sur `events` [DB-11]
  - [x] `onupdate=func.now()` — lister exhaustivement les UPDATE nécessitant `updated_at` explicite [DB-7]
- [x] `ancnouv/db/session.py` — `init_db()`, `get_session()`
  - [x] PRAGMAs : `foreign_keys = ON`, `journal_mode = WAL`, `busy_timeout = 10000`
  - [x] `connect_args={"timeout": 15}` [SC-M1]
- [x] `ancnouv/db/utils.py` — `compute_content_hash()`, `get_scheduler_state()`, `set_scheduler_state()`
  - [x] `compute_content_hash` : NFKC → strip → lowercase → SHA-256 hexdigest [DS-1.7b]
  - [x] Vecteur de test canonique pour `compute_content_hash` [DS-13]
- [x] `ancnouv/db/cli.py` — commandes DB
  - [x] `db init` : crée `data/ancnouv.db` + Alembic init, sans `Config()` [RF-3.6.3]
  - [x] `db migrate` : `alembic upgrade head`
  - [x] `db status` : `alembic current` (code retour `1` si DB inaccessible) [CLI-M7, C-08]
  - [x] `db backup` : `VACUUM INTO` (pas `shutil.copy`) [DB-14] + rotation alphabétique décroissante `backup_keep` fichiers
  - [x] `db reset` : confirmation interactive, code retour `3` si annulé
- [x] `ancnouv/db/migrations/` — Alembic
  - [x] `env.py` : `render_as_batch=True`, PRAGMA FK **avant** `context.begin_transaction()` [DB-10]
  - [x] Migration initiale step-by-step documentée [DB-9]

### CLI entry point [docs/CLI.md, SPEC-3.6]

- [x] `ancnouv/__main__.py` — `main()`, `_dispatch()`, `_dispatch_inner()`
  - [x] Deux groupes commandes : avec/sans config complète [CLI.md]
  - [x] `except BaseException` pour `SystemExit(2)` argparse [T-07]
  - [x] Codes de retour : `0` succès, `1` erreur, `2` usage incorrect, `3` annulé [CLI.md]
  - [x] `asyncio.run()` unique point d'entrée de la boucle événementielle [CLI.md]
- [x] `ancnouv/cli/setup.py` — `setup fonts` : téléchargement idempotent des 4 polices, écriture atomique (fichier temp → déplacement) [IMG-m6]
- [x] `ancnouv/cli/auth.py` — `auth meta`, `auth test`
  - [x] Serveur HTTP temporaire `localhost:8080`, timeout 120s, fermeture après callback
  - [x] OAuth : code → token court → token long (60j) → Page Access Token
  - [x] Stockage exclusif en DB `meta_tokens` (aucun token dans `.env`)
  - [x] `auth meta` réinitialise `publications_suspended = "false"` après succès [CLI.md]
  - [x] Guide VPS : tunnel SSH `ssh -L 8080:localhost:8080` [CLI.md]
  - [x] Guard : port 8080 déjà utilisé → `OSError` avec message explicite [CLI.md]
- [x] `ancnouv/cli/fetch.py` — `fetch [--prefetch]` [RF-3.1.1, RF-3.6.3]
  - [x] `--prefetch` : `prefetch_days` prochains jours [RF-3.1.5]
  - [x] Sans option : aujourd'hui uniquement
  - [x] Comportement erreur réseau avec cache existant [C-02]
- [x] `ancnouv/cli/generate.py` — `generate-test-image`, sauvegarde `data/test_output.jpg`, ouverture viewer (silencieuse si headless) [CLI-M9]
- [x] `ancnouv/cli/health.py` — `health` [CLI.md]
  - [x] Composants critiques (exit `1`) : DB, Token Meta [CLI-M2]
  - [x] Composants non-critiques (warning) : Wikipedia, polices, Telegram, serveur images [C-04]
  - [x] `publications_suspended` → warning, `escalation_level > 0` → info [CLI-M3]
  - [x] "Prochain post" : `CronTrigger.from_crontab().get_next_fire_time()` — sans accès à `scheduler.db` [CLI.md]
- [x] `ancnouv/cli/escalation.py` — `escalation reset` : remet `escalation_level = 0`, notification Telegram (silencieuse si bot KO) [CLI-M6]
- [x] `ancnouv/cli/test_commands.py` — `test telegram`, `test instagram` (publication réelle — avertissement)
- [x] `ancnouv/utils/date_helpers.py` — `compute_time_ago()`, `format_historical_date()`
  - [x] Tableau des formules [SPEC-2.2] : `< 1 mois`, `N mois`, `1 an`, `N ans`
  - [x] Accord grammatical (1 an / N ans, 1 mois / N mois)
  - [x] Cas Mode A (MM/JJ identique → toujours multiple de 12 mois) vs Mode B
- [x] `ancnouv/utils/text_helpers.py` — troncature, nettoyage
- [x] `ancnouv/utils/retry.py` — `with_retry()`, `NON_RETRIABLE` (inclut `ImageHostingError`) [ARCH-m2]

---

## Phase 2 — Collecte de données [SPEC-3.1, docs/DATA_SOURCES.md]

### Wikipedia Fetcher (Mode A) [DS-1]

- [x] `ancnouv/fetchers/base.py`
  - [x] `RawContentItem` dataclass [ARCHITECTURE.md]
  - [x] `RssFeedItem` dataclass [ARCHITECTURE.md]
  - [x] `EffectiveQueryParams` dataclass ici (pas dans `generator/`) [DS-1.4c, TRANSVERSAL-1] : `event_types`, `use_fallback_en`, `dedup_policy`, `dedup_window`
  - [x] `BaseFetcher` ABC : `fetch(target_date)`, `store(items, session)`
- [x] `ancnouv/fetchers/__init__.py` — `prefetch_wikipedia()` (niveau 0 uniquement) [DS-4], `check_sources_health()` (HEAD avec note risque 405) [DS-12]
- [x] `ancnouv/fetchers/wikipedia.py` — `WikipediaFetcher(BaseFetcher)`
  - [x] `_call_api(lang, event_type, target_date)` : GET, header `User-Agent`, timeout 10s
  - [x] Retry : HTTP 429/503 → `Retry-After` (int OU date RFC 7231 — parser les deux) [DS-2], max 3x, cap 60s
  - [x] HTTP 404 → retourner `{}` (date sans événements — normal)
  - [x] `TYPE_TO_KEY` dict [DS-1.8] : inclure `'selected'` si ajouté au CHECK DDL [DS-14]
  - [x] `fetch()` : `effective_params.event_types`, accès via `.get()` (champs optionnels)
  - [x] Filtrage qualité [DS-1.6] : `len(text) < 20` → ignorer, `> 500` → tronquer, `year > current_year` → ignorer, `year < -9999` → ignorer
  - [x] Fallback EN si FR < `wikipedia_min_events` **après filtrage** [RF-3.1.4]
  - [x] Niveau ≥ 2 : EN inconditionnel + fusion FR+EN (risque doublon inter-langues [DS-3])
  - [x] Mapping vers `RawContentItem` [DS-1.7] : `title=None` en v1
  - [x] `store()` : INSERT OR IGNORE, rollback atomique si exception
  - [x] Usage dans `job_fetch_wiki` : `get_effective_query_params` **avant** instanciation [DS-1.9] — Phase 6 (JOB-1)

### Stratégie d'escalade [DS-1.4b]

- [x] `increment_escalation_level(session: AsyncSession) -> int` dans `generator/selector.py`
- [x] `get_effective_query_params(session, config) -> EffectiveQueryParams`
  - [x] Niveaux 0–5 : types d'événements, fallback EN, politique déduplication [DS-1.4b]
  - [x] L'escalade ne peut qu'assouplir (jamais durcir une config déjà plus permissive) [DS-1.4c]
  - [x] `scheduler_state.escalation_level` : string `"0"` à `"5"`
- [x] Comptage stock par date individuelle (pas total 7j) [DS-1.4c] — `needs_escalation()` dans `generator/selector.py`
- [x] Cliquet à sens unique : `escalation reset` uniquement via CLI [DS-1.4b] — implémenté Phase 1 (`cli/escalation.py`)
- [x] Niveau 5 : notification Telegram bloquante, app ne peut plus poster [DS-1.4b] — Phase 6 (JOB-3)

### RSS Fetcher (Mode B, optionnel) [DS-2, RF-3.1.3]

- [x] `ancnouv/fetchers/rss.py` — `RssFetcher` (ne hérite pas de `BaseFetcher`) [DS-6]
  - [x] `fetch_all(config) -> list[RssFeedItem]` : itère sur feeds, `asyncio.to_thread(feedparser.parse)` [DS-2.3]
  - [x] Feeds séquentiels (pas `gather`) — évite saturation thread pool [JOB-2]
  - [x] Extraction `image_url` : deux passes `media_thumbnail` (`"url"` / `"href"`) → `enclosures` → `None` [DS-2.3]
  - [x] Gestion `bozo` : ignorer uniquement sur `URLError`/`ConnectionError`, pas sur `bozo=True` seul [DS-2.3]
  - [x] `from urllib.error import URLError` explicite [DS-4]
  - [x] Filtrage [DS-2.4] : doublon URL, trop vieux (`max_age_days`), `len(title) < 5`, `published_parsed is None`
  - [x] Validation `published_parsed is None` **avant** toute construction `RssFeedItem` [DS-1]
  - [x] `feed_name` depuis `config.content.rss.feeds[n].name`, NOT NULL [DS-8]
  - [x] `source_url` = URL du flux (pas de l'article), NOT NULL [DS-2.3]
  - [x] `store()` : INSERT OR IGNORE sur `article_url` UNIQUE
  - [x] Comportement si un flux sur N lève erreur générique : skip + log WARNING, continuer [DS-20]

---

## Phase 3 — Génération de contenu [SPEC-2, SPEC-3.2, docs/IMAGE_GENERATION.md]

### Génération d'images [SPEC-2.3, docs/IMAGE_GENERATION.md]

- [x] `ancnouv/generator/image.py`
  - [x] `generate_image(source, config, output_path, thumbnail=None) -> Path`
  - [x] `_load_fonts()` : FONTS_DIR = projet_root / "assets" / "fonts" — ERROR si police requise absente, WARNING si italique [IMG-13]
  - [x] `_draw_paper_texture(intensity)` : numpy int16, retourne nouvelle image [IMG-m7, IMAGE_GENERATION.md]
  - [x] `_draw_masthead(draw, W, masthead_text, fonts)` : centrage via textbbox [IMG-10]
  - [x] `_draw_date_banner(draw, W, time_ago, date_str, fonts)` : y=185 + y=230, non-chevauchants [IMG-11]
  - [x] `_draw_event_text(draw, W, text, text_y, max_height, fonts)` : max 18 lignes, tronc. "..." [IMG-2]
  - [x] `_draw_footer(draw, W, H, source_text, fonts)` : y = H - 60 (adaptatif) [IMG-3]
  - [x] `_wrap_text(draw, text, font, max_width)` : word-wrap pixel, préserve `\n`, mot long seul [IMG-12]
  - [x] `fetch_thumbnail(image_url)` : async, timeout 5s, import httpx au niveau module [IMG-9]
  - [x] `_draw_thumbnail(img, thumbnail, y, W)` : letterbox/crop/resize selon ratio [IMG-6]
  - [x] Mode sans thumbnail (typographique pur) — si image_url NULL ou téléchargement échoue [SPEC-3.2.4]
  - [x] Dimensions 1080×1350 px lues depuis config, ratio 4:5 [IG-F4]
  - [x] Sauvegarde `data/images/{uuid}.jpg`, qualité configurable [SPEC-3.2.3]
  - [x] Style gazette vintage : palette COLORS + polices Playfair/Baskerville/IMFell [IMAGE_GENERATION.md]
  - [x] `GeneratorError` importée depuis `ancnouv.exceptions` [IMG-18]
  - [x] `IMG-4` : masthead configurable via `config.image.masthead_text`

### Formatage de légendes [SPEC-2.3]

- [x] `ancnouv/generator/caption.py`
  - [x] `format_caption(event, config) -> str` — formule temporelle + date + description + source + hashtags
  - [x] `format_caption_rss(article, config) -> str` — formule temporelle obligatoire [IMG-7, SPEC-2.3]
  - [x] `truncate_caption(text, max_chars=300)` — tronque au dernier mot entier + "..."
  - [x] Mention source (`source_template_fr`/`_en` / `feed_name`)
  - [x] Hashtags configurables (`config.caption.hashtags` + `hashtags_separator`)
  - [x] Légende ≤ 2200 chars vérifiée [SPEC-2.3, IMG-14]

### Sélecteur + `generate_post` [SPEC-3.2, docs/DATA_SOURCES.md]

- [x] `ancnouv/generator/selector.py`
  - [x] `select_event(session, target_date, effective_params) -> Event | None` [TRANSVERSAL-1]
    - [x] Filtres : `month/day`, `status='available'`
    - [x] Politique `never` : `published_count = 0` [SPEC-3.2.1]
    - [x] Politique `window` : `last_used_at IS NULL OR last_used_at < cutoff` [DB-1]
    - [x] Politique `always` : sans filtre dédup
    - [x] `ORDER BY RANDOM() LIMIT 1`
  - [x] `select_article(session, config, effective_params) -> RssArticle | None` [DB-16, DB-17]
    - [x] `fetched_at <= today - min_delay_days` (date collecte) [DS-2.5]
    - [x] Politique `window`/`always` pour RSS [DB-18]
  - [x] `get_effective_query_params(session, config) -> EffectiveQueryParams` (implémenté Phase 2) [ARCH-09]
- [x] `ancnouv/generator/__init__.py`
  - [x] `generate_post(session) -> Post | None`
    - [x] Sélection hybride A+B : `mix_ratio` [SPEC-3.2.1, DS-3]
    - [x] Fallback si source tirée sans candidat [SPEC-3.2.1]
    - [x] `None` si aucune source disponible
    - [x] Sauvegarde post `status='pending_approval'` dans une transaction [SPEC-3.2.6]
    - [x] Guard `max_pending_posts` [RF-3.2.7, T-08]
- [x] `ancnouv/scheduler/context.py` — stub minimal `get_config`/`set_config` (Phase 6 étendra)

---

## Phase 4 — Publication Meta [SPEC-3.4, docs/INSTAGRAM_API.md]

### Gestion des tokens [docs/INSTAGRAM_API.md]

- [x] `ancnouv/publisher/token_manager.py`
  - [x] `TokenManager` : singleton partagé (évite refreshes simultanés) [IG-F1]
  - [x] `get_valid_token()` : lecture DB, refresh si nécessaire
  - [x] `_save_token()` : protégé contre race condition [IG-F1]
  - [x] `days_until_expiry(token) -> int`
  - [x] `get_alert_threshold(remaining) -> int | None` : seuils [30, 14, 7, 3, 1, 0] [SCHEDULER.md]
  - [x] Refresh : uniquement si `days_remaining <= 7` côté Meta [IG-F8]
  - [x] Vérification que `expires_at` a progressé après refresh [IG-F8]

### Hébergement d'images [SPEC-3.4.1, docs/INSTAGRAM_API.md]

- [x] `ancnouv/publisher/image_hosting.py`
  - [x] `upload_image(image_path, config) -> str` (URL publique)
  - [x] `start_local_image_server(images_dir, port)` — backend `local`, serveur aiohttp embarqué
  - [x] `upload_to_remote(image_path, config)` — backend `remote`, retries x3 internes [ARCH-m2]
  - [x] `run_image_server(port, token)` — vérifie `token` non vide avant démarrage [CLI.md]
    - [x] `EADDRINUSE` → message explicite + exit `1` [ARCH-22]
  - [x] Routes : `POST /images/upload` (Bearer `IMAGE_SERVER_TOKEN`), `GET /images/{filename}` (public)
  - [x] `ImageHostingError` dans `NON_RETRIABLE` [ARCH-m2]

### Publication Instagram [SPEC-3.4.2, docs/INSTAGRAM_API.md]

- [x] `ancnouv/publisher/instagram.py` — `InstagramPublisher`
  - [x] `_get_or_create_container(post, image_url, caption)` : si container existant bloqué → recréer [IG-F9]
  - [x] `_wait_for_container_ready(container_id)` : polling statut, timeout 60s, lève `PublisherError` [IG-F9]
  - [x] `publish(post) -> str` (instagram_post_id)
  - [x] Guard `image_path NULL` avant publication [IG-F3]

### Publication Facebook [SPEC-3.4.3]

- [x] `ancnouv/publisher/facebook.py` — `FacebookPublisher`
  - [x] `publish(post) -> str` (facebook_post_id)
  - [x] Endpoint `/{page_id}/photos`
  - [x] `api_version` partagé depuis `InstagramConfig` [CONF-11]

### Orchestration publication [SPEC-3.4.4, SPEC-3.4.6]

- [x] `ancnouv/publisher/__init__.py` — `publish_to_all_platforms(post, image_url, ig_publisher, fb_publisher, session, caption, max_daily_posts)`
  - [x] Pas d'imports top-level `InstagramPublisher`/`FacebookPublisher` (contrainte architecturale) [D-02]
  - [x] `asyncio.gather` Instagram + Facebook en parallèle [SPEC-3.4.4]
  - [x] Gestion échec partiel : colonnes `instagram_error` / `facebook_error` séparées [RF-3.4.8, SPEC-3.4.6]
  - [x] Atomicité : UPDATE `published_count` + `post.status='published'` dans même `commit()` [DB-2]
  - [x] Limite 25 posts/24h configurable (`max_daily_posts`), max 50 [RF-3.4.7]
  - [x] `fb_publisher=None` → skip Facebook sans affecter Instagram [RF-3.4.9]
  - [x] Comportement si les deux plateformes désactivées → `published` intentionnel [IG-F14]
  - [x] `check_and_increment_daily_count` : skippé si `ig_publisher is None` [IG-F10]
  - [x] Vérification `publications_suspended` hors race condition [SC-2]

---

## Phase 5 — Bot Telegram [SPEC-3.3, docs/TELEGRAM_BOT.md]

### Infrastructure bot

- [x] `ancnouv/bot/bot.py` — `create_application(token)`, `setup_handlers(app)`
  - [x] `bot_data["config"] = config` injecté au démarrage (avant `init_context`)
  - [x] Pattern session SQLAlchemy : injection via `context.bot_data["engine"]` [TRANSVERSAL-6]
- [x] `ancnouv/bot/notifications.py`
  - [x] `notify_all(bot, config, message)` : itère sur `authorized_user_ids`
  - [x] `send_with_retry(bot, chat_id, message)` : x5 backoff exp. (~160s max — noter risque timeout APScheduler) [TG-F15]
  - [x] `send_approval_request(bot, post, config, session)`
    - [x] Guard `authorized_user_ids` vide → log ERROR, ne pas bloquer silencieusement [TG-F9]
    - [x] Légende Telegram tronquée à 1024 chars en mémoire **sans écraser la légende DB** [TG-F11]
    - [x] `telegram_message_ids` dict `{str(user_id): message_id}` sauvegardé en DB

### Décorateur d'autorisation

- [x] `authorized_only(handler)` : `@functools.wraps`, `update.effective_message` (fonctionne pour messages et callbacks) [TELEGRAM_BOT.md]

### Handlers de commandes [SPEC-3.3.6]

- [x] `ancnouv/bot/handlers.py`
  - [x] `cmd_start` : bienvenue + état scheduler + pending count + prochaine exécution [RF-3.3.5]
  - [x] `cmd_help` : liste statique des commandes (pas de requête DB)
  - [x] `cmd_status` : scheduler, `daily_post_count`, pending, dernier publié, `token_alert_level` [TG-M9]
  - [x] `cmd_stats` : publié, rejeté, taux approbation (garde division par zéro) [TG-F13], posts 7j (fenêtre glissante UTC)
  - [x] `cmd_pending` : liste `pending_approval`, âge via `total_seconds()` (pas `.seconds`) [TELEGRAM_BOT.md], extrait 50 chars
  - [x] `cmd_pause` / `cmd_resume` : `set_scheduler_state(session, "paused", ...)` [SPEC-3.5.4]
  - [x] `cmd_force` : appel direct `generate_post` → bypass `max_pending_posts`, pas bypass limite journalière [TELEGRAM_BOT.md, SC-8]
    - [x] `generate_post` retourne `None` → message "Aucun événement disponible"
    - [x] Comportement pendant pause (`paused=True`) documenté [SC-14]
  - [x] `cmd_retry` : verrou optimiste `UPDATE ... WHERE status='error'` (0 lignes → retry parallèle, retour silencieux) [TELEGRAM_BOT.md]
  - [x] `cmd_retry_ig` : post `published` + `instagram_error IS NOT NULL`, `_retry_single_platform` [TG-F16]
  - [x] `cmd_retry_fb` : idem `facebook_error` [TG-F16]
  - [x] Noms commandes : underscores (`/retry_ig`, `/retry_fb`) — pas de tirets [TELEGRAM_BOT.md]

### Workflow d'approbation inline [SPEC-3.3.2]

- [x] `handle_approve` : `pending_approval → publishing → published`
  - [x] Verrou optimiste (deux admins simultanés) [TG-F5]
  - [x] `check_and_increment_daily_count` → si limite atteinte : `approved → queued` (v1 : bloquer + message + action documentée) [TRANSVERSAL-2]
  - [x] Éditer les messages des **autres** admins après approbation [TG-F10]
  - [x] Upload image → `publish_to_all_platforms` → update statut DB
  - [x] `OperationalError: database is locked` → message utilisateur [SCHEDULER.md]
- [x] `handle_reject` : `rejected`, `event.status = 'blocked'` [SPEC-3.3.2]
- [x] `handle_skip` : `skipped` + nouveau `generate_post` [SPEC-3.3.2]
  - [x] Séquençage : écrire `skipped` + **commit** → puis `generate_post` [SPEC-3.3.2]
  - [x] `generate_post` retourne `None` → notifier, post reste `skipped`, pas de restauration
  - [x] `pending_count` inchangé (+1 nouveau, −1 skipé)
- [x] `handle_edit_caption` : `ConversationHandler` [SPEC-3.3.2]
  - [x] Stocker le type de message original (photo vs texte) dans `context.chat_data` [TG-F4]
  - [x] `handle_new_caption` : écrire en DB la légende complète (pas tronquée 1024) [TG-F11]
  - [x] Timeout/annulation → re-clic relance l'entry_point [TG-F14]
- [x] `job_check_expired` (JOB-4 — voir Phase 6) : désactiver boutons via `telegram_message_ids.items()` avec guard `message_id is not None` [SCHEDULER.md]

---

## Phase 6 — Scheduler [SPEC-3.5, docs/SCHEDULER.md]

### Contexte partagé

- [x] `ancnouv/scheduler/context.py` — singletons `config`, `bot_app`, `engine`, session factory
  - [x] Getters lèvent `RuntimeError` si non initialisé (pas `assert`) [SCHEDULER.md]
  - [x] `init_context(config, bot_app, engine)` : `set_config`, `set_bot_app`, `set_engine`, rebind `_session_factory`
  - [x] `data/scheduler.db` relatif à `config.data_dir` (préciser dans doc) [ARCH-15]

### Création du scheduler

- [x] `ancnouv/scheduler/__init__.py` — `create_scheduler(config)`, `run(config)`, `main_async(config)`
  - [x] `AsyncIOScheduler` avec `timezone=config.scheduler.timezone` [SC-6]
  - [x] Jobstores : `default` = `SQLAlchemyJobStore` (sync URL `sqlite:///`), `memory` = `MemoryJobStore`
  - [x] `coalesce=True`, `max_instances=1`, `misfire_grace_time=300`
  - [x] `replace_existing=True` pour JOB-1, JOB-2, JOB-3 [SCHEDULER.md]
  - [x] Séquence canonique `main_async` [SC-C6] :
    1. `init_db` → 2. `create_application` → 3. `bot_data["config"]` → 4. `init_context`
    5. `create_scheduler` → 6. `start_local_image_server` (si backend=local)
    6. `recover_pending_posts` → 8. `scheduler.start` → 9. `run_polling` → 10. `scheduler.shutdown`
  - [x] Inverser 4 et 8 → `RuntimeError`; inverser 6 et 7 → URLs inaccessibles [SC-C6]

### Jobs

- [x] **JOB-1** `job_fetch_wiki` — `cron 0 2 * * *` (heure fixe, configurable à documenter) [ARCH-04]
  - [x] `get_effective_query_params` → `WikipediaFetcher(config, params).fetch(date)` → `store`
  - [x] Vérification stock 7j après collecte → `increment_escalation_level` si bas [DS-1.4c]
  - [x] `OperationalError` → retry x3 backoff (1s, 2s, 4s) [SC-M1]
  - [x] Concurrence avec JOB-3 à 2h → noter et recommander décalage via config [SC-M1]
- [x] **JOB-2** `job_fetch_rss` — `interval 6h` (configurable à documenter) [ARCH-05]
  - [x] Enregistré uniquement si `rss.enabled = true`
  - [x] Déclenchement immédiat au redémarrage si délai > 6h (intentionnel, idempotent) [SC-C1]
- [x] **JOB-3** `job_generate` — `cron config.scheduler.job_generate_cron` (défaut `0 */4 * * *`)
  - [x] `max_instances=1` (skip silencieux si instance active) [SC-C7]
  - [x] Vérification `paused` → skip [SPEC-3.5.4]
  - [x] `pending_count >= max_pending_posts` → skip [RF-3.2.7]
  - [x] Mode `auto_publish=false` : `generate_post` → `send_approval_request`
  - [x] Mode `auto_publish=true` : `generate_post` **avant** `check_and_increment_daily_count` [RF-3.2.8]
  - [x] `try/except Exception as e: logger.error(..., exc_info=True)` [SCHEDULER.md]
- [x] **JOB-4** `job_check_expired` — `interval 1h`, MemoryJobStore [RF-3.3.3]
  - [x] Expire posts `pending_approval` > `approval_timeout_hours`
  - [x] Désactiver boutons via `telegram_message_ids.items()` + guard [SCHEDULER.md]
  - [x] Notifier utilisateur avec extrait de légende [TG-F12]
  - [x] Posts renvoyés par `recover_pending_posts` expirés mais pas encore marqués → comportement défini [SC-9]
- [x] **JOB-5** `job_check_token` — `cron 0 9 * * *`, MemoryJobStore [RF-3.4.5]
  - [x] Anti-doublon via `MetaToken.last_alert_days_threshold` + `scheduler_state.token_alert_level` [TRANSVERSAL-4]
  - [x] Seuils 30j, 14j : notification uniquement
  - [x] Seuils 7j, 3j : notification + refresh automatique
  - [x] J-1 ou expiré + refresh échoue : `publications_suspended = "true"`
  - [x] Appel direct dans `main_async` avant `scheduler.start()` [SC-M4]
- [x] **JOB-6** `job_cleanup` — `cron 0 3 * * *`, MemoryJobStore
  - [x] Posts `published/rejected/expired/skipped` dont date > `config.content.image_retention_days` (défaut 7j) [TRANSVERSAL-5]
  - [x] Clarifier `created_at` vs `published_at` (impact `/retry_ig` si image nettoyée avant publication) [DB-8]
  - [x] `FileNotFoundError` → ignorer silencieusement, écrire `image_path = NULL` [SCHEDULER.md]
- [x] **JOB-7** `job_publish_queued` — **commenté en v1** [SPEC-7ter]
  - [x] Procédure de déblocage v1 documentée [SC-15, TRANSVERSAL-2]

### Compteur journalier [SCHEDULER.md]

- [x] `check_and_increment_daily_count(engine, max_daily_posts) -> bool`
  - [x] `execution_options(isolation_level="AUTOCOMMIT")` + `BEGIN EXCLUSIVE` [SCHEDULER.md]
  - [x] aiosqlite ≥ 0.20.0 épinglé [SCHEDULER.md]
  - [x] Vérification `publications_suspended` en premier
  - [x] Reset à minuit UTC (premier appel du jour) [SCHEDULER.md]
  - [x] `publications_suspended = "true"` → retourne `False` immédiatement

### Récupération après crash [SCHEDULER.md, TRANSVERSAL-3]

- [x] `recover_pending_posts(session, bot, config)` dans `scheduler/jobs.py`
  - [x] `publishing → approved` (crash mid-publish)
  - [x] `approved` + `image_public_url` non-NULL + (`instagram_post_id IS NULL` OU `facebook_post_id IS NULL`) → re-publish [SC-4]
  - [x] `approved` sans `image_public_url` → notifier `/retry` manuel
  - [x] `pending_approval` + `telegram_message_ids == {}` → re-envoyer
  - [x] `telegram_message_ids` partiellement rempli → ne pas renvoyer [SC-10]
  - [x] Session externe : SELECT/UPDATE uniquement, fermée avant appels réseau Meta [SC-m7]
  - [x] Posts `queued` non traités en v1

### Pause/reprise [SPEC-3.5.4]

- [x] `pause_scheduler(session)`, `resume_scheduler(session)`, `is_paused(session)`
- [x] Via `scheduler_state.paused` (persisté entre redémarrages), pas APScheduler API
- [x] Seul JOB-3 bloqué par pause [SCHEDULER.md]

---

## [x] Phase 7 — Tests [docs/TESTING.md] ✅ 80% Coverage Atteint

### Configuration

- [x] `pyproject.toml` : `asyncio_mode = auto`, `--cov-fail-under=80`
- [x] Omit list pour CLI/infrastructure non-testables
- [x] `.github/workflows/ci.yml` [T-16]

### `tests/conftest.py`

- [x] `db_engine` (scope function, `:memory:`, `run_sync(Base.metadata.create_all)`, PRAGMAs via event listener, `scheduler_state` DDL, `set_engine`) [TESTING.md]
- [x] `db_engine_static` (StaticPool) pour tests de concurrence [TESTING.md]
  - [x] Contrainte d'exclusion mutuelle avec `db_engine` documentée [T-15]
- [x] `db_session` issue de `db_engine`
- [x] `db_article` : `fetched_at = now - (min_delay_days + 1)` dynamique (pas hardcodé à 91j) [T-05]
- [x] `mock_config`, `mock_bot`, `mock_wikipedia_response`
- [x] Chemin de patch `notify_all` documenté pour chaque fichier de test [T-02]

### Tests unitaires

- [x] `test_image_generation.py` : génération, wrapping, texture, zones, cas NULL image_url
- [x] `test_caption.py` : format légende wiki + RSS (formule temporelle présente) [IMG-7]
- [x] `test_deduplication.py` : `compute_content_hash` avec vecteur canonique [DS-13], politiques dedup
- [x] `test_token_manager.py` : jours restants, **tous les seuils** 30j/14j/7j/3j/1j/0j [T-03]
- [x] `test_config.py` : validators Pydantic, cron invalide, toutes les contraintes `ge`/`le` [T-07]
- [x] `test_text_helpers.py` : truncate, clean_text, truncate_for_image
- [x] `test_scheduler_context.py` : get_config/get_engine/get_bot_app RuntimeError paths
- [x] `test_db_session.py` : _configure_sqlite_pragmas, create_engine, init_db, get_session RuntimeError

### Tests d'intégration

- [x] `test_wikipedia_fetcher.py` : mock API, filtrage, fallback EN, `Retry-After` RFC 7231 [DS-2], gestion 404
- [x] `test_rss_fetcher.py` : parsing, déduplication URL, `max_age`, `bozo` handling [DS-1]
- [x] `test_instagram_publisher.py` : container → publish, erreurs Meta, container bloqué [IG-F9]
- [x] `test_facebook_publisher.py` : flow `/photos`, erreurs
- [x] `test_post_lifecycle.py` : machine à états complète, publication parallèle IG+FB, échec partiel [T-10]
- [x] `test_scheduler_jobs.py`
  - [x] Logique jobs (pas déclenchement cron)
  - [x] `test_daily_counter_race_condition` (fichier SQLite via tmp_path) [TESTING.md]
  - [x] `test_daily_counter_exclusivity` [TESTING.md]
- [x] `test_selector.py`
  - [x] `select_event` politiques `never`, `window`, `always` [T-09]
  - [x] `select_article` politiques déduplication [DB-17, DB-18]
  - [x] `get_effective_query_params` tous les niveaux 0–5
  - [x] `pending_count >= max_pending_posts` → `generate_post` retourne `None` [T-08]
- [x] `test_recover_pending_posts.py` : envoi/skip/publication, chemin patch documenté [T-02]
- [x] `test_telegram_handlers.py`
  - [x] `test_handle_approve_optimistic_lock` (fichier SQLite via tmp_path) [TESTING.md]
  - [x] `handle_skip` → `generate_post` → `send_approval_request` (flux complet) [T-06]
  - [x] `cmd_stats` division par zéro [TG-F13]
  - [x] Post expiré → notification + `event.status` reste `available` [T-04, RF-3.3.3]
  - [x] `cmd_force` : bypass `max_pending_posts`, pas bypass limite journalière

---

## [x] Phase 8 — Déploiement [docs/DEPLOYMENT.md] ✅

### Prérequis système

- [x] Python 3.12+ (confirmer dans ARCHITECTURE.md) [ARCH-02]
- [x] `libjpeg-dev` pour Pillow JPEG [IMAGE_GENERATION.md]
- [x] `sqlite3` CLI sur hôte pour crontab backup (ajouter dans doc si absent) [D-08]

### Systemd

- [x] `ancnouv.service` : `WorkingDirectory`, `ExecStart`, `Restart=on-failure` (`deploy/systemd/`)
- [x] `ancnouv-images.service` : service séparé pour `images-server` (backend `remote`)
- [x] `ancnouv-notify@.service` : curl crash → guard `TELEGRAM_CHAT_ID` non vide [D-07]
- [x] Séquence init systemd alignée sur CLI.md (`db init → setup fonts → auth meta → fetch --prefetch`) [D-03]

### Docker

- [x] `Dockerfile` : `libjpeg-dev`, `COPY alembic.ini`, `COPY db/migrations/versions/` [D-06]
- [x] `docker-compose.yml` : services `ancnouv` + `ancnouv-images`
- [x] `setup fonts` comme étape explicite dans la séquence Docker (pas implicite via COPY) [D-04]
- [x] Ne pas lancer `start` et `images-server` simultanément sur le même port [CLI.md]

### Architecture Raspberry Pi + VPS [SPEC-3.4.1, D-05]

- [x] Documenter architecture RPi (bot + scheduler) + VPS (serveur images `backend=remote`)
- [x] Tunnel SSH permanent pour serveur images
- [x] Incompatibilité C-4.1.5 (NAT) vs serveur images → clarifiée dans SPEC [D-01]

### Backup et maintenance

- [x] Crontab `db backup` quotidien (`VACUUM INTO`, sûr pendant `start`)
- [x] Crontab backup `scheduler.db` (rotation 7 fichiers, `sqlite3` requis sur hôte) [D-08]
- [x] Procédure rollback Alembic : Docker + systemd [D-11]
- [x] Procédure renouvellement token (`auth meta`) + levée `publications_suspended` [D-10]

---

## Phase 9 — v2 : Stories, Templates et File d'attente

> Périmètre v2. Implémentation après stabilisation v1.

### Stories Instagram et Facebook [SPEC-7]

- [x] Template Pillow 1080×1920 px (ratio 9:16) [SPEC-7.1]
- [x] Zones de sécurité : 270px haut, 400px bas [SPEC-7.4]
- [x] Texte condensé — `stories.max_text_chars` (défaut 400) [SPEC-7.4]
- [x] `stories.enabled` dans config [SPEC-7.3.5]
- [x] Publication Story immédiatement après post feed (même approbation) [SPEC-7.3.3]
- [x] Instagram Stories (`media_type=STORIES`) [SPEC-7.3.4, IG-F5]
- [x] Facebook Stories (`/{page-id}/photo_stories`) [SPEC-7.3.4]
- [x] Échec Story non bloquant pour le post feed [SPEC-7.3.6]
- [x] Colonnes `story_image_path`, `story_post_id` dans `posts` (migration 0003) [SPEC-7.4]
- [x] `data/images/{uuid}_story.jpg`, même politique de rétention [SPEC-7.3.7]
- [x] Documenter endpoint `media_type=STORIES` dans INSTAGRAM_API.md [IG-F5]

### Templates par époque [SPEC-7bis]

- [x] 6 époques avec palettes de couleurs [SPEC-7bis] [IMG-1]
- [x] `_get_template_for_year(year, force_template)` [RF-7bis.1]
- [x] Mode B → toujours style "XXIe siècle" [RF-7bis.1]
- [x] `image.force_template` dans config [RF-7bis.4]
- [x] Même système feed et Story [RF-7bis.3]
- [ ] Polices et dispositions spécifiques par époque (v3) [RF-7bis.2]

### File d'attente et publication planifiée [SPEC-7ter]

- [x] Statut `queued` opérationnel — colonne `scheduled_for` dans `posts` [SPEC-7ter.1]
- [x] Migration Alembic 0004 : `scheduled_for DATETIME` dans `posts`
- [x] Nouveaux boutons : **Publier maintenant** / **Ajouter à la file** [SPEC-7ter.2]
- [x] Commande `/queue` : file + heures estimées [SPEC-7ter.4]
- [x] `scheduler.max_queue_size` configurable (défaut 10) [SPEC-7ter.5]
- [x] Ordre : `scheduled_for ASC NULLS LAST, approved_at ASC` [SPEC-7ter.6]
- [x] **JOB-7** activé (même cron que JOB-3, MemoryJobStore) [SPEC-7ter.3]
- [x] `recover_pending_posts` : posts `queued` repris par JOB-7 au prochain cron (v2) [SCHEDULER.md]
- [x] `/retry` opérationnel sur posts `queued` [TRANSVERSAL-2] — obsolète en v2 : JOB-7 publie automatiquement, échecs → `error` → `/retry` les prend
- [ ] **Planifier à [heure]** (`scheduled_for` fixe via Telegram) [RF-7ter.2] — v3

---

## Phase 10 — Dashboard de configuration

> Interface web légère pour configurer ancnouv sans éditer les fichiers manuellement.
> Conteneur séparé (`ancnouv-dashboard`), FastAPI + Jinja2 + htmx, exposé sur loopback.

### Phase 10.1 — Table `config_overrides` et persistance (ancnouv)

- [x] Migration Alembic : table `config_overrides` (`key TEXT PK`, `value TEXT`, `value_type TEXT`, `updated_at`)
- [x] `ancnouv/db/config_store.py` : `get_all_overrides()`, `get_override()`, `set_override()`, `delete_override()`
- [x] `value_type` : `'str' | 'int' | 'float' | 'bool' | 'list' | 'dict'` (JSON-encodé)
- [x] Tests unitaires `config_store`

### Phase 10.2 — Config overlay dans ancnouv

- [x] `ancnouv/config_loader.py` : `get_effective_config()` avec cache TTL 30s
  - [x] Charge YAML via `Config` (base), puis applique les overrides DB (`apply_dot_overrides`)
  - [x] `invalidate_config_cache()` appelé après écriture dashboard (via `scheduler_state` flag)
- [x] `apply_dot_overrides(base_dict, overrides_flat)` : merge par dot-path
- [x] Jobs APScheduler migrent de `get_config()` vers `get_effective_config()`
- [x] Flag `config_restart_required` dans `scheduler_state` si `scheduler.generation_cron` modifié
- [x] Tests unitaires overlay

### Phase 10.3 — Service dashboard

- [x] Structure `dashboard/` dans le repo
  - [x] `Dockerfile` (python:3.12-slim, uvicorn, port 8766)
  - [x] `requirements.txt` : fastapi, uvicorn, jinja2, aiosqlite, sqlalchemy, pydantic
  - [x] `main.py`, `db.py`, `config_meta.py` (définitions des settings exposés)
  - [x] `routers/overview.py`, `routers/config.py`, `routers/posts.py`
  - [x] `templates/` : `base.html`, `overview.html`, `config.html`, `posts.html`
- [x] **Vue d'ensemble** (`GET /`) : état bot (paused/running), daily count, derniers posts, alertes token
- [x] **Éditeur config** (`GET /config`) : sections accordéon, widgets adaptés au type, badge "override" vs "défaut"
  - [x] `POST /config/set` (htmx) : écriture `config_overrides`, validation inline, retour fragment
  - [x] `POST /config/reset/{key}` (htmx) : suppression override, retour valeur défaut
  - [x] Bandeau "redémarrage requis" si `config_restart_required` présent
- [x] **Posts récents** (`GET /posts`) : liste avec statuts
- [x] `GET /health` : healthcheck 200 OK
- [x] Settings exposés (tous les groupes du registre)
- [x] Validations dashboard (`config_meta.py`) : cron via `CronTrigger`, options enum, min/max

### Phase 10.4 — Infrastructure

- [x] `docker-compose.yml` : service `ancnouv-dashboard` (port `127.0.0.1:8766:8766`, volume `./data`)

---

## Phase 11 — v3+ [SPEC-8, SPEC-9]

> Implémentation en cours sur `feat/phase11`.

### Reels Instagram et Facebook [SPEC-8]

- [x] Choix librairie vidéo : `ffmpeg` (subprocess) [SPEC-8.3]
- [x] Génération vidéo : animation fade+reveal (shell → image complète), 15–30s, MP4 H.264, 1080×1920 [SPEC-8.3]
- [x] Upload vidéo en deux temps (API Meta async) [SPEC-8.3]
- [x] Polling statut traitement (max 5 min — 150 × 2s) [SPEC-8.3]
- [x] Droits audio : 3 enregistrements CC0 1.0 (Internet Archive) — `setup audio` + sélection aléatoire automatique depuis `assets/audio/` [SPEC-8.3]
- [ ] Politique rétention révisée (5–20 Mo/fichier) [SPEC-8.3]
- [ ] Performance RPi : H264 hardware acceleration [SPEC-8.3]

### Autres formats et sources [SPEC-9]

- [x] Threads (publication texte uniquement)
- [ ] Carrousel Instagram (multi-images)
- [x] Archives BnF / Gallica (source historique alternative)

---

## Phase 12 — Contenus thématiques (non planifié)

### Post quotidien anniversaire de naissance

- [ ] Activer `births` dans `wikipedia_event_types` par défaut (ou en config recommandée)
- [ ] Job dédié ou critère de sélection prioritaire pour garantir un post "naissance" par jour
- [ ] Mise en page spécifique : mention "Né(e) il y a X ans" dans le banner date
- [ ] Option config `content.daily_birth_post: true` pour activer le mode post dédié (en plus du post événement principal)

