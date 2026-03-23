# Stratégie de tests

> Référence : [SPEC-3.x]

---

## Principes

- **Framework : pytest >= 8.0** + **pytest-asyncio >= 0.23**. Toutes les fonctions `async def test_*` et les fixtures `async def` sont gérées automatiquement via `asyncio_mode = auto` (voir section « Dépendances de test »).
- **Pas de mock sur la base de données** : les tests d'intégration utilisent une DB SQLite en mémoire (`:memory:`) — comportement identique à la prod, sans coût de fichier.
- **Mock sur les frontières réseau uniquement** : Wikipedia API, Meta Graph API, serveur d'images, Telegram — tout ce qui sort du processus.
- **Pas de test sur le scheduler APScheduler** : tester les fonctions de job directement (logique métier), pas le déclenchement cron.

---

## Structure

```
tests/
├── unit/
│   ├── test_image_generation.py   # génération d'image, wrapping, texture
│   ├── test_caption.py            # formatage de la légende, hashtags
│   ├── test_deduplication.py      # compute_content_hash, politique dedup
│   ├── test_token_manager.py      # calcul jours restants, seuils d'alerte
│   └── test_config.py             # validation Pydantic, cron invalide
├── integration/
│   ├── test_wikipedia_fetcher.py  # appel API Wikipedia avec httpx mock
│   ├── test_rss_fetcher.py        # parsing RSS, déduplication par URL, max_age
│   ├── test_instagram_publisher.py# flow container → publish, erreurs Meta
│   ├── test_facebook_publisher.py # flow /{page-id}/photos, erreurs Meta
│   ├── test_post_lifecycle.py     # machine à états + publication parallèle IG+FB
│   ├── test_scheduler_jobs.py     # logique des jobs (pas le déclenchement)
│   ├── test_selector.py           # select_event, select_article, get_effective_query_params
│   ├── test_recover_pending_posts.py  # recover_pending_posts — envoi/skip/publication
│   └── test_telegram_handlers.py  # handlers PTB avec Application.builder().build()
└── conftest.py                    # fixtures partagées (DB, config, mocks)
```

---

## Fixtures partagées (`conftest.py`)

### `db_engine`

Scope : `function` (engine isolé par test — évite la contamination d'état entre tests).

### `db_engine_static`

Scope : `function`. Engine créé avec `StaticPool` — requis uniquement pour `test_daily_counter_race_condition` et `test_daily_counter_exclusivity` (tests de concurrence `BEGIN EXCLUSIVE`). Pattern :

```python
create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
```

`StaticPool` force toutes les connexions async à partager le même objet `Connection` sous-jacent, permettant à `BEGIN EXCLUSIVE` d'agir sur la même DB partagée entre coroutines. Sans `StaticPool`, chaque coroutine crée une connexion vers une DB `:memory:` distincte — `BEGIN EXCLUSIVE` sur la seconde connexion deadlockerait et le compteur ne serait pas partagé. Même initialisation que `db_engine` (PRAGMAs + `Base.metadata.create_all` + création de `scheduler_state` + `set_engine`).

**Usage dans les tests :** `db_engine_static` est une fixture pytest injectée par paramètre de fonction, exactement comme `db_engine` :

```python
async def test_daily_counter_race_condition(db_engine_static, db_session):
    # db_engine_static est déjà initialisé et set_engine() a déjà été appelé
    ...
    results = await asyncio.gather(
        check_and_increment_daily_count(db_session, limit=25),
        check_and_increment_daily_count(db_session, limit=25),
    )
    assert sorted(results) == [False, True]
```

La fixture appelle `set_engine(engine)` lors de son initialisation — le test n'a pas besoin de le rappeler.

**`test_handle_approve_optimistic_lock` (dans `test_telegram_handlers.py`) utilise la même mécanique :** deux `handle_approve` lancés via `asyncio.gather` avec le même `post_id`. `db_engine_static` est obligatoire ici — sans `StaticPool`, chaque coroutine créerait une connexion vers une DB `:memory:` distincte, les deux trouveraient le post `pending_approval` et "gagneraient" toutes les deux. `StaticPool` garantit que les deux coroutines partagent le même état DB, rendant le verrou optimiste (`UPDATE ... WHERE status = 'pending_approval'`) effectivement testé.

Moteur SQLAlchemy `create_async_engine("sqlite+aiosqlite:///:memory:")`. Après création, activer les 3 PRAGMAs obligatoires via event listener synchrone (identique à la production — sans cela, les FK sont ignorées et les tests ne sont pas représentatifs) :

```python
from sqlalchemy import event
@event.listens_for(engine.sync_engine, "connect")
def set_pragmas(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA foreign_keys = ON")
    dbapi_conn.execute("PRAGMA journal_mode = WAL")
    dbapi_conn.execute("PRAGMA busy_timeout = 10000")
```

Création des tables — **pattern `run_sync` obligatoire** avec un engine async (appel direct `Base.metadata.create_all(engine)` lève `MissingGreenlet`) :

```python
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)
    await conn.execute(text(
        "CREATE TABLE IF NOT EXISTS scheduler_state "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '', "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    ))
```

`scheduler_state` est créée manuellement (pas un modèle ORM — non couvert par `Base.metadata`). DDL aligné avec DATABASE.md.

Appeler `set_engine(engine)` (`from ancnouv.scheduler.context import set_engine`) : rebinde la `_session_factory` dans `db/session.py` pour que `get_session()` retourne des sessions sur l'engine de test. Tous les handlers et jobs qui appellent `get_session()` utilisent ainsi la DB en mémoire sans modification de leur code.

> `set_engine` permet à `TokenManager` et aux handlers Telegram d'accéder à la DB en mémoire sans modification de leur code.

### `db_session`

Scope : `function`. Session `async_sessionmaker` (SQLAlchemy 2.x — **pas** `sessionmaker(class_=AsyncSession)` qui est la syntaxe 1.x). Utilise `expire_on_commit=False` pour éviter le rechargement implicite après commit dans les tests.

### `db_event`

Scope : `function`. Insère un `Event` valide en DB avec tous les champs NOT NULL renseignés : `year`, `month`, `day`, `description`, `source_lang`, `source`, `event_type`, `content_hash`, `fetched_at`, **`status="available"`**, **`published_count=0`**. Retourne l'`Event`. Requis pour créer des `Post` avec `event_id` non-NULL — la contrainte CHECK et la FK exigent que l'`Event` existe avant le `Post`.

### `db_article`

Scope : `function`. Insère un `RssArticle` valide en DB avec tous les champs NOT NULL renseignés : `title`, `article_url`, `feed_url`, `feed_name`, `summary`, `published_at`, `fetched_at`, **`status="available"`**, **`published_count=0`**. Valeur de `fetched_at` : `datetime.now(timezone.utc) - timedelta(days=91)` pour que l'article soit éligible selon la contrainte `min_delay_days=90` (défaut config). La valeur `91` est hardcodée — si la config de test surcharge `content.rss.min_delay_days` au-delà de `90`, les tests Mode B utilisant `db_article` échoueront. La fixture doit rester alignée avec la valeur par défaut de `RssFeedConfig.min_delay_days`. Retourne l'`RssArticle`. Requis pour les tests Mode B (`test_generate_post_mode_b_selects_rss`, `test_handle_reject_mode_b`).

> **[T-05] Correction recommandée — éviter le hardcode de 91j :**
>
> ```python
> @pytest.fixture
> def db_article(db_session, mock_config):
>     """Article RSS éligible : fetched_at antérieur au cutoff."""
>     delay = mock_config.content.rss.min_delay_days + 1  # dynamique
>     fetched_at = datetime.now(timezone.utc) - timedelta(days=delay)
>     return create_rss_article(db_session, fetched_at=fetched_at)
> ```

### `seed_token` / `seed_page_token`

Helpers locaux définis dans `conftest.py` (ou directement dans le fichier de test si non partagés). Ce sont des **fonctions async** (pas des fixtures pytest) appelées manuellement en début de test :

```python
async def seed_token(session: AsyncSession) -> MetaToken:
    token = MetaToken(token_kind="user_long", access_token="test_token", expires_at=datetime.now(timezone.utc) + timedelta(days=30))
    session.add(token); await session.commit(); return token

async def seed_page_token(session: AsyncSession) -> MetaToken:
    token = MetaToken(token_kind="page", access_token="test_page_token", expires_at=None)
    session.add(token); await session.commit(); return token
```

Appellées avec `await seed_token(db_session)` en début de test, avant d'instancier le publisher.

### `config`

`Config` minimale sans fichier YAML : `_env_file=None` n'est **pas** un paramètre du constructeur Pydantic Settings v2 (lève `TypeError`). Deux approches valides :
1. Créer une sous-classe avec `model_config = SettingsConfigDict(env_file=None, yaml_file=None)` — surcharge le `SettingsConfigDict` de `Config`.
2. Patcher via `mock.patch.dict(os.environ, {...})` avec les valeurs minimales requises, sans modifier la classe.

Champs minimaux requis : `TELEGRAM_BOT_TOKEN`, `META_APP_ID`, `META_APP_SECRET`, `IMAGE_HOSTING__PUBLIC_BASE_URL` (via `env_nested_delimiter="__"`), `TELEGRAM__AUTHORIZED_USER_IDS` (sérialisé JSON : `'[123456789]'`).

---

## Tests unitaires

### `test_config.py`

**Scénarios :**
- **`test_invalid_cron_raises`** : `Config` avec `scheduler.generation_cron="0 */25 * * *"` (heure hors plage) doit lever `ValueError` avec message contenant `"generation_cron invalide"`.
- **`test_no_platform_valid`** : `instagram.enabled=False` + `facebook.enabled=False` est une configuration valide — `validate_meta` ne doit pas lever d'erreur. État attendu pendant `auth meta`.
- **`test_platform_enabled_without_id_raises`** : `instagram.enabled=True` avec `user_id=""` doit lever `ValueError` avec message mentionnant `"instagram.user_id"`.

### `test_deduplication.py`

**Scénarios :**
- **`test_hash_normalizes_whitespace`** : `compute_content_hash("  Hello World  ")` == `compute_content_hash("Hello World")`.
- **`test_hash_normalizes_case`** : `compute_content_hash("ÉVÉNEMENT")` == `compute_content_hash("événement")`.
- **`test_hash_different_text`** : textes différents → hashs différents.

### `test_token_manager.py`

**Fonctions testées :** `days_until_expiry(expires: datetime) -> int`, `get_alert_threshold(remaining: int) -> int | None`, et `get_valid_token(session, kind, ...) -> str` (flux de refresh [RF-3.4.5]).

**Scénarios :**
- **`test_days_until_expiry_future`** : `now + 15j + 1h` → `15`. Le `+1h` rend l'assertion robuste aux exécutions à minuit (`.days` sur un timedelta à la microseconde peut donner 14 si pile à minuit).
- **`test_days_until_expiry_past`** : date passée → valeur négative.
- **`test_alert_threshold`** (paramétrisé) :
  - `31` → `None` (au-dessus de tous les seuils)
  - `30`, `14`, `7`, `3`, `1` → seuil correspondant (notification)
  - `15`, `5`, `2` → `None` (entre deux seuils — pas de spam quotidien)
  - `0`, `-1` → `0` (expiré ou expire aujourd'hui — message distinct de "1 jour restant")

**[T-03] Cas de test obligatoires pour TOUS les seuils (paramétrisé) :**

```python
@pytest.mark.parametrize("remaining,expected_threshold", [
    (35, None),   # au-delà des seuils
    (30, 30),     # seuil J-30
    (25, None),   # entre seuils
    (14, 14),     # seuil J-14
    (7, 7),       # seuil J-7 (+ refresh déclenché)
    (3, 3),       # seuil J-3 (+ refresh déclenché)
    (1, 1),       # seuil J-1 (alerte bloquante)
    (0, 0),       # expiré
    (-5, 0),      # expiré depuis plusieurs jours
])
def test_get_alert_threshold(remaining, expected_threshold):
    assert get_alert_threshold(remaining) == expected_threshold
```

**Scénarios `get_valid_token` (avec DB en mémoire + `db_session`) — [RF-3.4.5] :**

- **`test_get_valid_token_no_refresh_needed`** : token `user_long` avec `expires_at = now + 35j` en DB → `get_valid_token(session, "user_long")` retourne l'`access_token` sans appel réseau (aucune requête httpx).
- **`test_get_valid_token_triggers_refresh`** : token avec `expires_at = now + 25j` (dans les 30j) → `get_valid_token` appelle `POST /oauth/access_token` Meta (mock httpx retournant `{"access_token": "new_token", "expires_in": 5184000}`) → nouveau token persisté en DB, `"new_token"` retourné.
- **`test_get_valid_token_raises_when_refresh_fails`** : token expirant bientôt + mock Meta renvoie HTTP 400 `{"error": {"code": 190}}` → `TokenRefreshError` levée (ou `PublisherError` selon l'implémentation — vérifier `publisher/token_manager.py`).
- **`test_publications_suspended_on_refresh_failure`** : token avec `expires_at = now + 1j` (seuil critique) + refresh échoue → `scheduler_state[key="publications_suspended"]` mis à `"true"` en DB après appel. Teste la bascule de suspension [RF-3.4.5]. Utilise `db_session` pour vérifier l'état DB après exécution.

### `test_caption.py`

> **Import correct :** `compute_time_ago` et `format_historical_date` sont définis dans `ancnouv/utils/date_helpers.py`. Les importer depuis `ancnouv.utils.date_helpers`, pas depuis `ancnouv.generator.caption` — même si `caption.py` les réexporte, le chemin d'import direct est plus robuste.

**Scénarios `compute_time_ago` (avec `@freeze_time("2026-03-21")`) :**
- `(2026, 3, 10)` → `"Il y a moins d'un mois"` (branche `months == 0` — distincte de "Il y a X mois")
- `(2025, 12, 21)` → `"Il y a 3 mois"`
- `(2025, 3, 21)` → `"Il y a 1 an"`
- `(2024, 3, 21)` → `"Il y a 2 ans"` (accord pluriel — branche distincte de `"1 an"`)
- `(2016, 3, 21)` → `"Il y a 10 ans"`
- `(-44, 3, 15)` → `"Il y a 2070 ans"`

**Scénarios `format_historical_date` :**
- `(2016, 3, 21)` → `"21 mars 2016"`
- `(-44, 3, 15)` → `"15 mars 44 av. J.-C."`

**Scénarios `truncate_caption` :**
- Texte de 350 chars → résultat `<= 300` chars avec `"..."` en fin.
- Texte de exactement 300 chars → retourné sans modification (pas de `"..."`).

**Scénarios `format_caption` (avec `@freeze_time`) :**
- Caption avec `source_lang="fr"` → contient `"Wikipédia"` et des `"#"`.
- Caption avec `source_lang="en"` → contient `"Wikipedia (EN)"`.

**Scénarios `format_caption_rss` :**
- Caption RSS → contient le `feed_name` de l'article, le titre, et des `"#"`.
- Caption RSS avec résumé > 300 chars → résumé tronqué, total < 1024 chars.

### `test_image_generation.py`

**Scénarios `generate_image` :**
- Image générée : dimensions exactes `1080×1350` px.
- Fichier créé et non vide.
- Format JPEG valide : `Image.open(output_path).format == "JPEG"` et `Image.open(output_path).verify()` ne lève pas d'exception (fichier non corrompu — distinct de la qualité artistique).
- Appel sans thumbnail : pas d'exception.
- Appel avec thumbnail Pillow `320×213` px : pas d'exception, format `1080×1350`.

**Scénarios `fetch_thumbnail` :**
- `fetch_thumbnail(None)` → `None` sans exception.
- URL avec réponse HTTP 404 → `None` sans exception.

**Scénarios `truncate_for_image` :**
- Texte de 600 chars → résultat `<= 500` chars se terminant par `"…"`.
- Texte de 100 chars → retourné sans modification.

---

## Tests d'intégration

### Mocking des appels réseau

Utiliser `pytest-httpx` pour intercepter les appels `httpx.AsyncClient` :

```python
# Exemple de mock Wikipedia
httpx_mock.add_response(
    url="https://api.wikimedia.org/feed/v1/wikipedia/fr/onthisday/events/03/21",
    json={"events": [{"year": 1871, "text": "La Commune de Paris est proclamée.", "pages": []}]}
)
```

> **Query params dynamiques dans les mocks httpx** : `pytest-httpx >= 0.21` matche sur l'URL complète incluant les query params. Les appels de statut de container Instagram incluent `?fields=status_code&access_token=<token>` (token dynamique). Solution : mocker `_get_container_status` au niveau méthode via `patch.object` pour éviter les query params.

### `test_wikipedia_fetcher.py`

**Instanciation correcte :** `WikipediaFetcher(config=config)` — le paramètre `config: Config` est **obligatoire** dans le constructeur. `WikipediaFetcher()` sans argument lève `TypeError`. Utiliser la fixture `config`.

**Scénarios :**
- **`test_fetch_and_store_events`** : mock API Wikipedia avec 1 événement → `fetcher.fetch(date)` retourne 1 item, `fetcher.store(items, session)` retourne 1.
- **`test_fetch_deduplication`** : deux appels `store()` avec les mêmes items → deuxième appel retourne 0 (UNIQUE constraint).
- **`test_fetch_fallback_en`** : mock API FR avec 0 événements, mock API EN avec 2 événements → fallback déclenché, items stockés avec `source_lang="en"`.
- **`test_fetch_both_apis_fail`** : mock API FR → HTTP 500, mock API EN → HTTP 500 → `fetcher.fetch(date)` retourne `[]` (liste vide — pas d'exception propagée, l'échec réseau est loggué). `fetcher.store([], session)` retourne 0. Aucun `Event` créé en DB.

### `test_rss_fetcher.py`

**Interface correcte :** `RssFetcher()` sans arguments ; `fetcher.fetch_all(config)` (pas de `feed_url` au constructeur, pas de `fetch(target_date=...)`).

**Scénarios :**
- **`test_rss_fetch_parses_articles`** : flux RSS avec 2 items valides → `fetch_all(config)` retourne 2 articles avec `title` et `article_url` corrects.
- **`test_rss_store_deduplicates_by_url`** : deux `store()` successifs avec les mêmes articles → premier retourne 2, deuxième retourne 0.
- **`test_rss_max_age_filters_old_articles`** : mock `_fetch_feed` (méthode interne — **pas** `_parse_feed` qui n'existe pas) avec un article > `max_age_days` et un récent → seul le récent est retourné.

> **Format de retour de `_fetch_feed` (pour le mock) :** liste de dicts feedparser. Chaque entrée contient au minimum : `title: str`, `link: str`, `summary: str`, `published_parsed: time.struct_time | None`. Exemple :
> ```python
> import time
> [
>     {
>         "title": "Article récent",
>         "link": "https://example.com/recent",
>         "summary": "Résumé court.",
>         "published_parsed": time.strptime("2026-01-01", "%Y-%m-%d"),
>     }
> ]
> ```
> Le mock doit être appliqué sur l'instance : `patch.object(fetcher, "_fetch_feed", return_value=[...])` (méthode synchrone).

### `test_instagram_publisher.py`

**Fixtures :** `seed_token(db_session)` insère `MetaToken(token_kind="user_long", expires_at=now+30j)`. Instancier `InstagramPublisher(ig_user_id=..., token_manager=TokenManager(...), api_version="v21.0")`.

**Scénarios :**
- **`test_publish_success`** : mock `POST /{ig_user_id}/media` → `{"id": "container_123"}`, mock `_get_container_status` → `"FINISHED"`, mock `POST /{ig_user_id}/media_publish` → `{"id": "post_456"}`. Résultat attendu : `"post_456"`, `post.ig_container_id == "container_123"` après commit.
- **`test_publish_container_creation_error`** : mock `POST /{ig_user_id}/media` → `{"error": {"message": "...", "code": 190}}`. Attendu : `PublisherError` avec `"190"` dans le message.

- **`test_publish_container_processing_then_finished`** : mock `_get_container_status` retourne `"PROCESSING"` au premier appel, `"FINISHED"` au second → `publish(post, session, image_url)` réussit. Vérifier que `_get_container_status` est appelé exactement deux fois (`mock.call_count == 2`). Utiliser `AsyncMock(side_effect=["PROCESSING", "FINISHED"])`.

> **Mocks async :** mocker `_get_container_status` (coroutine) avec `AsyncMock`, pas `MagicMock`. `MagicMock` retourne un objet non-awaitable → `TypeError: object MagicMock can't be used in 'await' expression`. Utiliser `patch.object(publisher, "_get_container_status", new=AsyncMock(return_value="FINISHED"))`.

### `test_facebook_publisher.py`

**Fixtures :** `seed_page_token(db_session)` insère `MetaToken(token_kind="page", expires_at=None)` (token permanent).

**Scénarios :**
- **`test_facebook_publish_success`** : mock `POST /{page_id}/photos` → `{"post_id": "page_123_post_789"}`. Résultat attendu : `"page_123_post_789"`.
- **`test_facebook_publish_error`** : mock avec corps `{"error": {..., "code": 190}}` → `PublisherError`.
- **`test_facebook_no_page_token_raises`** : aucun token en DB → `TokenExpiredError`.

### `test_post_lifecycle.py`

**Scénarios machine à états :**
- **`test_post_approved_then_published`** : `Post` créé avec `status="pending_approval"` → passe en `"approved"` → passe en `"published"` avec `instagram_post_id`. Vérifier après `session.get(Post, id)`.
- **`test_publish_both_platforms_success`** : mock IG + FB → `post.status == "published"`, les deux `post_id` stockés, `error_message is None`.
- **`test_publish_instagram_ok_facebook_fails`** : IG OK, FB retourne `{"error": ...}` → `post.status == "published"` (IG OK suffit), `facebook_error` renseigné pour `/retry_fb`.
- **`test_publish_both_platforms_fail`** : les deux échouent → `post.status == "error"`, `error_message` renseigné.
- **`test_daily_counter_race_condition`** : `asyncio.gather` avec 2 appels simultanés à `check_and_increment_daily_count` au compteur 24/25 → exactement `[False, True]` (le verrou `BEGIN EXCLUSIVE` garantit l'atomicité). **Prérequis :** l'engine de test doit être créé avec `StaticPool` (`create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)`) — sans `StaticPool`, chaque coroutine utiliserait une connexion distincte sur `:memory:`, les deux verraient une DB vide et `BEGIN EXCLUSIVE` sur la seconde connexion deadlockerait. `StaticPool` force toutes les connexions à partager le même objet `Connection`. Ajouter aussi `test_daily_counter_exclusivity` (test d'intégration dédié au pattern aiosqlite ≥ 0.20.0, voir SCHEDULER.md).
- **`test_generate_post_creates_post`** : mock `select_event`, `fetch_thumbnail`, `generate_image`, `get_config` → `generate_post(session)` retourne un `Post` avec `status="pending_approval"`, `event_id` correct, `caption` et `image_path` renseignés.
- **`test_generate_post_mode_b_selects_rss`** : avec `mix_ratio` élevé forçant Mode B (mock `random.random` retournant 0.1 < mix_ratio), mock `select_article` retournant un `RssArticle` → `generate_post(session)` retourne un `Post` avec `article_id` non-NULL et `event_id` NULL. Vérifier que `select_event` n'est pas appelé.
- **`test_generate_post_hybrid_fallback`** : Mode B décidé mais `select_article` retourne `None` (aucun article disponible) → fallback Mode A : `select_event` appelé, post avec `event_id` non-NULL retourné. Valide que le fallback hybride [DS-3] est bien implémenté.
- **`test_generate_post_no_stock`** : `select_event` retourne `None` **et** `select_article` retourne `None` (toutes sources épuisées) → `generate_post(session)` retourne `None`. Aucun `Post` créé en DB (vérifier `await session.execute(select(func.count(Post.id)))` == 0).
- **`test_generate_post_auto_publish`** : config avec `telegram.auto_publish=True`, mock `select_event`, `fetch_thumbnail`, `generate_image`, `publish_to_all_platforms` → `generate_post` (ou la logique job dans `test_scheduler_jobs.py`) retourne un `Post` avec `status="published"` sans passer par `status="pending_approval"`. Vérifier que `send_approval_request` n'est **pas** appelé.
- **`test_post_publishing_state_recoverable`** : post inséré directement avec `status="publishing"` (simulation d'un crash pendant la publication) → `recover_pending_posts(session, bot)` le détecte et appelle `_publish_approved_post` (mock `AsyncMock`). Vérifie que l'état `"publishing"` est traité comme récupérable — un post bloqué dans cet état n'est pas abandonné au redémarrage [ARCH-C5].

### `test_scheduler_jobs.py`

**Scénarios :**
- **`test_job_check_expired_marks_expired`** : post créé avec `created_at = now - 49h` → après `job_check_expired()`, `post.status == "expired"`. Post créé avec `created_at = now - 1h` → reste `"pending_approval"`. Mocker `get_config()`, `get_bot_app()`, `get_session()`. **Chemin de patch de `get_bot_app()`** : `"ancnouv.scheduler.jobs.get_bot_app"` (là où il est importé dans `jobs.py`, pas là où il est défini dans `context.py`). Un chemin incorrect laisse `get_bot_app()` appeler `RuntimeError` si le contexte n'est pas initialisé.

> **[T-04] Tests requis pour RF-3.3.3 :**
>
> ```python
> async def test_job_check_expired_marks_post_expired(db_session):
>     # Créer un post pending_approval créé il y a 49h
>     # Exécuter job_check_expired
>     # Vérifier : post.status == "expired"
>     # Vérifier : notification Telegram envoyée (mock notify_all)
>     # Vérifier : event.status == "available" (pas bloqué par l'expiration)
>
> async def test_job_check_expired_disables_telegram_buttons(db_session):
>     # Vérifier que bot.edit_message_reply_markup est appelé pour chaque message_id
> ```
- **`test_job_check_token_sends_alert_at_threshold`** : token avec `expires_at = now + 7j`, `last_alert_days_threshold=None` → `job_check_token()` doit appeler `notify_all` avec un message contenant `"7"`. Le mock de `notify_all` doit être une `async def` (pas un lambda synchrone) — un lambda retournant une coroutine sans await ne déclenchera jamais la notification.
- **`test_job_check_token_no_duplicate_alert`** : token avec `last_alert_days_threshold=7` déjà renseigné → `job_check_token()` ne renvoie pas l'alerte (anti-doublon).

> **[T-08] Test de `max_pending_posts` :**
>
> ```python
> async def test_job_generate_skips_when_max_pending_reached(db_session, mock_config):
>     # Créer max_pending_posts posts en statut pending_approval
>     # Exécuter job_generate
>     # Vérifier : generate_post N'EST PAS appelé
>     # Vérifier : aucun nouveau post créé
> ```

> **Patch de `notify_all` dans ce fichier :** `"ancnouv.scheduler.jobs.notify_all"` (là où il est importé dans `jobs.py`, pas là où il est défini dans `notifications.py`). Chemin incorrect → patch sans effet, appel réel tenté en test.

### `test_selector.py`

Tests directs de `generator/selector.py` — les fonctions `select_event`, `select_article`, `get_effective_query_params` ne sont pas couvertes par les tests de haut niveau.

**Scénarios :**
- **`test_select_event_returns_candidate`** : DB avec 2 events pour (month=3, day=21), `status="available"`, `published_count=0` → `select_event(session, date(2026, 3, 21))` retourne un des deux events.
- **`test_select_event_respects_dedup_never`** : event avec `published_count=1` → non retourné (politique `"never"`).
- **`test_select_event_respects_dedup_window`** : event publié il y a 300j, politique `"window"` avec `dedup_window=365` → non retourné. Publié il y a 400j → retourné.
- **`test_select_article_respects_delay`** : article avec `fetched_at = now - 80j` (< `min_delay_days=90`) → non retourné. `fetched_at = now - 91j` → retourné.
- **`test_get_effective_query_params_escalation_0`** : `escalation_level=0` → `event_types=["events"]`, `use_fallback_en=False`, `dedup_policy` selon config.
- **`test_get_effective_query_params_escalation_1`** : `escalation_level=1` → `event_types` étendu (tous les types disponibles, selon DATA_SOURCES.md §escalade niveau 1), `use_fallback_en=False`.
- **`test_get_effective_query_params_escalation_2`** : `escalation_level=2` → `use_fallback_en=True`.
- **`test_get_effective_query_params_escalation_3`** : `escalation_level=3` → `dedup_policy="window"` forcé (indépendamment de la config), `use_fallback_en=True` (cumul des niveaux précédents — voir DATA_SOURCES.md §escalade niveau 3).
- **`test_get_effective_query_params_escalation_4`** : `escalation_level=4` → politique la plus permissive : `dedup_policy="always"` (ou `"none"` selon l'implémentation), tous les types, `use_fallback_en=True`. Vérifie qu'un événement normalement exclu (ex. `published_count > 0`, politique stricte) est retourné.

> **[T-09] Tests de politique end-to-end via `generate_post()` :**
>
> ```python
> async def test_generate_post_with_window_policy(db_session, mock_config):
>     # Configurer policy="window", window=365
>     # Créer un événement avec published_count=1, last_used_at=400j ago
>     # Appeler generate_post()
>     # Vérifier : l'événement est sélectionné (last_used_at > window)
>
> async def test_generate_post_with_always_policy(db_session, mock_config):
>     # Configurer policy="always"
>     # Créer un événement avec published_count=5
>     # Appeler generate_post()
>     # Vérifier : l'événement est sélectionné (aucun filtre de dédup)
> ```

### `test_recover_pending_posts.py`

- **`test_recover_sends_pending_posts`** : 2 posts `pending_approval` avec `telegram_message_ids={}` → `recover_pending_posts` appelle `bot.send_photo` 2 fois, `post.telegram_message_ids` renseigné après exécution.
- **`test_recover_skips_already_sent`** : post `pending_approval` avec `telegram_message_ids={"123": 456}` → `recover_pending_posts` n'appelle **pas** `bot.send_photo` pour ce post (anti-duplication).
- **`test_recover_publishes_approved_with_url`** : post `approved` avec `image_public_url` non-NULL → `_publish_approved_post` appelé une fois (mock `AsyncMock`). Le statut final du post (`"published"` ou `"error"`) est géré par `_publish_approved_post` — dans ce test, vérifier uniquement que `_publish_approved_post` est appelé avec le bon post.
- **`test_recover_approved_without_url_notifies`** : post `approved` sans `image_public_url` (ou `image_public_url=None`) → `notify_all` appelé avec un message contenant `"url"` ou `"image"` (correspondance partielle avec `in`), `_publish_approved_post` non appelé, `post.status == "error"` après exécution.

> **[T-10]** Le test de récupération des posts `publishing` est dans ce fichier (pas `test_post_lifecycle.py`) :
>
> ```python
> async def test_recover_publishing_posts_on_restart(db_session):
>     # Créer un post status='publishing' (crash mid-publish simulé)
>     # Appeler recover_pending_posts
>     # Vérifier : post.status == 'approved'
> ```

### `test_telegram_handlers.py`

Handlers testés directement en appelant les fonctions (`cmd_pause`, `cmd_resume`, `cmd_status`, `handle_approve`, `handle_reject`, `handle_skip`) avec des `AsyncMock` PTB.

**Mocking PTB :** construire un faux `context` avec `bot_data["config"] = config` — sans cela, `authorized_only` lève `KeyError: 'config'` dès le premier handler.

**`TokenManager` — instanciation :** `TokenManager(config.meta_app_id, config.meta_app_secret)` (voir INSTAGRAM_API.md — `TokenManager.__init__`). Pas d'argument `session` au constructeur — la session est passée à chaque appel de `get_valid_token(session, ...)`. Le paramètre `notify_fn` est omis dans les tests (défaut `None`).

**Assertions post-handler — session refresh :** après qu'un handler a commité via sa propre session, vérifier l'état en DB avec la session de la fixture `db_session` nécessite un `await db_session.refresh(obj)` (ou une nouvelle requête) — sans cela, `db_session` peut retourner l'état en cache d'avant le commit du handler.

```python
context = MagicMock()
context.bot_data = {"config": config}
context.bot = AsyncMock()
```

**[T-02] Chemins de patch corrects pour `notify_all` selon le fichier de test :**
- Dans `test_scheduler_jobs.py` : `mock.patch("ancnouv.scheduler.jobs.notify_all")`
- Dans `test_recover_pending_posts.py` : `mock.patch("ancnouv.scheduler.jobs.notify_all")`
- Dans `test_telegram_handlers.py` : `mock.patch("ancnouv.bot.handlers.notify_all")`

Un chemin incorrect → patch sans effet, appel Telegram réel en test.

**Scénarios :**
- **`test_cmd_pause_sets_flag`** : **pré-condition** — la ligne `scheduler_state` avec `key="paused"` doit exister en DB avant l'appel (INSERT dans la fixture ou début du test) ; `/pause` → `scheduler_state.paused == "true"` en DB. `reply_text` appelé une fois.
- **`test_unauthorized_user_rejected`** : `user_id=999999` (non autorisé) → `reply_text("Accès non autorisé.")` appelé, aucun autre effet de bord.
- **`test_cmd_resume_clears_flag`** : **pré-condition** — ligne `paused="true"` insérée en DB avant l'appel ; `/resume` → `paused="false"` en DB.
- **`test_handle_reject_mode_a`** : post avec `event_id` non-NULL → après `handle_reject`, `post.status == "rejected"` et `event.status == "blocked"`. Vérifier aussi que `rss_articles` n'est pas modifié.
- **`test_handle_reject_mode_b`** : post avec `article_id` non-NULL → après `handle_reject`, `post.status == "rejected"` et `rss_article.status == "blocked"` (pas `"rejected"` — vérifier le CHECK constraint). Récupérer l'état mis à jour : `await db_session.refresh(rss_article)` avant l'assertion (le handler a commité via sa propre session, `db_session` peut avoir l'état en cache).
- **`test_handle_skip_generates_next`** : post skippé → `post.status == "skipped"`, `generate_post` appelé une fois. Avec mock `generate_post` retournant `None` : `send_approval_request` non appelé, `notify_all` appelé avec un message contenant `"Aucun autre événement disponible"` (assertion partielle avec `in` — le texte exact peut contenir des emojis ou contexte supplémentaire).

> **[T-06] Test complet du flux `handle_skip` :**
>
> ```python
> async def test_handle_skip_sends_new_approval_request(db_session, mock_bot):
>     # 1. Créer un post pending_approval
>     # 2. Mock generate_post pour retourner un nouveau post
>     # 3. Appeler handle_skip
>     # 4. Vérifier : post original status == "skipped"
>     # 5. Vérifier : generate_post appelé
>     # 6. Vérifier : send_approval_request appelé avec le nouveau post
> ```

- **`test_handle_approve_daily_limit_queues`** : `check_and_increment_daily_count` retourne `False` (limite atteinte, mock) → `post.status == "queued"` après commit, `edit_message_text` appelé (pas `notify_all`).

> **[T-11]** Ce test vérifie que `handle_approve` passe le post en `"queued"` quand la limite est atteinte. En v1, le post restera `"queued"` jusqu'à une intervention manuelle (JOB-7 non actif en v1).

- **`test_handle_approve_optimistic_lock`** : deux appels simultanés à `handle_approve` avec le même `post_id` → exactement un résultat `"approved"`, l'autre retourne silencieusement (0 lignes affectées par le `UPDATE`). **Prérequis :** utiliser `db_engine_static` (fixture avec `StaticPool`) comme décrit dans la section `db_engine_static` — `asyncio.gather(handle_approve(...), handle_approve(...))` nécessite que les deux coroutines partagent la même connexion DB `:memory:`.
- **`test_handle_approve_sets_approved_at`** : `handle_approve` appelé sur post `pending_approval` → `post.approved_at IS NOT NULL` après commit. `post.status == "approved"`.
- **`test_handle_approve_image_path_null`** : `post.image_path = None` → `handle_approve` édite le message avec "image introuvable", `_publish_approved_post` non appelé.

---

> **`RssFeedItem` (dataclass) vs `RssArticle` (ORM) :** `ancnouv.fetchers.base.RssFeedItem` est la dataclass de transport (retournée par `RssFetcher.fetch_all`), `ancnouv.db.models.RssArticle` est le modèle ORM (stocké en DB). Le mock de `select_article` dans `test_generate_post_mode_b_selects_rss` doit retourner un objet **ORM** (`db.models.RssArticle`) avec un `id` non-NULL — retourner la dataclass lèverait `IntegrityError` à l'insertion. Utiliser la fixture `db_article` qui retourne l'objet ORM persisté.

## Dépendances de test

```txt
# requirements-dev.txt
pytest>=8.0
pytest-asyncio>=0.23
pytest-httpx>=0.30
pytest-cov>=5.0       # couverture de code
aiosqlite>=0.20
freezegun>=1.4        # mock de datetime dans test_caption.py — la version 1.4+ est requise pour le support des dates av. J.-C. (années négatives via `@freeze_time("2026-03-21")` combiné à des calculs en années négatives dans `compute_time_ago` — ne pas réduire cette contrainte)
# [T-13] feedparser est une dépendance de prod (dans requirements.txt) — NE PAS dupliquer ici
# Les tests utilisent feedparser via les mocks de _fetch_feed (dicts au format feedparser)
# La version est déjà épinglée dans requirements.txt : feedparser==6.*
Pillow>=10.0          # utilisé dans test_image_generation.py
```

Installer les dépendances de test :
```sh
pip install -r requirements.txt -r requirements-dev.txt
```

Configuration dans `pytest.ini` (à la racine du projet) :
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

Alternative `pyproject.toml` :
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

# [T-12] Seuils de couverture
[tool.coverage.report]
fail_under = 80
# Modules critiques ciblant 90% : publisher/, bot/handlers.py, generator/
# Configurer via --cov-fail-under séparé par module si pytest-cov le supporte
```

> **[T-12]** La CI retourne un code non-zéro si la couverture globale passe sous 80% (`--cov-fail-under=80`). Modules critiques ciblant 90% : `publisher/`, `bot/handlers.py`, `generator/`.

> `asyncio_mode = auto` s'applique aux **fonctions de test** (`async def test_*`) **et aux fixtures async** (`async def db_engine`, `async def db_session`, etc.) — sans cette config, les fixtures async avec `scope="function"` sont silencieusement exécutées de manière synchrone, ce qui lève `RuntimeError: no running event loop`. Les décorateurs `@pytest.mark.asyncio` deviennent redondants mais inoffensifs — les conserver pour la lisibilité. Sans `asyncio_mode = auto`, chaque test async doit être décoré individuellement, et les fixtures async nécessitent `@pytest_asyncio.fixture` (au lieu de `@pytest.fixture`) — source de confusion fréquente.

---

## Couverture minimale cible

| Module | Cible |
|--------|-------|
| `db/models.py` + migrations | 90% |
| `fetchers/wikipedia.py` | 80% |
| `fetchers/rss.py` | 80% |
| `publisher/instagram.py` | 85% |
| `publisher/facebook.py` | 85% |
| `publisher/__init__.py` (`publish_to_all_platforms`) | 90% |
| `publisher/token_manager.py` | 85% |
| `scheduler/jobs.py` (logique) | 80% |
| `bot/handlers.py` | 70% |
| `bot/notifications.py` | 75% |
| `generator/__init__.py` (`generate_post`) | 80% |
| `generator/selector.py` | 85% |
| `config.py` (validation) | 95% |

```bash
pytest --cov=ancnouv --cov-report=term-missing --cov-fail-under=80
```

> `--cov-fail-under=80` : la CI retourne un code non-zéro si la couverture globale passe sous 80%. Seuil conservateur — les modules critiques ont des cibles individuelles supérieures (voir table ci-dessus).

---

## Ce qu'on ne teste PAS

- Le déclenchement des jobs APScheduler (timing cron) — trop couplé au temps réel
- La génération d'image visuelle (qualité artistique) — testée manuellement
- Les appels directs au serveur d'images en production — mockés dans tous les tests
- **Les commandes CLI** (`cli/auth.py`, `cli/fetch.py`, `cli/generate.py`, `cli/health.py`, `cli/escalation.py`, `cli/setup.py`, `cli/test_commands.py`) — exclues délibérément : elles sont des orchestrateurs minces qui délèguent aux couches métier déjà testées. Les smoke tests CLI nécessiteraient un process `subprocess.run(["python", "-m", "ancnouv", ...])` avec une DB en mémoire et des mocks réseau — coût d'infrastructure disproportionné pour la valeur. `test_config.py` couvre les validations Pydantic ; les jobs et handlers sont testés dans `test_scheduler_jobs.py` et `test_telegram_handlers.py`.

> **[T-01] Exception — smoke test pour `_dispatch` :** même si les commandes sont des orchestrateurs minces, tester que `_dispatch` ne lève pas d'exception non gérée sur des arguments invalides :
>
> ```python
> # tests/unit/test_cli.py
> def test_dispatch_unknown_command():
>     with pytest.raises(SystemExit) as exc_info:
>         _dispatch(argparse.Namespace(command="unknown"))
>     assert exc_info.value.code == 1
>
> def test_dispatch_catches_system_exit():
>     """_dispatch doit catcher BaseException (incluant SystemExit(2) d'argparse)."""
>     from ancnouv.__main__ import _dispatch
>     # _dispatch doit retourner un code de sortie propre, jamais propager BaseException
>     ...
> ```

> **[T-07]** `_dispatch` doit catcher `BaseException` (incluant `SystemExit(2)` d'argparse) pour garantir un code de sortie propre — jamais de traceback brut.
- **Le test d'intégration bout en bout automatisé** (Wikipedia → génération d'image → approbation Telegram → publication Meta) — exclu délibérément des tests automatisés. Ce flux nécessite des credentials réels, un réseau, et des quotas API. Valider ce flux en staging avec les comptes de test dédiés (voir section « Environnement de staging »).

> **Note :** les callbacks inline Telegram (`handle_approve`, `handle_reject`, `handle_skip`) sont **entièrement testables** avec `AsyncMock` PTB — ils ne nécessitent pas de bot réel. Les marquer comme "testés manuellement uniquement" est une décision de sous-couverture délibérée, pas une contrainte technique.

---

## Contrainte : db_engine vs db_engine_static

**[T-15]** Ne jamais utiliser `db_engine` ET `db_engine_static` dans le même test. Les deux appellent `set_engine()` — la seconde écrase la `_session_factory` de la première.

Tests nécessitant `db_engine_static` :
- `test_daily_counter_race_condition`
- `test_daily_counter_exclusivity`
- `test_handle_approve_optimistic_lock`

Tous les autres tests utilisent `db_engine`.

---

## Environnement de staging

**[T-14]** Pour isoler staging de production :

1. Variable d'environnement : `ANCNOUV_DB_PATH=/data/ancnouv_staging.db`
2. Fichier config séparé : `config.staging.yml` + `ANCNOUV_CONFIG_PATH=config.staging.yml`
3. Bot Telegram distinct (token différent)
4. Comptes Instagram/Facebook de test distincts

Avant déploiement, tester en conditions réelles avec :
- Un bot Telegram de test (token distinct)
- Une App Meta en mode **développement** (pas de review requise pour les comptes de test)
- Un compte Instagram de test lié à la Page Facebook de test

Cela permet de valider le flow complet (Wikipedia → image → Telegram → Instagram) sans risquer de publier sur les comptes de production.

---

## CI minimale — [T-16]

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: pytest --cov=ancnouv --cov-fail-under=80
```
