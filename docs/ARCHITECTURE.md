# Architecture Technique

> Référence : [SPEC-3]

---

## Dépendances Python (versions épinglées)

> Python 3.12+ requis (SPEC C-4.1.2). Non compatible avec Python 3.10 ou 3.11.

### requirements.txt

```
apscheduler~=3.10          # 3.x — INCOMPATIBLE avec 4.x (SQLAlchemyJobStore supprimé)
python-telegram-bot~=20.0  # 20.x — INCOMPATIBLE avec 13.x (API async complètement réécrite)
sqlalchemy~=2.0            # 2.x — INCOMPATIBLE avec 1.x (API ORM changée)
aiosqlite>=0.20.0          # ≥ 0.20.0 OBLIGATOIRE pour BEGIN EXCLUSIVE (voir section Concurrence)
numpy>=1.24,<2             # < 2 OBLIGATOIRE (np.random.randint API modifiée en 2.x)
pillow>=10.0               # ≥ 10.0 recommandé
feedparser>=6.0
httpx>=0.25
aiohttp>=3.9
pydantic-settings[yaml]>=2.0   # [yaml] obligatoire pour lire config.yml
alembic>=1.13
```

---

## Vue d'ensemble

```
┌──────────────────────────────────────────────────────────────┐
│                     SCHEDULER (APScheduler)                  │
│   job_fetch_wiki | job_fetch_rss | job_generate | job_cleanup│
└────┬──────────────┬──────────────┬──────────────┬────────────┘
     │              │              │              │
     ▼              ▼              ▼              ▼
┌─────────┐   ┌──────────┐  ┌──────────┐  ┌──────────────┐
│FETCHERS │   │FETCHERS  │  │GENERATOR │  │MAINTENANCE   │
│Wikipedia│   │RSS       │  │Image     │  │Expired posts │
│(Mode A) │   │(Mode B)  │  │Caption   │  │Old images    │
│         │   │(optionnel│  │Selector  │  │Token refresh │
└────┬────┘   └────┬─────┘  └────┬─────┘  └──────────────┘
     │              │              │
     └──────────────┴──────────────┘
                    │
                    ▼
     ┌──────────────────────────────┐
     │      DATABASE (SQLite)       │
     │  events | rss_articles       │
     │  posts  | tokens             │
     └──────────────┬───────────────┘
                    │
                    ▼
          ┌─────────────────┐
          │  TELEGRAM BOT   │
          │  (validation)   │
          │  ✅ ❌ ⏭ ✏️    │
          └────────┬────────┘
                   │ ✅ approuvé
                   ▼
          ┌─────────────────┐
          │  IMAGE HOSTING  │
          │  local / remote │
          └────────┬────────┘
                   │ url publique
                   ▼
          ┌─────────────────────────────────┐
          │        PUBLISHER                │
          │  ┌─────────────┐ ┌───────────┐  │
          │  │ Instagram   │ │ Facebook  │  │
          │  │ Graph API   │ │ Graph API │  │
          │  │ (container) │ │ (/photos) │  │
          │  └─────────────┘ └───────────┘  │
          │    (parallèle asyncio)           │
          └─────────────────────────────────┘
```

> **Note :** "MAINTENANCE" regroupe JOB-4 (`job_check_expired`), JOB-5 (`job_check_token`), JOB-6 (`job_cleanup`). Voir SCHEDULER.md pour le détail de chaque job.

> **[ARCH-05] JOB-2 (RSS fetch) :** l'intervalle de collecte RSS de 6h (`timedelta(hours=6)`) n'est pas configurable en v1. Pour modifier cet intervalle, éditer `scheduler/__init__.py`. Justification : la fréquence de collecte RSS n'a pas besoin d'être ajustée souvent — les flux RSS changent à une fréquence de l'ordre de l'heure, pas de la minute.

> **[ARCH-06] `job_cleanup` :** nettoie les fichiers image locaux des posts finalisés (`published`, `rejected`, `expired`, `skipped`) dont `COALESCE(published_at, created_at) < now - config.content.image_retention_days`. Tourne à `0 3 * * *` (3h du matin, MemoryJobStore). Ne touche pas aux enregistrements DB — uniquement les fichiers `data/images/*.jpg`.

> **Architecture push (pas pull) :** la flèche DATABASE → TELEGRAM BOT dans le diagramme représente le flux de données (le post passe par la DB avant d'atteindre le bot), non un mécanisme de poll. L'architecture réelle est **push** : JOB-3 (`job_generate`) génère un post, l'insère en DB, puis appelle `send_approval_request(bot, post)` activement. Le bot Telegram ne poll pas la DB — il reçoit le post via cet appel direct.

> **[ARCH-m2] Note (référence anticipée) :** `ImageHostingError` est dans `NON_RETRIABLE` (défini dans `utils/retry.py` — voir section [Retry pattern](#retry-pattern)) car `upload_to_remote` gère déjà ses propres retries (x3) en interne. Inclure `ImageHostingError` dans `NON_RETRIABLE` évite un double-wrapping : un échec définitif de `upload_to_remote` remonte immédiatement sans être ré-enveloppé par `with_retry`.

> **[ARCH-m1] `IMAGE HOSTING` dans le diagramme :** le bloc `IMAGE HOSTING` dans la vue d'ensemble représente **deux choses distinctes** selon le backend : (a) `publisher/image_hosting.py` — code Python intégré au processus principal (fonctions `serve_locally`, `upload_to_remote`, `upload_image`) ; (b) le container Docker `ancnouv-images` (service `ancnouv-images` dans `docker-compose.yml`) — processus séparé exécutant `run_image_server`. Ces deux composants partagent le même port et le même token mais ne coexistent jamais dans le même processus.

> **[ARCH-M3] Dépendances inter-modules — analyse des cycles potentiels :**
> - `scheduler/jobs.py` → `bot/notifications.py` (`send_approval_request`, `notify_all`) — **sens unique** : le scheduler utilise le bot pour les notifications
> - `bot/handlers.py` → `scheduler/jobs.py` (`check_and_increment_daily_count`, `recover_pending_posts` indirect) — **sens unique** : les handlers utilisent les fonctions de job
> - `bot/handlers.py` → `bot/notifications.py` — **intra-module, pas de cycle**
> - `scheduler/jobs.py` → `bot/handlers.py` : **NON** — les jobs n'importent pas les handlers (ce serait un cycle)
>
> Il n'existe **pas** de cycle d'importation en v1. Le risque serait de placer dans `scheduler/jobs.py` un import vers `bot/handlers.py`. La règle à respecter : `handlers.py` peut importer de `jobs.py`, jamais l'inverse.

---

## Lexique des types (éviter les confusions)

| Type | Module | Rôle |
|------|--------|------|
| `RssFeedItem` | `ancnouv.fetchers.base` | Dataclass de transport — résultat de `RssFetcher.fetch_all()` |
| `RssArticle` | `ancnouv.db.models` | Modèle ORM SQLAlchemy — enregistrement en DB |
| `rss_articles` | SQL | Nom de la table en base de données |

---

## Structure des modules

**`__main__.py` — `_dispatch_inner`** [ARCH-C1] :

```python
def _dispatch_inner(args: argparse.Namespace, config: Config) -> int: ...
```

Reçoit les `args` parsés et la config chargée. Dispatche vers la commande appropriée selon `args.command` (ex : `"start"` → `run(config)`, `"db"` → délègue à `db/cli.py`, `"setup"` → délègue à `cli/setup.py`). Retourne un code de retour entier (0 = succès, 1 = erreur). `_dispatch_inner` est appelée depuis `__main__.py:main()` après le chargement de `Config` — c'est le seul endroit où `Config` est instanciée.

> **[ARCH-m5] `db/cli.py` vs `cli/` :** les commandes de base de données (`db init`, `db migrate`, `db status`, `db backup`, `db reset`) sont dans `db/cli.py` car elles sont intimement liées au module de base de données et partagent ses imports. Les autres commandes CLI (`auth`, `setup`, `fetch`, `health`, etc.) sont dans `cli/` car elles n'ont pas de dépendance forte sur un module unique. `_dispatch_inner` route les deux types de manière uniforme.

```
ancnouv/
├── __init__.py
├── __main__.py              # Entry point CLI (argparse) — voir docs/CLI.md
├── exceptions.py            # Hiérarchie des exceptions custom
├── config.py                # Pydantic Settings — chargement + validation config
├── scheduler/
│   ├── __init__.py          # APScheduler — create_scheduler(), main_async()
│   ├── jobs.py              # Fonctions de job : job_fetch_wiki, job_generate,
│   │                        # check_and_increment_daily_count, recover_pending_posts, …
│   └── context.py           # Contexte partagé (config, bot_app, engine) — singletons module-level
│
├── db/
│   ├── __init__.py
│   ├── cli.py               # Commandes db (init, migrate, status, backup, reset)
│   ├── models.py            # Modèles SQLAlchemy (ORM) — voir DATABASE.md
│   ├── session.py           # init_db(), get_session() — voir DATABASE.md
│   ├── utils.py             # compute_content_hash, get_scheduler_state, set_scheduler_state
│   └── migrations/          # Scripts Alembic
│       ├── env.py
│       └── versions/
│
├── fetchers/
│   ├── __init__.py
│   ├── base.py              # Dataclasses RawContentItem, RssArticle (transport) ; BaseFetcher ABC
│   ├── wikipedia.py         # WikipediaFetcher — Wikipedia "On This Day" API
│   └── rss.py               # RssFetcher — collecte RSS (Mode B, optionnel)
│
├── generator/
│   ├── __init__.py          # generate_post() — orchestration complète
│   ├── selector.py          # select_event(), select_article(), get_effective_query_params()
│   ├── image.py             # Génération d'image avec Pillow
│   └── caption.py           # Formatage de la légende Instagram
│
├── bot/
│   ├── __init__.py
│   ├── bot.py               # Instance Application python-telegram-bot, setup_handlers()
│   ├── handlers.py          # Handlers de commandes et callbacks inline
│   └── notifications.py     # notify_all(), send_approval_request()
│
├── cli/
│   ├── __init__.py
│   ├── auth.py              # Commandes auth (auth meta, auth test)
│   ├── escalation.py        # Commande escalation reset
│   ├── fetch.py             # Commande fetch
│   ├── generate.py          # Commande generate-test-image
│   ├── health.py            # Commande health
│   ├── setup.py             # Commande setup fonts
│   └── test_commands.py     # Commandes test telegram/instagram
│   # Voir docs/CLI.md pour chaque commande
│
├── publisher/
│   ├── __init__.py          # publish_to_all_platforms()
│   ├── instagram.py         # InstagramPublisher
│   ├── facebook.py          # FacebookPublisher
│   ├── token_manager.py     # TokenManager, days_until_expiry, get_alert_threshold
│   └── image_hosting.py     # start_local_image_server(), upload_to_remote(), serve_locally(), run_image_server()
│
└── utils/
    ├── __init__.py
    ├── date_helpers.py      # compute_time_ago(), format_historical_date()
    ├── text_helpers.py      # Troncature, nettoyage de texte
    └── retry.py             # with_retry() — backoff exponentiel
```

---

## Interfaces entre composants

### Fetchers → DB (`fetchers/base.py`)

Dataclasses de transport (ne sont pas des modèles ORM) :

```python
@dataclass
class RawContentItem:
    source: str; source_lang: str; event_type: str
    month: int; day: int; year: int; description: str
    title: str | None = None
    wikipedia_url: str | None = None
    image_url: str | None = None

@dataclass
class RssFeedItem:  # dataclass de transport — NE PAS confondre avec ancnouv.db.models.RssArticle (ORM)
    source_url: str; title: str; summary: str; article_url: str
    published_at: datetime; fetched_at: datetime
    image_url: str | None = None
    feed_name: str = ""      # fourni par config.content.rss.feeds[n].name
```

Contrats `BaseFetcher` [ARCH-M2] (implémenté uniquement par `WikipediaFetcher` — `RssFetcher` a une interface différente et n'hérite pas de `BaseFetcher`) — justification : `BaseFetcher` formalise l'interface pour tout fetcher basé sur une date (fetch(date) → store). `RssFetcher` n'est pas date-based (collecte tous les articles en une fois) — l'ABC ne s'applique pas. En v2, tout nouveau fetcher date-based (ex : `WikidataFetcher`) devra hériter de `BaseFetcher` :

```python
class BaseFetcher(ABC):
    async def fetch(self, target_date: date) -> list[RawContentItem]: ...
    async def store(self, items: list[RawContentItem], session: AsyncSession) -> int: ...
```

Interface `RssFetcher` (RSS n'est pas date-based) :

```python
class RssFetcher:
    async def fetch_all(self, config: Config) -> list[RssFeedItem]: ...
    async def store(self, articles: list[RssFeedItem], session: AsyncSession) -> int: ...
```

`feedparser.parse()` est synchrone — wrappé avec `asyncio.to_thread()` pour ne pas bloquer la boucle événementielle.

### Generator → DB → Bot

```python
async def select_event(session: AsyncSession, target_date: date) -> Event | None: ...
async def select_article(session: AsyncSession, config: Config) -> RssArticle | None: ...
async def generate_post(session: AsyncSession) -> Post | None: ...

# Dans generator/selector.py
async def get_effective_query_params(
    session: AsyncSession,
    config: Config
) -> EffectiveQueryParams:
    """Lit escalation_level depuis DB, calcule les paramètres effectifs.
    L'escalade ne peut qu'assouplir la config de base, jamais la durcir."""

# Dans generator/caption.py
def format_caption_rss(article: RssArticle, config: Config) -> str: ...
def format_caption_wiki(event: Event, config: Config) -> str: ...

# Dans generator/image.py
async def generate_image(
    event_or_article: Event | RssArticle,
    config: Config,
    output_dir: Path
) -> Path:  # chemin vers data/images/{uuid}.jpg
```

`generate_post` orchestre la sélection hybride A+B (voir [Génération hybride Mode A+B](#génération-hybride-mode-ab)), génère l'image et la légende, persiste le `Post` en DB. La config est obtenue via `get_config()` (contexte partagé) — pas passée en paramètre. Voir CONFIGURATION.md pour la structure complète de `Config` (champs racine, sous-modèles, validators). [ARCH-M5]

### Table `scheduler_state` — clés du flux de données

`scheduler_state` est une table non-ORM (voir DATABASE.md). Les clés utilisées dans le flux de l'application :

| Clé | Type | Écrit par | Lu par |
|-----|------|-----------|--------|
| `paused` | `"true"/"false"` | `cmd_pause`, `cmd_resume` | JOB-3 |
| `daily_post_count` | JSON `{"date":"…","count":N}` | `check_and_increment_daily_count` | JOB-3 (auto_publish), `handle_approve` |
| `publications_suspended` | `"true"/"false"` | `job_check_token` (token expiré) | `check_and_increment_daily_count` |
| `escalation_level` | `"0"` à `"5"` | `increment_escalation_level` (JOB-1) | `get_effective_query_params` (JOB-1, `job_fetch_wiki`) |
| `token_alert_level` | `"normal"` \| `"30j"` \| `"14j"` \| `"7j"` \| `"3j"` \| `"1j"` \| `"expired"` | `job_check_token` | `cmd_status` |

> `token_alert_level` (dans `scheduler_state`) et `last_alert_days_threshold` (dans `meta_tokens`) sont deux mécanismes distincts : le premier expose le niveau courant pour l'affichage par `cmd_status` ; le second sert d'anti-spam pour éviter les doublons d'alerte quotidiens dans `job_check_token`. Voir DATABASE.md.

> **[ARCH-13] Mécanisme d'escalade (résumé pour JOB-1) :** l'escalade est gérée par `increment_escalation_level(session)` (dans `generator/selector.py`), appelée depuis JOB-1 quand le stock des 7 prochains jours passe sous `low_stock_threshold`. Elle incrémente `scheduler_state.escalation_level` de 0 à 5. Chaque niveau débloque des types d'événements et des politiques de déduplication supplémentaires. Voir DATA_SOURCES.md [DS-1.4b] pour le tableau complet.

### DB Session (voir DATABASE.md pour les détails)

```python
def init_db(db_path: str) -> AsyncEngine: ...
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]: ...
```

`db/utils.py` expose les fonctions d'accès à `scheduler_state` :

```python
# Dans db/utils.py
async def get_scheduler_state(session: AsyncSession, key: str) -> str | None: ...
async def set_scheduler_state(session: AsyncSession, key: str, value: str) -> None: ...
```

`init_db` retourne l'`AsyncEngine` nécessaire à `init_context()`.

### Contexte partagé (`scheduler/context.py`)

Les jobs APScheduler ne peuvent pas recevoir d'objets non-picklables en `kwargs` (SQLAlchemyJobStore ne peut pas les sérialiser). Contournement : singletons module-level initialisés dans `main_async()` avant `scheduler.start()`.

```python
def init_context(config: Config, bot_app: Application, engine: AsyncEngine) -> None: ...
def get_config() -> Config: ...
def get_bot_app() -> Application: ...
def get_engine() -> AsyncEngine: ...
def set_engine(engine: AsyncEngine) -> None: ...
```

Les getters lèvent `RuntimeError` (pas `assert`) si le contexte n'est pas initialisé — les `assert` sont silencieusement désactivés avec `python -O`.

`set_engine(engine)` rebinde la `_session_factory` dans `db/session.py` — c'est via cette `_session_factory` que `get_session()` (défini dans `db/session.py`) crée ses sessions. `init_context` appelle `set_engine` en interne. Les tests appellent `set_engine(test_engine)` directement pour rediriger toutes les sessions vers la DB en mémoire sans init complète du contexte.

> **[ARCH-M6] Justification du couplage `set_engine` / `_session_factory` :** ce couplage permet de changer la cible de toutes les sessions (y compris celles créées dans des jobs APScheduler) avec un seul appel, sans passer l'engine en paramètre partout. L'alternative (injecter l'engine dans chaque fonction) est incompatible avec la contrainte APScheduler (pas de kwargs non-picklables). C'est le compromis délibéré pour maintenir `get_session()` sans paramètre.

> **[ARCH-M1] Contrainte de testabilité :** toute fonction qui appelle `get_config()`, `get_engine()`, ou `get_session()` doit être testée avec `init_context(config, bot_app, engine)` (ou `set_engine(test_engine)` minimum) appelé au préalable dans le setup du test. Sans cela, les getters lèvent `RuntimeError`. Voir TESTING.md pour les fixtures recommandées.

> **[ARCH-m6] `config.image_server_token` :** ce champ est un champ **racine** de `Config` (lu depuis `.env` `IMAGE_SERVER_TOKEN`) — pas `config.image_hosting.token`. La raison : le token est utilisé à la fois par `upload_to_remote` (dans `publisher/image_hosting.py`) et par `run_image_server` (qui lit directement `os.environ["IMAGE_SERVER_TOKEN"]` sans `Config`). L'avoir au niveau racine de `Config` évite d'imbriquer un secret dans `ImageHostingConfig` et clarifie qu'il s'agit d'un secret comme les autres tokens (Telegram, Meta). Voir CONFIGURATION.md.

**Règle absolue :** ne jamais passer `config`, `session_factory` ou `bot_app` en `kwargs` de `scheduler.add_job()`.

### Bot Notifications (`bot/notifications.py`)

```python
async def notify_all(bot: Bot, config: Config, message: str) -> None: ...
async def send_approval_request(bot: Bot, post: Post, config: Config, session: AsyncSession) -> None: ...
```

`notify_all` : envoie `message` à tous les `user_ids` dans `config.telegram.authorized_user_ids`. Erreurs par utilisateur silencieuses (log WARNING). Utilisée pour alertes système, résultats de publication, erreurs scheduler.

`send_approval_request` : envoie image + légende + boutons inline (✅ ❌ ⏭ ✏️) à tous les admins. Stocke les `message_id` dans `post.telegram_message_ids` (JSON dict `{user_id: message_id}`) et commit. Ces `message_id` sont utilisés par `job_check_expired` pour désactiver les boutons inline.

Si `post.image_path` est absent ou introuvable, envoie le texte seul (sans photo).

### Image Hosting (`publisher/image_hosting.py`)

```python
async def start_local_image_server(images_dir: Path, port: int) -> web.AppRunner: ...
async def upload_image(image_path: Path, config: Config) -> str: ...  # dispatcher backend
async def serve_locally(image_path: Path, config: Config) -> str: ...
async def upload_to_remote(image_path: Path, config: Config) -> str: ...
async def run_image_server(port: int = 8765, token: str = "") -> int: ...
```

`start_local_image_server` : démarre le serveur aiohttp (appelé depuis `main_async` — voir séquence ci-dessus), retourne un `web.AppRunner` pour permettre l'arrêt propre (`runner.cleanup()`). Distinct de `run_image_server` qui est le point d'entrée CLI (`images-server`).

`upload_image` : dispatcher — délègue à `serve_locally` si `config.image_hosting.backend == "local"`, ou à `upload_to_remote` si `"remote"`. C'est la seule fonction appelée par les consommateurs externes (`_publish_approved_post`).

> **[ARCH-C4] Point d'entrée CLI de `run_image_server` :** la commande `images-server` est dispatchée depuis `__main__.py` via `_dispatch_inner` directement vers `run_image_server()` (dans `publisher/image_hosting.py`). Il n'existe pas de `cli/images.py` — cette commande est l'exception à la règle de localisation dans `cli/`. Elle est destinée au container Docker séparé `ancnouv-images` et n'a pas de dépendances sur `Config` (le token est lu depuis `IMAGE_SERVER_TOKEN` directement). Voir DEPLOYMENT.md — section `docker-compose.yml`.

`serve_locally` : copie le fichier dans `data/images/`, retourne `f"{config.image_hosting.public_base_url}/images/{filename}"`.

`upload_to_remote` : POST multipart vers `config.image_hosting.remote_upload_url`, header `Authorization: Bearer {config.image_server_token}` (champ **racine** de `Config`, pas `config.image_hosting.token`). Retry x3 avec backoff exponentiel (1s, 2s, 4s). Réponse JSON : `{"filename": "abc123.jpg"}`. Lève `ImageHostingError` si tous les retries échouent.

`run_image_server` : démarre le serveur aiohttp statique (backend `local`). Le token est lu depuis la variable d'environnement `IMAGE_SERVER_TOKEN` (pas depuis `config.yml` — la commande `images-server` tourne dans un container séparé sans accès complet à la config). Retourne le code de retour du processus.

> **[ARCH-22] `EADDRINUSE` :** si le port est déjà occupé, aiohttp lève `OSError: [Errno 98] Address already in use`. `run_image_server` doit catcher cette exception et afficher un message explicite : `"Port {port} déjà utilisé. Vérifier avec lsof -i :{port}."` puis `sys.exit(1)`.

### Bot → Publisher (`publisher/__init__.py`)

```python
async def publish_to_all_platforms(
    post: Post,
    image_url: str,
    ig_publisher: InstagramPublisher | None,
    fb_publisher: FacebookPublisher | None,
    session: AsyncSession,
    caption: str | None = None,
) -> dict: ...
```

Retourne `{"instagram": post_id|None, "facebook": post_id|None}`.

Chaque publisher s'exécute dans sa **propre session** (`async with get_session() as ig_session:`). Ne jamais passer la même `session` aux deux publishers dans `asyncio.gather` — deux `commit()` concurrents sur la même session corrompent silencieusement l'état ORM.

Après publication réussie (`post.status = 'published'`), incrémente `published_count` sur l'`Event` ou l'`RssArticle` source (selon lequel de `event_id` / `article_id` est non-NULL). `last_used_at` est mis à jour à la **génération** du post (dans `generate_post`), pas ici — voir DATABASE.md section "Requête de sélection des candidats".

`caption` : si `None`, utilise `post.caption`. Si fourni, écrase `post.caption` pour cette publication.

```python
class InstagramPublisher:
    def __init__(self, ig_user_id: str, token_manager: TokenManager, api_version: str = "v21.0") -> None: ...
    async def publish(self, post: Post, image_url: str, caption: str, session: AsyncSession) -> str: ...

class FacebookPublisher:
    def __init__(self, page_id: str, token_manager: TokenManager, api_version: str = "v21.0") -> None: ...
    async def publish(self, post: Post, image_url: str, caption: str, session: AsyncSession) -> str: ...
```

Voir INSTAGRAM_API.md pour le détail des appels Meta Graph API.

### Flux partagés (`bot/handlers.py`)

```python
async def _publish_approved_post(post: Post, session: AsyncSession, bot: Bot, config: Config) -> None: ...
async def _retry_single_platform(post: Post, platform: str, session: AsyncSession, bot: Bot, config: Config) -> None: ...
```

`_publish_approved_post` : flux commun entre `handle_approve` et `cmd_retry`. Si `post.image_public_url` est déjà renseigné (crash après upload réussi), l'upload est sauté — sinon il est retente. Instancie les publishers, appelle `publish_to_all_platforms`. En cas d'`ImageHostingError`, notifie via `notify_all` et retourne sans changer le statut (le post reste `approved` pour retry ultérieur).

`_retry_single_platform` : flux de `cmd_retry_ig` / `cmd_retry_fb`. Cible uniquement la plateforme indiquée. Met à jour `post.instagram_post_id` (ou `post.facebook_post_id`) en cas de succès, ou écrit le message d'erreur dans `post.instagram_error` (ou `post.facebook_error`) en cas d'échec. Notifie le résultat via `notify_all`.

---

## Génération hybride Mode A+B

`generate_post(session)` sélectionne la source selon le ratio configuré :

1. Si `config.content.rss.enabled = false` : sélection Mode A uniquement (`select_event`).
2. Si `config.content.rss.enabled = true` : tirage probabiliste avec `config.content.mix_ratio` (proportion de posts Mode B — RSS, défaut : `0.2`). Ex illustration : `mix_ratio = 0.7` → 70% Mode B, 30% Mode A. `mix_ratio = 0.0` = 100% Wikipedia, `mix_ratio = 1.0` = 100% RSS.
3. Fallback : si la source tirée retourne `None`, tenter l'autre source. Si les deux retournent `None`, retourner `None`.

Après sélection, `generate_post` :
- Met à jour `last_used_at` sur l'event ou l'article sélectionné
- Génère l'image : `generate_image(source, config, ...)` — accepte `Event | RssArticle` (voir IMAGE_GENERATION.md)
- Formate la légende : Mode A → `format_caption(event, config)`, Mode B → `format_caption_rss(article, config)` (`generator/caption.py`)
- Insère le `Post` en DB (`status='pending_approval'`)
- Retourne le `Post` créé

---

## Flux de données complet

### Cycle nominal (Mode A)

```
1. [quotidien 2h] job_fetch_wiki
   → WikipediaFetcher.fetch(today)        # appel API Wikipedia fr (+ EN fallback si < 3 résultats)
   → WikipediaFetcher.store(items, db)    # INSERT OR IGNORE dans events
   # Note : l'heure fixe 0 2 * * * (2h du matin) n'est pas configurable en v1.
   # Pour modifier cette heure, éditer directement scheduler/__init__.py.

2. [cron configurable] job_generate
   → select_event(db, target_date)        # SELECT event WHERE disponible AND mm/jj = target_date
   → image.generate(event)               # Pillow → data/images/{uuid}.jpg
   → caption.format(event)               # str légende
   → db.posts INSERT (status=pending_approval)
   → send_approval_request(bot, post)    # envoie image + boutons inline

3. [callback Telegram ✅]
   → UPDATE posts SET status='approved' WHERE id=:id AND status='pending_approval'  # verrou optimiste
     → 0 lignes affectées → retourner silencieusement (double-clic ou race condition)
   → session.refresh(post)               # synchroniser l'état ORM après UPDATE SQL brut
   → post.approved_at=now(), commit      # N'écrire QUE approved_at (status déjà 'approved' via SQL)
   → upload_image(image_path)            # → URL publique HTTPS
   → post.image_public_url=url, commit  # idempotent : URL persistée avant publish (crash-safe)
   → post.status='publishing', commit   # [ARCH-M7] marqué avant publish — si crash ici, recover_pending_posts re-publie via image_public_url
   → check_and_increment_daily_count(engine, max_daily_posts)  # après upload, avant publish
     → si atteinte → post.status='queued' + notification Telegram informative
       (NOTE v1 : JOB-7 désactivé — le post restera 'queued' jusqu'à intervention manuelle.
        Procédure de déblocage : forcer la limite via DB directement ou relancer /force demain.)
   → publish_to_all_platforms(post, url) # parallèle asyncio (sessions séparées)
       ├── instagram.publish(...)        # container + publish
       └── facebook.publish(...)        # /{page-id}/photos
   → Post: status='published', *_post_id renseignés
   → events.published_count += 1  # last_used_at mis à jour à la génération, pas ici
   → notify_all("✅ Publié")

4. [callback Telegram ❌]
   → post.status='rejected'
   → si post.event_id IS NOT NULL : events.status='blocked'
   → si post.article_id IS NOT NULL : rss_articles.status='blocked'
   # Ne pas utiliser update(Event).where(Event.id == post.event_id) si event_id peut être NULL

5. [callback Telegram ⏭]
   → post.status='skipped'
   → reprendre à l'étape 2 immédiatement (pas de vérification pending_count)

6. [callback Telegram ✏️]
   → ConversationHandler : attendre saisie texte
   → post.caption=nouveau_texte, commit
   → re-envoyer la preview avec nouvelle légende

7. [horaire] job_check_expired
   → posts UPDATE status='expired' WHERE status='pending_approval' AND created_at < NOW()-48h
   → désactiver boutons Telegram, notifier

8. [quotidien 9h] `job_check_token`
   → meta_tokens SELECT expires_at WHERE token_kind='user_long'
   → selon seuil (30/14/7/3/1j) : notification + éventuel refresh auto
   → si refresh échoue et remaining ≤ 1j : publications_suspended='true'
```

### Gestion du cas `post.event_id IS NULL` (Mode B)

`handle_reject` doit vérifier lequel de `event_id` / `article_id` est non-NULL avant de bloquer la source. Un `UPDATE events WHERE id = NULL` met 0 lignes à jour silencieusement — ne pas l'utiliser sans guard.

---

## Gestion des erreurs

### Hiérarchie des exceptions (`exceptions.py`)

```python
class AnciennesNouvellesError(Exception): pass
class FetcherError(AnciennesNouvellesError): pass
class GeneratorError(AnciennesNouvellesError): pass
class PublisherError(AnciennesNouvellesError): pass
class TokenExpiredError(PublisherError): pass
class RateLimitError(PublisherError): pass
class ImageHostingError(PublisherError): pass
class DatabaseError(AnciennesNouvellesError):
    """Erreur de base de données (connexion, verrou, corruption)."""
```

> **[ARCH-21] `DatabaseError` :** `OperationalError` SQLAlchemy (DB inaccessible) est wrappée en `DatabaseError` dans `db/session.py`. Le cas "DB inaccessible → arrêt immédiat" est géré dans `_dispatch_inner` par `except DatabaseError: sys.exit(1)`.

> **[ARCH-19]** Pour le schéma DB complet, voir DATABASE.md. Colonnes clés de `posts` pour comprendre les flux : `status`, `image_path`, `image_public_url`, `telegram_message_ids`, `instagram_post_id`, `facebook_post_id`, `instagram_error`, `facebook_error`.

### Tableau de comportement

| Situation | Comportement | Notification Telegram |
|-----------|-------------|----------------------|
| Wikipedia API indisponible | Retry x3 (backoff exponentiel), utiliser cache DB | Non (silencieux si cache OK) |
| Wikipedia API indisponible + cache vide | Log CRITICAL, skip ce cycle | Oui : "⚠️ Aucun événement disponible" |
| Aucun événement pour la date | Skip ce cycle | Oui : "⚠️ Pas d'événement pour le [date]" |
| Génération image échoue | `GeneratorError` levée, log ERROR, skip ce cycle | Non (réessai au prochain cycle) |
| Image hosting échoue | Retry x3 interne dans `upload_to_remote` — si échec définitif, `ImageHostingError` | Oui : "❌ Upload image échoué" |
| Instagram API 429 | Lire le header `Retry-After` de la réponse HTTP si présent (secondes entières ou date HTTP) — attendre ce délai avant retry. Si `Retry-After` absent, appliquer backoff exponentiel standard. `RateLimitError` est retriable (non dans `NON_RETRIABLE`). | Oui si > 1h d'attente |
| Instagram API 400 | Log ERROR, post → `error` | Oui : détail Meta de l'erreur |
| Facebook API échoue (Instagram OK) | `facebook_post_id=NULL`, `facebook_error` renseigné, statut `published` quand même | Oui : "⚠️ Échec Facebook, Instagram OK" |
| Instagram API échoue (Facebook OK) | `instagram_post_id=NULL`, `instagram_error` renseigné, statut `published` quand même | Oui : "⚠️ Échec Instagram, Facebook OK" |
| Les deux plateformes échouent | Post → `error`, `error_message` renseigné. `/retry` → remet en `approved` | Oui : détail des deux erreurs |
| Token Meta expiré | Arrêt des publications (publications_suspended='true') | Oui : "🔑 Token expiré" |
| Token Meta expire dans ≤7j | Tentative refresh auto | Oui si refresh échoue |
| DB inaccessible | Arrêt immédiat | N/A |

### Retry pattern (`utils/retry.py`)

```python
NON_RETRIABLE = (TokenExpiredError, ImageHostingError)

async def with_retry(
    func,
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    non_retriable: tuple = NON_RETRIABLE,
) -> Any: ...
```

Les exceptions `non_retriable` sont propagées immédiatement sans retry. Les autres sont relancées avec `backoff_base ** attempt` secondes d'attente entre chaque tentative.

---

## Logging

- Logger Python standard (`logging`)
- Format : `%(asctime)s | %(levelname)-8s | %(name)s | %(message)s`
- Niveau configurable dans `config.yml` (défaut : `INFO`)
- Sortie simultanée : stdout + fichier rotatif `logs/ancnouv.log` (10 MB max, 5 fichiers). Le répertoire `logs/` est **indépendant de `config.data_dir`** — chemin fixe relatif au CWD (pas `data/logs/`). Cela évite d'accumuler des logs dans le volume Docker data/ tout en gardant les logs accessibles séparément. [ARCH-m3] Limitation : en Docker, si le CWD (`/app`) n'est pas monté en volume, les logs ne persistent pas après redémarrage du container. Solution : monter `-v ./logs:/app/logs` dans `docker-compose.yml` (voir DEPLOYMENT.md). En systemd, le CWD est fixé par `WorkingDirectory=` — `logs/` est créé dans ce répertoire.
- **[ARCH-16] Création automatique de `logs/` :** le répertoire `logs/` est créé automatiquement au démarrage dans `_dispatch_inner` : `Path("logs").mkdir(exist_ok=True)`. Sans cette création, `FileHandler` crashe au démarrage si `logs/` n'existe pas — ne jamais supprimer cette ligne.
- Chaque module utilise `logging.getLogger(__name__)`

---

## Concurrence et async

- Application entièrement asynchrone (`asyncio`)
- APScheduler utilise `AsyncIOScheduler` — partage la même boucle que PTB
- python-telegram-bot v20+ est async natif
- SQLAlchemy utilise `AsyncSession` avec `aiosqlite`

## Intégration asyncio PTB + APScheduler

PTB `run_polling(stop_signals=None)` est bloquant et gère sa propre boucle asyncio. APScheduler `AsyncIOScheduler` tourne sur la même boucle.

**Point d'entrée CLI (`scheduler/__init__.py`) :**

```python
def run(config: Config) -> int: ...
async def main_async(config: Config) -> None: ...
```

`run(config)` est le point d'entrée appelé par `_dispatch_inner` (CLI). Il appelle `asyncio.run(main_async(config))` et retourne `0` (succès) ou `1` (exception non gérée).

**Séquence `main_async(config)` :**

1. `engine = init_db(db_path)` — initialise DB, retourne `AsyncEngine`
2. `bot_app = create_application(config.telegram_bot_token)` — construit l'`Application` PTB et enregistre tous les handlers (voir TELEGRAM_BOT.md — `create_application`). `config.telegram_bot_token` est un champ **racine** de `Config` (pas `config.telegram.bot_token`)
3. `bot_app.bot_data["config"] = config` — injecte la config dans `bot_data` pour les handlers (notamment `authorized_only` qui lit `config.telegram.authorized_user_ids`)
4. `init_context(config, bot_app, engine)` — initialise le contexte partagé
5. Si `backend=local` : `runner = start_local_image_server(images_dir, port)` — démarre le serveur aiohttp, retourne un `web.AppRunner`
6. `async with get_session() as session: await recover_pending_posts(session, bot_app.bot, config)` — re-envoie les posts `pending_approval` sur Telegram après redémarrage. La session est créée ici car aucune session n'existe à ce point — `recover_pending_posts` ne crée pas sa propre session pour les requêtes initiales. [ARCH-C3]
7. `scheduler.start()` — démarre APScheduler
8. `await bot_app.run_polling(stop_signals=None)` — [ARCH-m7] `run_polling` en PTB v20+ est une **coroutine** qui s'exécute dans la boucle asyncio existante (partagée avec APScheduler). Elle appelle `initialize()`, `start()`, puis la boucle de polling, puis `stop()` et `shutdown()` avant de rendre la main — le cycle de vie est entièrement géré en interne.
9. `scheduler.shutdown(wait=False)` — arrêt propre après retour de `run_polling`
10. Si `backend=local` : `await runner.cleanup()` — arrêt propre du serveur aiohttp

> **[ARCH-C5] Séquence d'arrêt propre :** `stop_signals=None` évite le conflit entre PTB et APScheduler sur les handlers SIGTERM. Un handler de signal est enregistré dans `main_async` :
> ```python
> def stop_cb():
>     asyncio.create_task(bot_app.stop())  # [ARCH-18] create_task (ensure_future deprecated Python 3.12)
> loop = asyncio.get_event_loop()
> loop.add_signal_handler(signal.SIGTERM, stop_cb)
> loop.add_signal_handler(signal.SIGINT, stop_cb)
> ```
> **[ARCH-18]** `asyncio.ensure_future` est deprecated depuis Python 3.7 et génère `DeprecationWarning` en Python 3.12. Utiliser `asyncio.create_task` à la place.
> Ordre d'arrêt canonique déclenché par SIGTERM/SIGINT :
> 1. `stop_cb` → `bot_app.stop()` (PTB arrête le polling et les workers internes)
> 2. PTB appelle `bot_app.shutdown()` en interne avant que `run_polling` rende la main
> 3. Retour de `await bot_app.run_polling(...)` (étape 8)
> 4. `scheduler.shutdown(wait=False)` (étape 9) — n'attend pas les jobs en cours
> 5. `await runner.cleanup()` si backend=local (étape 10)

### Résolution du conflit APScheduler / python-telegram-bot

- Ne pas utiliser l'extra `[job-queue]` de PTB — il embarque APScheduler en interne.
- APScheduler utilise `data/scheduler.db` (distinct de `data/ancnouv.db`).
- `SQLAlchemyJobStore` utilise SQLAlchemy **synchrone** — URL sans `aiosqlite`.

| Fichier | Contenu |
|---------|---------|
| `data/ancnouv.db` | Données applicatives (events, posts, tokens, state) |
| `data/scheduler.db` | Jobs APScheduler uniquement |

> **[ARCH-15] Chemin de `scheduler.db` :** `data/scheduler.db` est toujours relatif à `config.data_dir`. Dans `create_scheduler()`, l'URL SQLAlchemy est `f"sqlite:///{config.data_dir}/scheduler.db"`. Si `config.data_dir = "data"`, le fichier est `data/scheduler.db` relatif au CWD. Si `config.data_dir = "/abs/path"`, c'est `/abs/path/scheduler.db`.

### Compteur journalier (`scheduler/jobs.py`)

```python
async def check_and_increment_daily_count(engine: AsyncEngine, max_daily_posts: int) -> bool: ...
```

Défini dans `scheduler/jobs.py`. Utilise `BEGIN EXCLUSIVE` pour garantir l'atomicité du test-et-incrément. Pattern exact (requis avec SQLAlchemy 2.x async + aiosqlite ≥ 0.20.0) :

```python
async with engine.connect() as conn:
    await conn.execution_options(isolation_level="AUTOCOMMIT")
    await conn.execute(text("BEGIN EXCLUSIVE"))
    # lecture + vérification + mise à jour de scheduler_state
    await conn.execute(text("COMMIT"))
```

> **Pourquoi `AUTOCOMMIT` + `BEGIN EXCLUSIVE` explicite :** aiosqlite maintient son propre état de transaction interne. Sans `AUTOCOMMIT`, `BEGIN EXCLUSIVE` envoyé comme SQL brut peut lever `OperationalError: cannot start a transaction within a transaction`. Le mode `AUTOCOMMIT` désactive la gestion automatique de transaction par SQLAlchemy, permettant le `BEGIN EXCLUSIVE` manuel.
>
> **[ARCH-17] Version aiosqlite validée :** testé et validé avec aiosqlite 0.20.0+, SQLAlchemy 2.0.x, Python 3.12. Les versions aiosqlite 0.17–0.19 maintiennent leur propre état de transaction d'une façon incompatible avec ce pattern — `BEGIN EXCLUSIVE` peut lever `sqlite3.OperationalError` sur ces versions. Épingler `aiosqlite>=0.20.0` dans `requirements.txt`.

Lit la clé `publications_suspended` avant le compteur — retourne `False` immédiatement si `True`. Retourne `True` si la publication est autorisée (compteur incrémenté), `False` si la limite est atteinte ou publications suspendues.

### Récupération au redémarrage (`scheduler/jobs.py`)

```python
async def recover_pending_posts(session: AsyncSession, bot: Bot, config: Config) -> None: ...
```

Appelée dans `main_async()` **après** `start_local_image_server` et **avant** `scheduler.start()` pour que les URLs d'images soient valides. Re-envoie sur Telegram tous les posts `pending_approval` dont le délai n'est pas dépassé. Pour les posts `approved` (ou `publishing`) : ceux avec `image_public_url` non-NULL sont re-publiés immédiatement via `publish_to_all_platforms` ; ceux sans `image_public_url` (upload interrompu) notifient l'utilisateur de lancer `/retry` manuellement.

> **[ARCH-07] Comportement pour les posts `publishing` :** un post en statut `publishing` indique un crash mid-publish (l'application s'est arrêtée après avoir commencé la publication). `recover_pending_posts` remet ces posts en statut `approved`. Au redémarrage : si `image_public_url` est présent (l'upload avait réussi avant le crash), la publication est tentée immédiatement sans nouvel upload. Si `image_public_url` est absent, l'utilisateur est notifié de lancer `/retry` manuellement.

> **[ARCH-M8] `TokenManager` au démarrage :** `recover_pending_posts` instancie `InstagramPublisher` et `FacebookPublisher` pour re-publier. Ces publishers créent chacun un `TokenManager` qui lit les tokens depuis la DB. À l'étape 6, `init_db` (étape 1) a déjà initialisé le moteur et `get_session()` est disponible — `TokenManager` peut lire `meta_tokens` normalement. Aucune initialisation séparée n'est requise avant `scheduler.start()`.

---

## Dépendances Python

**Python requis : 3.12+** [ARCH-C6] — Python 3.10 manque de `str | None` comme annotation de type dans les dataclasses et des améliorations asyncio. Python 3.11 est insuffisant pour la compatibilité de `pydantic-settings[yaml]` v2 + `sqlalchemy[asyncio]` 2.x en conjonction. La version minimale testée est 3.12. Instruction d'installation pour Raspberry Pi / Debian : voir DEPLOYMENT.md.

```
# requirements.txt
python-telegram-bot==21.*              # PAS [job-queue]
apscheduler>=3.8,<4                    # CronTrigger.from_crontab requiert >=3.8
sqlalchemy[asyncio]==2.*
aiosqlite>=0.20.0,<1                   # [ARCH-M4] >=0.20.0 requis pour AUTOCOMMIT + BEGIN EXCLUSIVE (voir SCHEDULER.md)
alembic==1.*
pydantic-settings[yaml]==2.*           # [yaml] installe pyyaml, expose YamlConfigSettingsSource
httpx==0.*
pillow==10.*
numpy>=1.24,<2                         # [ARCH-m4] >=1.24 pour np.random.randint stable ; <2 car numpy 2.x modifie l'API dtype/broadcasting — peut créer des conflits avec d'autres packages numpy-2-only, mais aucun tel package n'est dans ce projet
feedparser==6.*                        # Mode B
python-dotenv==1.*
aiohttp==3.*                           # serveur HTTP images (backend=local)
```

> `pydantic-settings[yaml]` : sans l'extra `[yaml]`, `yaml_file` dans `SettingsConfigDict` est ignoré silencieusement et `config.yml` n'est jamais lu.

---

## Setup environnement de développement

```bash
# 1. Créer l'environnement virtuel
python3.12 -m venv .venv
source .venv/bin/activate  # Linux/macOS

# 2. Installer les dépendances
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 3. Initialiser la DB de développement
python -m ancnouv db init

# 4. Télécharger les polices
python -m ancnouv setup fonts

# 5. Copier la config
cp config.yml.example config.yml
# Éditer config.yml : garder instagram.enabled/facebook.enabled = false pour le dev

# 6. Lancer les tests
pytest
```

---

## Pattern de session SQLAlchemy dans les handlers PTB

```python
# Pattern standard — chaque handler crée sa propre session
@authorized_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with get_session() as session:
        paused = await get_scheduler_state(session, "paused")
        # ...
```

`get_session()` est importé depuis `ancnouv.db.session`. La `_session_factory` est configurée globalement par `init_context()` — aucun paramètre supplémentaire nécessaire. Pas besoin de passer l'engine via `context.bot_data`.
