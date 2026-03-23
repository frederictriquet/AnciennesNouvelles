# Base de Données

> Référence : [SPEC-3.1], [SPEC-3.2], [SPEC-3.4], C-4.1.3

---

## Choix technique

**SQLite** avec **SQLAlchemy 2.x async** + **aiosqlite ≥ 0.20.0**.

Justification :
- Zéro infrastructure (fichier unique `data/ancnouv.db`)
- Suffisant pour le volume attendu (quelques milliers d'enregistrements)
- Portable sur VPS, RPi, NAS
- Transactions ACID garanties

**Migrations** : Alembic (schéma versionné, évolutions sans perte de données).

---

## Configuration du moteur SQLAlchemy (`db/session.py`)

Le moteur active trois PRAGMAs SQLite via un event listener synchrone sur `engine.sync_engine, "connect"` :
- `PRAGMA foreign_keys = ON` — sans ceci, les contraintes `REFERENCES` sont ignorées silencieusement
- `PRAGMA journal_mode = WAL` — lecture concurrente pendant les écritures async (évite `SQLITE_BUSY`)
- `PRAGMA busy_timeout = 10000` — gestion des accès concurrents directs (en millisecondes = 10s). À combiner avec `create_async_engine(..., connect_args={"timeout": 15})` (en secondes — valeur recommandée : 15s pour laisser au PRAGMA le temps d'agir avant que aiosqlite ne lève `OperationalError: database is locked`).

> **Pourquoi un event listener synchrone ?** aiosqlite utilise un thread dédié en interne — les PRAGMAs doivent être configurés au niveau de la connexion SQLite sous-jacente (synchrone), pas au niveau de la session async. `event.listens_for(engine.sync_engine, "connect")` est la méthode recommandée par SQLAlchemy pour ce cas.

**Signatures canoniques :**

```python
def create_engine(db_path: str) -> AsyncEngine: ...
def init_db(db_path: str) -> AsyncEngine: ...
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]: ...
```

- `init_db(db_path)` : initialise le moteur (`create_engine`) et la fabrique de sessions (`async_sessionmaker`, **pas** `sessionmaker(class_=AsyncSession)` qui est la syntaxe SQLAlchemy 1.x). Retourne l'`AsyncEngine` — nécessaire pour `init_context(config, bot_app, engine)`.
- `get_session()` : context manager — utiliser exclusivement avec `async with get_session() as session:`. Chaque coroutine (handler Telegram, job scheduler) doit obtenir sa propre session. **Ne jamais partager une session entre coroutines concurrentes** (deux `commit()` parallèles sur la même session corrompent silencieusement l'état ORM).
- La `_session_factory` est un `async_sessionmaker` initialisé avec `expire_on_commit=False`.

> **`set_engine(engine)`** (dans `scheduler/context.py`) : pour les tests, appeler `set_engine(engine)` après `create_async_engine(...)` afin que `get_session()` utilise le même engine que les fixtures.

---

## Schéma complet

### Table `events` (Mode A — Wikipedia)

```sql
CREATE TABLE events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identification de la source
    source          TEXT NOT NULL DEFAULT 'wikipedia',  -- 'wikipedia'
    source_lang     TEXT NOT NULL DEFAULT 'fr',          -- 'fr' ou 'en' (fallback)
    event_type      TEXT NOT NULL DEFAULT 'event',       -- 'event', 'birth', 'death', 'holiday'

    -- Date de l'événement historique
    month           INTEGER NOT NULL,   -- 1-12
    day             INTEGER NOT NULL,   -- 1-31
    year            INTEGER NOT NULL,   -- peut être négatif (avant J.-C.)

    -- Contenu
    title           TEXT,               -- titre court (NULL en v1 — l'endpoint Wikipedia "On This Day"
                                        -- ne retourne pas de titre séparé, cf [DS-1.7]). Réservé pour
                                        -- des sources futures fournissant un titre explicite.
    description     TEXT NOT NULL,      -- texte de l'événement
    wikipedia_url   TEXT,               -- URL de la page Wikipedia associée
    image_url       TEXT,               -- URL du thumbnail Wikipedia (peut être NULL)

    -- Hash de déduplication
    -- SHA-256 de NFKC(description).strip().lower() encodée UTF-8
    -- Plus robuste qu'une contrainte UNIQUE sur description (résiste aux
    -- variations de casse, ligatures, espaces en fin de chaîne)
    content_hash    TEXT NOT NULL,

    -- Gestion de l'utilisation
    status          TEXT NOT NULL DEFAULT 'available',
                    -- 'available' : peut être proposé
                    -- 'blocked'   : rejeté définitivement via ❌ (voir TELEGRAM_BOT.md — handle_reject)
    last_used_at    DATETIME,           -- dernière fois proposé (approuvé ou non)
    published_count INTEGER NOT NULL DEFAULT 0,

    -- Métadonnées
    fetched_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Contrainte d'unicité sur le hash (résistant aux variations mineures de texte)
    UNIQUE (source, source_lang, month, day, year, content_hash),
    CHECK (status IN ('available', 'blocked'))
    -- Transition 'available' → 'blocked' : déclenchée par handle_reject (rejet utilisateur)
    -- pour les events Mode A. Voir TELEGRAM_BOT.md — section handle_reject.
);

CREATE INDEX idx_events_date ON events (month, day);
CREATE INDEX idx_events_status ON events (status);
CREATE INDEX idx_events_year ON events (year);
CREATE INDEX idx_events_date_status ON events (month, day, status, published_count);
-- Cet index composite couvre exactement le pattern de la requête select_event
-- (filtre sur month/day/status/published_count exécuté à chaque cycle de génération).
```

### Table `rss_articles` (Mode B — RSS optionnel)

```sql
CREATE TABLE rss_articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Source
    feed_url        TEXT NOT NULL,      -- URL du flux RSS source
    feed_name       TEXT NOT NULL,      -- Nom lisible (ex: "Le Monde") — fourni par config.content.rss.feeds[n].name

    -- Contenu
    title           TEXT NOT NULL,
    summary         TEXT,               -- résumé/chapeau de l'article
    article_url     TEXT NOT NULL UNIQUE,  -- URL canonique (clé de déduplication)
    image_url       TEXT,               -- URL de l'image associée à l'article (peut être NULL)

    -- Dates
    published_at    DATETIME NOT NULL,  -- date de publication de l'article
    fetched_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- date de collecte par l'app

    -- Gestion
    status          TEXT NOT NULL DEFAULT 'available',
                    -- 'available', 'blocked'
    published_count INTEGER NOT NULL DEFAULT 0,
    last_used_at    DATETIME,           -- dernière fois proposé (à la génération, pas à la publication)
                                        -- même sémantique que events.last_used_at

    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (status IN ('available', 'blocked'))
);

CREATE INDEX idx_rss_published_at ON rss_articles (published_at);
CREATE INDEX idx_rss_status ON rss_articles (status);
CREATE INDEX idx_rss_status_fetched_at ON rss_articles (status, fetched_at); -- requête select_article (status + filtre fetched_at)
CREATE INDEX idx_rss_feed_url ON rss_articles (feed_url);                    -- filtrage par source (collecte, debug)
```

### Table `posts`

```sql
CREATE TABLE posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Source du contenu (l'un ou l'autre est renseigné, pas les deux)
    -- ON DELETE RESTRICT : l'app ne supprime jamais les events/articles — seule la purge manuelle
    -- pourrait déclencher cette contrainte ; RESTRICT est préférable à CASCADE (perte de données)
    -- ou SET NULL (violerait le CHECK d'exclusivité ci-dessous).
    event_id        INTEGER REFERENCES events(id) ON DELETE RESTRICT,
    article_id      INTEGER REFERENCES rss_articles(id) ON DELETE RESTRICT,

    -- Contenu généré
    caption         TEXT NOT NULL,      -- légende Instagram (modifiable par l'utilisateur)
    image_path      TEXT,               -- chemin local : data/images/{uuid}.jpg (NULL après nettoyage par job_cleanup)
    image_public_url TEXT,              -- URL publique après upload (renseignée juste avant publication)

    -- Cycle de vie (machine à états)
    status          TEXT NOT NULL DEFAULT 'pending_approval',
                    -- 'pending_approval' : en attente de validation Telegram
                    -- 'approved'         : approuvé, en attente de publication
                    -- 'queued'           : approuvé mais mis en file (limite journalière atteinte)
                    -- 'publishing'       : publication en cours (verrou)
                    -- 'published'        : publié sur au moins une plateforme (Instagram et/ou Facebook)
                    -- 'rejected'         : rejeté définitivement
                    -- 'skipped'          : ignoré (utilisateur a demandé "Autre")
                    -- 'expired'          : délai de validation dépassé
                    -- 'error'            : erreur de publication (voir error_message)
    CHECK (status IN ('pending_approval', 'approved', 'queued', 'publishing', 'published', 'rejected', 'skipped', 'error', 'expired')),

    -- Telegram
    -- JSON dict {user_id: message_id} — un message_id par admin (chaque chat a son propre espace)
    telegram_message_ids TEXT NOT NULL DEFAULT '{}',

    -- Container Instagram en cours (protection contre les crashs entre étape 1 et étape 2)
    ig_container_id      TEXT,          -- creation_id Meta, persisté juste après POST /media
                                        -- NULL = pas encore créé ou déjà publié
                                        -- Réutilisé au retry si le container est encore valide (< 24h)
                                        -- Durée de vie < 24h côté Meta : selon la documentation Meta Graph API (v21.0).
                                        -- Cette valeur peut changer selon les mises à jour Meta.
                                        -- Vérifier la documentation officielle si les comportements de retry semblent anormaux.

    -- Résultats de publication par plateforme (NULL = non publié ou non applicable)
    instagram_post_id    TEXT,          -- ID du post Instagram (NULL si échec ou désactivé)
    instagram_error      TEXT,          -- message d'erreur Instagram si échec partiel
    facebook_post_id     TEXT,          -- ID du post Facebook (NULL si échec ou désactivé)
    facebook_error       TEXT,          -- message d'erreur Facebook si échec partiel
    -- Un post est 'published' dès qu'une plateforme a réussi.
    -- Les colonnes *_error permettent de détecter et retenter les plateformes échouées
    -- via /retry_ig ou /retry_fb sans repasser par l'approbation.

    -- Timestamps
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at     DATETIME,
    published_at    DATETIME,           -- date de la première publication réussie (Instagram ou Facebook)

    -- Erreurs
    -- Logique de remplissage :
    -- instagram_error / facebook_error : erreur partielle (une plateforme OK, l'autre KO)
    --   → post.status = 'published' quand même (au moins une plateforme OK)
    --   → la colonne *_error permet /retry_ig ou /retry_fb ciblé
    -- error_message : erreur totale (les deux plateformes KO)
    --   → post.status = 'error', error_message = concaténation des deux erreurs
    --   → /retry remet en 'approved' pour retenter les deux
    error_message   TEXT,               -- détail de l'erreur si status='error'
    retry_count     INTEGER NOT NULL DEFAULT 0,

    -- Colonnes v2 (commentées en v1 — migration Alembic requise avant activation de JOB-7)
    -- scheduled_for  DATETIME,  -- heure de publication planifiée (NULL = pas planifié)
    -- queued_at      DATETIME,  -- horodatage de la mise en file

    -- Colonnes v2 Story (migration Alembic requise)
    -- story_post_id  TEXT,      -- ID du post Story Instagram (v2 — SPEC-7.4)

    -- Contraintes
    CHECK (
        (event_id IS NOT NULL AND article_id IS NULL) OR
        (event_id IS NULL AND article_id IS NOT NULL)
    )
);

CREATE INDEX idx_posts_status ON posts (status);
CREATE INDEX idx_posts_created_at ON posts (created_at);
CREATE INDEX idx_posts_status_created_at ON posts (status, created_at);  -- requêtes critiques (pending_approval + âge)
CREATE INDEX idx_posts_event_id ON posts (event_id);
CREATE INDEX idx_posts_article_id ON posts (article_id);
CREATE INDEX idx_posts_event_id_status ON posts (event_id, status);      -- requêtes de comptage stock/escalade
CREATE INDEX idx_posts_article_id_status ON posts (article_id, status);  -- requêtes de comptage stock RSS
```

### Table `meta_tokens`

Centralise les tokens Meta (utilisateur + page). Remplace l'ancienne table `instagram_tokens`.

```sql
CREATE TABLE meta_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Type de token
    token_kind      TEXT NOT NULL,
                    -- 'user_long'  : token utilisateur long durée (expire 60j)
                    -- 'page'       : Page Access Token (n'expire pas)
    CHECK (token_kind IN ('user_long', 'page')),

    -- Identifiants associés
    ig_user_id      TEXT,               -- ID utilisateur Instagram (pour token_kind='user_long')
    ig_username     TEXT,               -- @handle Instagram (informatif)
    fb_page_id      TEXT,               -- ID Page Facebook (pour token_kind='page')
    fb_page_name    TEXT,               -- Nom de la Page (informatif)

    -- Token
    access_token    TEXT NOT NULL,      -- token d'accès
    expires_at      DATETIME,           -- NULL si token permanent (page token)

    -- Renouvellement
    last_refreshed_at DATETIME,
    refresh_attempts  INTEGER NOT NULL DEFAULT 0,

    -- Alertes progressives [IG-2.4] : J-30, J-14, J-7, J-3, J-1
    -- Mémorise le dernier seuil d'alerte envoyé pour éviter les doublons entre cycles.
    -- NULL = aucune alerte envoyée.
    -- Valeurs : 30, 14, 7, 3, 1 (jours restants au moment de l'alerte)
    -- Rôle : valeur entière du dernier seuil dont l'alerte a été envoyée — évite de renvoyer
    --        le même seuil plusieurs fois entre deux cycles. Distinct de token_alert_level
    --        dans scheduler_state (chaîne lisible pour affichage /status).
    last_alert_days_threshold INTEGER,   -- seuil lors de la dernière alerte (ex: 7)
    last_alert_sent_at DATETIME,         -- horodatage de la dernière alerte

    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Contrainte d'unicité : un seul enregistrement par type de token.
    -- Sans cette contrainte, auth meta lancé deux fois crée des doublons
    -- et _load_token() retourne une ligne arbitraire.
    UNIQUE (token_kind)
);
```

**Deux enregistrements sont créés lors du setup :**
1. `token_kind='user_long'` — token utilisateur Meta (expire dans 60j, renouvelable automatiquement)
2. `token_kind='page'` — Page Access Token (permanent, dérivé du token utilisateur)

### Table `scheduler_state`

Cette table est dans **`ancnouv.db`** (pas `scheduler.db`).

```sql
CREATE TABLE IF NOT EXISTS scheduler_state (
    key        TEXT PRIMARY KEY,          -- ex: 'paused', 'escalation_level', 'daily_post_count'
    value      TEXT NOT NULL DEFAULT '',  -- sérialisé en JSON si structuré, sinon valeur brute
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

**Clés standard :**

| Clé | Valeur | Description |
|-----|--------|-------------|
| `paused` | `"true"` / `"false"` | Pause globale du scheduler |
| `daily_post_count` | JSON `{"date": "YYYY-MM-DD", "count": N}` | Compteur journalier de publications. Incrémenté par `check_and_increment_daily_count()` (scheduler/jobs.py). Remis à zéro automatiquement au premier appel de chaque nouveau jour (date UTC) : la réinitialisation se fait à minuit UTC, soit 1h ou 2h du matin à Paris selon l'heure d'été. |
| `escalation_level` | `"0"` à `"5"` | Niveau d'escalade Wikipedia (voir DATA_SOURCES.md [DS-1.4b]) |
| `token_alert_level` | `"normal"` \| `"30j"` \| `"14j"` \| `"7j"` \| `"3j"` \| `"1j"` \| `"expired"` | Niveau d'alerte d'expiration du token Meta — chaîne lisible, écrite par `job_check_token()` (scheduler/jobs.py), lue par `cmd_status` pour affichage. **Complémentaire** (non redondant) avec `MetaToken.last_alert_days_threshold` : ce champ est une valeur entière (30, 14, 7, 3, 1) qui évite de renvoyer le même seuil plusieurs fois ; `token_alert_level` est une chaîne d'affichage (`"30j"`, `"14j"`, etc.). Les deux champs ont des rôles distincts. |
| `publications_suspended` | `"true"` / absent | Publications suspendues suite à échec de refresh à J-1. **Sémantique** : valeur `"true"` = suspendu ; clé absente (ou valeur ≠ `"true"`) = non suspendu. `get_scheduler_state` retourne `None` si la clé est absente — les vérifications doivent utiliser `value == "true"` (pas un test de présence). Levée par `auth meta` réussi via `set_scheduler_state("publications_suspended", "false")`. |

---

## Machine à états des posts

```
                    ┌─────────────────┐
         généré     │ pending_approval │
    ────────────────►                 │
                    └──┬───┬────┬──┬──┘
                       │   │    │  │ ⏭ bouton
             ✅ bouton  │   │    │  └──────────────────────► ┌─────────┐
               approuver│   │    │                           │ skipped │ (terminal)
                       │   │    │ ❌ bouton                  └─────────┘
                       │   │    └──────────────────────► ┌──────────┐
                       │   │ timeout (JOB-4)              │ rejected │
                       │   │ (approval_timeout_hours)     │          │
                       │   ▼                              └──────────┘
                       │ ┌─────────┐
                       │ │ expired │
                       │ └─────────┘
                       │
                       ▼
               ┌──────────────┐
               │   approved   │◄─────────────────── /retry ─── error
               └──┬───────────┘
                  │
                  ├─ limite journalière atteinte (handle_approve)
                  │         ▼
                  │    ┌─────────┐
                  │    │ queued  │
                  │    └────┬────┘
                  │         │ JOB-7, slot dispo → approved → publishing
                  │         │
                  ├─────────────────────────────────────────────────────┐
                  │ publication immédiate                                │
                  ▼                                                     ▼
           ┌────────────┐                                          (depuis queued)
           │ publishing │
           └─────┬──────┘
                 │
        ┌────────┴──────────────────────┐
        │ succès                        │ échec total (les deux plateformes échouent)
        ▼                               ▼
   ┌───────────┐                   ┌─────────┐
   │ published │                   │  error  │──── /retry ────► approved
   │           │                   └─────────┘
   │ ig_post_id│
   │ fb_post_id│
   └─────┬─────┘  (terminal — pas de transition sortante)
         │
    échec partiel
    (une plateforme)
         │
         ├── instagram_error ≠ NULL → /retry_ig → re-tente Instagram seul
         └── facebook_error  ≠ NULL → /retry_fb → re-tente Facebook seul
```

**Transitions `skipped` :** le bouton ⏭ passe le post en `skipped` (terminal) et déclenche immédiatement un nouveau cycle de génération. La vérification `pending_count >= max_pending_posts` **ne s'applique pas** au skip — un nouveau post est généré sans délai pour remplacer celui ignoré.

**Légende des colonnes liées aux états :**

| Statut | `instagram_post_id` | `facebook_post_id` | `instagram_error` | `facebook_error` |
|--------|--------------------|--------------------|-------------------|-----------------|
| `published` (succès total) | renseigné | renseigné | NULL | NULL |
| `published` (Instagram KO) | NULL | renseigné | message d'erreur | NULL |
| `published` (Facebook KO) | renseigné | NULL | NULL | message d'erreur |
| `error` (échec total) | NULL | NULL | NULL | NULL |
| `queued` (v2) | NULL | NULL | NULL | NULL |

**Colonne `ig_container_id` — cycle de vie :**

| Moment | Valeur |
|--------|--------|
| Avant création du container | `NULL` |
| Juste après `POST /media` (étape 1) | `creation_id` renseigné, commit immédiat |
| Après publication réussie (`instagram_post_id` présent) | Conservé en DB (sans effet — la publication est terminée, le container n'est plus sollicité) |
| Au retry après échec : container < 24h côté Meta | Réutilisé si statut `FINISHED` ou `IN_PROGRESS` (évite de créer un doublon) |
| Au retry après échec : container > 24h ou statut `ERROR` | Recréé — la nouvelle valeur écrase l'ancienne dans `ig_container_id` |

---

## Modèles ORM SQLAlchemy (`db/models.py`)

SQLAlchemy 2.x, style `DeclarativeBase`. `status` utilise `String` avec une contrainte `CHECK` — SQLite ne supporte pas les types Enum natifs.

**Contraintes communes à tous les modèles :**
- Les colonnes `NOT NULL` sans valeur Python (datetimes) déclarent `server_default=text("CURRENT_TIMESTAMP")`. Sans cela, SQLAlchemy 2.x n'inspecte pas les `DEFAULT` du DDL et chaque INSERT sans valeur explicite lève `IntegrityError: NOT NULL constraint failed`.
- `updated_at` déclare en plus `onupdate=func.now()` pour être mis à jour automatiquement à chaque `UPDATE` ORM.

**`Event`** (`events`) :
- Toutes les colonnes du DDL ci-dessus sont présentes.
- `fetched_at` et `created_at` : `Mapped[datetime]`, `nullable=False`, `server_default=text("CURRENT_TIMESTAMP")`. **Distinction sémantique :** `fetched_at` = date à laquelle l'événement a été collecté via l'API Wikipedia (UTC, **renseigné explicitement par le fetcher** via `datetime.now(timezone.utc)` — le `DEFAULT CURRENT_TIMESTAMP` DDL sert de filet de sécurité uniquement) ; `created_at` = date d'insertion en DB (timestamp serveur SQLite UTC — `CURRENT_TIMESTAMP` SQLite retourne toujours UTC, indépendamment du fuseau système). Les deux ont le même `DEFAULT` mais `fetched_at` est toujours peuplé par le code Python. La contrainte de délai RSS utilise `fetched_at` (voir DATABASE.md — `select_article`).

> **Timezone — règle absolue :** `CURRENT_TIMESTAMP` SQLite retourne **UTC**. Tout code Python comparant à ces timestamps doit utiliser `datetime.now(timezone.utc)`. Utiliser `datetime.now()` (sans timezone) introduit une dérive si `TZ` du process ne vaut pas UTC (ex: `TZ=Europe/Paris` → décalage ±1h/2h sur les expirations et resets journaliers).
- Contraintes ORM : `UniqueConstraint("source", "source_lang", "month", "day", "year", "content_hash")`, `CheckConstraint("status IN ('available', 'blocked')")`, index sur `(month, day)`, `status`, `year`.

**`RssArticle`** (`rss_articles`) :
- Toutes les colonnes du DDL ci-dessus sont présentes, y compris `image_url` et `last_used_at`.
- `feed_name : Mapped[str]`, `nullable=False` — fourni par `config.content.rss.feeds[n].name` lors du stockage.
- `feed_url : Mapped[str]`, `nullable=False` — correspond à `RssFeedItem.source_url` dans la dataclass de transport (voir DATA_SOURCES.md — [DS-2.3] et ARCHITECTURE.md — `fetchers/base.py`). Mapping explicite lors de l'insertion : `orm_obj.feed_url = dataclass.source_url`. Attention : la dataclass (`RssFeedItem`) nomme ce champ `source_url`, la colonne DDL et l'ORM (`RssArticle`) le nomment `feed_url`.
- `fetched_at` et `created_at` : `Mapped[datetime]`, `nullable=False`, `server_default=text("CURRENT_TIMESTAMP")`.
- Contraintes ORM : `UniqueConstraint("article_url")`, `CheckConstraint("status IN ('available', 'blocked')")`.

**`Post`** (`posts`) :
- Toutes les colonnes du DDL ci-dessus sont présentes.
- `created_at` et `updated_at` : `Mapped[datetime]`, `nullable=False`, `server_default=text("CURRENT_TIMESTAMP")`. `updated_at` déclare en plus `onupdate=func.now()`.
  > **`onupdate` et UPDATE directs :** `onupdate=func.now()` n'est honoré que par les `UPDATE` passant par le flush ORM (ex: `session.flush()` après modification d'attribut). Les `UPDATE` directs via `session.execute(text("UPDATE posts SET ... WHERE ..."))` — comme le verrou optimiste dans `handle_approve` (TELEGRAM_BOT.md) — ne déclenchent **pas** `onupdate`. `updated_at` peut donc stagner après ces UPDATEs. Préférer les modifications via l'objet ORM chargé quand `updated_at` doit être à jour.
  >
  > **UPDATEs directs nécessitant `updated_at` explicite [DB-7] :** Les `session.execute(text("UPDATE ..."))` suivants ne déclenchent pas `onupdate` et doivent inclure `updated_at = CURRENT_TIMESTAMP` explicitement :
  > - Verrou optimiste dans `handle_approve` : `UPDATE posts SET status='publishing', updated_at=CURRENT_TIMESTAMP WHERE id=:id AND status='pending_approval'`
  > - `check_and_increment_daily_count()` : UPDATE sur `scheduler_state` — cette table n'a pas de colonne `updated_at` ORM, elle est gérée manuellement via `set_scheduler_state()` (qui inclut `updated_at=CURRENT_TIMESTAMP` dans son SQL).
  > - Tout `session.execute(text("UPDATE ..."))` modifiant `posts`, `events`, `rss_articles` ou `meta_tokens` doit inclure `updated_at = CURRENT_TIMESTAMP` dans le SET.
- `telegram_message_ids` : `Mapped[str]`, `nullable=False`, `server_default=text("'{}'")` — la valeur SQL est `'{}'` (chaîne JSON pour objet vide). Syntaxe exacte Python : `server_default=text("'{}'")` (guillemets doubles externes, guillemets simples pour la valeur SQL). Un test unitaire est recommandé pour valider que cette syntaxe produit bien `'{}'` comme valeur par défaut SQL sur la version SQLAlchemy utilisée.
- **Contrainte ORM `status` :** `CheckConstraint("status IN ('pending_approval', 'approved', 'queued', 'publishing', 'published', 'rejected', 'skipped', 'error', 'expired')")` — **le DDL ci-dessus fait foi** ; ce `CheckConstraint` ORM doit être identique au DDL. Doit inclure `'skipped'` (omission → `CHECK constraint failed` sur `handle_skip`).
- Contrainte d'exclusivité source : `CheckConstraint("(event_id IS NOT NULL AND article_id IS NULL) OR (event_id IS NULL AND article_id IS NOT NULL)")`.

**`MetaToken`** (`meta_tokens`) :
- Toutes les colonnes du DDL ci-dessus sont présentes.
- `created_at` et `updated_at` : `Mapped[datetime]`, `nullable=False`, `server_default=text("CURRENT_TIMESTAMP")`. `updated_at` déclare en plus `onupdate=func.now()`.
- Contraintes ORM : `UniqueConstraint("token_kind")`, `CheckConstraint("token_kind IN ('user_long', 'page')")` — une valeur invalide (typo) serait sinon insérée silencieusement.

---

## Configuration Alembic (`alembic/env.py`)

Points clés :
- `render_as_batch = True` est **obligatoire** pour SQLite — sans cette option, les `ALTER TABLE` échouent (SQLite supporte mal `ALTER COLUMN` nativement). Emplacement : dans `run_migrations_online()` de `alembic/env.py`, passer `render_as_batch=True` dans `context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True, ...)`.
- L'URL est construite depuis la variable d'environnement `ANCNOUV_DB_PATH` (défaut : `data/ancnouv.db`).
- `run_migrations_online()` est `async` car le moteur utilise `aiosqlite`.
- `PRAGMA foreign_keys = ON` doit être activé dans `run_migrations_online()` pour enforcer les FK lors des migrations DML. Ce PRAGMA contrôle l'enforcement des contraintes FK au niveau DML — il n'affecte **pas** l'introspection des FK par Alembic (qui lit le DDL directement). Activer ce PRAGMA via `connection.execute(text("PRAGMA foreign_keys=ON"))` avant les opérations batch pour éviter des violations silencieuses lors des `ALTER TABLE` batch (qui recréent les tables en interne). Ordre exact dans `run_migrations_online()` [DB-10] :
  ```python
  connection.execute(text("PRAGMA foreign_keys=ON"))
  with context.begin_transaction():
      context.run_migrations()
  ```
  Le PRAGMA doit être exécuté **avant** `context.begin_transaction()`.
- `target_metadata = Base.metadata` — Alembic découvre tous les modèles depuis la base déclarative.

> **`scheduler_state` hors ORM :** cette table n'est pas un modèle `DeclarativeBase`, donc `Base.metadata.create_all()` et l'autogenerate Alembic ne la couvrent pas. Elle doit être créée **explicitement** dans la migration initiale via `op.execute(...)`. Sans cette ligne dans la migration, le premier démarrage lève `OperationalError: no such table: scheduler_state`.

**Squelette `versions/0001_initial.py` :**

```python
"""initial schema

Revision ID: 0001
Revises:
Create Date: <date>
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Les 4 tables ORM sont créées par autogenerate depuis Base.metadata.
    # Créer ici uniquement scheduler_state (hors ORM).
    op.execute(
        "CREATE TABLE IF NOT EXISTS scheduler_state ("
        "key TEXT PRIMARY KEY, "
        "value TEXT NOT NULL DEFAULT '', "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scheduler_state")
```

> La partie ORM (tables `events`, `rss_articles`, `posts`, `meta_tokens`) est générée par `alembic revision --autogenerate` depuis `Base.metadata`. Le squelette ci-dessus montre uniquement l'ajout de `scheduler_state` dans `upgrade()`.

---

## Politique de déduplication

### Niveau événement

Un même événement Wikipedia est identifié par `source + source_lang + month + day + year + content_hash`.

```python
def compute_content_hash(text: str) -> str: ...
```

Implémentation canonique dans `ancnouv/db/utils.py` (voir aussi DATA_SOURCES.md [DS-1.7b]) :
1. Normalisation Unicode NFKC (`unicodedata.normalize("NFKC", text)`) — décompose les ligatures (`ﬁ` → `fi`)
2. Strip — supprime les espaces en début/fin
3. Lowercase — casse insensible
4. Encodage UTF-8 → SHA-256 hexdigest

La contrainte `UNIQUE(source, source_lang, month, day, year, content_hash)` garantit qu'un même événement historique ne peut pas être inséré deux fois.

### Niveau publication

Un événement avec `published_count > 0` :
- `deduplication_policy: "never"` (défaut) : ne sera plus jamais proposé (`published_count = 0` requis).
- `deduplication_policy: "window"` : peut être re-proposé si `last_used_at < now - deduplication_window_days` (ou si jamais utilisé).
- `deduplication_policy: "always"` : toujours proposable.

> **Note :** la politique `"never"` filtre sur `published_count = 0`, et NON sur `status NOT IN (...)`. Un événement avec un post `pending_approval` actif peut donc être re-sélectionné si sa politique autorise un second post. C'est intentionnel — la déduplication porte sur les publications effectives, pas sur les soumissions en cours.

**Incrémentation de `published_count` :** incrémenté dans `publish_to_all_platforms()` (`publisher/__init__.py`) après publication réussie (`status = 'published'`). L'incrément se fait sur `event_id` (table `events`) ou `article_id` (table `rss_articles`) selon lequel est non-NULL.

> **Atomicité [DB-2] :** l'UPDATE de `published_count` (sur `events` ou `rss_articles`) et l'UPDATE de `post.status = 'published'` doivent être dans la même transaction (même `session.commit()`). Un crash entre ces deux opérations laisserait la DB dans un état incohérent : post marqué `published` sans incrément du compteur, ou compteur incrémenté sans post marqué publié.

> **Colonnes v2 — `scheduled_for` / `queued_at` :** Le statut `queued` est défini en v1 (la contrainte CHECK l'inclut) mais les colonnes `scheduled_for DATETIME` et `queued_at DATETIME` nécessaires à JOB-7 sont des colonnes v2. En v1, les posts `queued` restent bloqués (JOB-7 commenté).
>
> **Déblocage manuel des posts `queued` [TRANSVERSAL-2] :** En v1, JOB-7 est commenté donc les posts `queued` ne sont jamais automatiquement remis en circulation. Pour débloquer manuellement :
> ```sql
> UPDATE posts SET status='approved' WHERE status='queued';
> ```
> Puis envoyer `/retry` dans Telegram. **Important :** `/retry` ne gère que les posts en statut `error` — il ne traite pas les posts `queued`. La commande SQL ci-dessus est donc la seule voie de déblocage en v1.

> **État `approved + image_public_url=NULL` :** un post peut rester `approved` avec `image_public_url=NULL` si l'upload a échoué après l'approbation. `recover_pending_posts` détecte cet état au redémarrage (posts `approved` sans `image_public_url`) et notifie l'utilisateur de lancer `/retry` — aucune tentative d'upload automatique au redémarrage (le serveur d'images peut être temporairement indisponible).

> **`recover_pending_posts` [DB-4] :**
> ```python
> async def recover_pending_posts(session: AsyncSession, bot: Bot, config: Config) -> None: ...
> ```
> Définie dans `scheduler/jobs.py`, appelée depuis `main_async()` au démarrage. Détecte les posts dans un état intermédiaire (posts `publishing` → `approved`, posts `approved` sans `image_public_url`) et envoie une notification Telegram pour inviter l'utilisateur à relancer `/retry`. Voir aussi SCHEDULER.md pour les détails d'implémentation.

---

## Requête de sélection des candidats (`generator/selector.py`)

C'est la requête maîtresse du système — elle sélectionne les événements éligibles pour la date du jour selon la politique de déduplication et l'état d'escalade.

```python
async def select_event(session: AsyncSession, target_date: date, effective_params: EffectiveQueryParams) -> Event | None: ...
```

`EffectiveQueryParams` est défini dans `ancnouv/fetchers/base.py`.

Comportement :
- `WHERE month = :month AND day = :day AND status = 'available'`
- Clause de déduplication appliquée selon `config.content.deduplication_policy` (et l'état d'escalade)
- `ORDER BY RANDOM() LIMIT 1`
- Retourne `None` si aucun événement éligible
- Retourne l'objet `Event` **chargé via `session.get(Event, row[0])`** — objet géré par l'identity map, pas un objet détaché (évite `DetachedInstanceError` lors des mises à jour ORM ultérieures comme `last_used_at`)

**Requêtes SQL complètes par politique de déduplication :**

Politique `"never"` (défaut) :
```sql
SELECT id FROM events
WHERE month = :month AND day = :day
  AND status = 'available'
  AND published_count = 0
ORDER BY RANDOM() LIMIT 1
```

Politique `"window"` (avec `deduplication_window_days = N`) :
```sql
SELECT id FROM events
WHERE month = :month AND day = :day
  AND status = 'available'
  AND (published_count = 0
       OR last_used_at IS NULL
       OR last_used_at < :cutoff)   -- cutoff = datetime.now(utc) - timedelta(days=N)
ORDER BY RANDOM() LIMIT 1
```

> **Note [DB-1] — `last_used_at IS NULL` :** cette clause est intentionnelle et correcte. Elle couvre le cas d'un événement jamais utilisé (valeur normale au premier cycle). Le cas `published_count > 0` + `last_used_at IS NULL` est théoriquement possible après un crash si `last_used_at` n'a pas été mis à jour avant le crash, mais cette clause ne filtre pas des événements invalides — elle s'assure de les inclure. La clause `OR last_used_at IS NULL` est donc un filet de sécurité, pas un indicateur d'état incohérent.

Politique `"always"` :
```sql
SELECT id FROM events
WHERE month = :month AND day = :day
  AND status = 'available'
ORDER BY RANDOM() LIMIT 1
```

```python
async def select_article(session: AsyncSession, config: Config, effective_params: EffectiveQueryParams) -> RssArticle | None: ...
```

`EffectiveQueryParams` est défini dans `ancnouv/fetchers/base.py`.

Comportement (Mode B) :
- `WHERE status = 'available'` et contrainte de délai : `fetched_at + min_delay_days <= today`
- Politique de déduplication appliquée (même logique que `select_event`)
- `ORDER BY RANDOM() LIMIT 1`
- Retourne `None` si aucun article éligible

**Requêtes SQL complètes de `select_article` :**

Politique `"never"` (défaut) :
```sql
SELECT id
FROM rss_articles
WHERE status = 'available'
  AND published_count = 0
  AND fetched_at <= :cutoff_date  -- cutoff = today - min_delay_days
ORDER BY RANDOM()
LIMIT 1
```

Politique `"window"` (avec `deduplication_window_days = N`) :
```sql
SELECT id
FROM rss_articles
WHERE status = 'available'
  AND fetched_at <= :cutoff_delay
  AND (published_count = 0 OR last_used_at < :cutoff_window)
ORDER BY RANDOM()
LIMIT 1
```

> **Note [DB-17] — politique `window` pour `select_article` :** la requête combine deux contraintes indépendantes : `fetched_at <= :cutoff_delay` (contrainte RSS min_delay — l'article doit avoir été collecté depuis au moins `min_delay_days` jours avant d'être proposé) ET `(published_count = 0 OR last_used_at < :cutoff_window)` (déduplication — l'article doit être nouveau ou ne pas avoir été utilisé récemment).

> **Note [DB-18] — `published_count` sur `rss_articles` avec politique `window` ou `always` :** un article RSS peut être republié avec ces politiques. C'est intentionnel : l'objectif est de permettre de republier des articles populaires après un certain délai. La politique de déduplication (`window` ou `always`) s'applique indépendamment pour les événements Wikipedia (table `events`) et les articles RSS (table `rss_articles`).

> **Sémantique de `last_used_at` :** mis à jour à la **génération** du post (dans `generate_post()`), pas à la publication. Un événement généré puis rejeté aura `last_used_at` renseigné avec `published_count = 0`. Avec la politique `window`, cet événement sera filtré jusqu'à expiration de la fenêtre — même s'il n'a jamais été publié. Ce comportement est **intentionnel** : la fenêtre de déduplication protège contre la répétition de propositions récentes, qu'elles aient abouti ou non. La politique `never` (défaut) n'est pas affectée car elle filtre uniquement sur `published_count = 0`.

> **Limites transactionnelles :** les opérations multi-tables (`generate_post`, `handle_approve`) s'exécutent dans une seule session et un seul `commit`. Un crash entre deux opérations peut laisser la DB dans un état intermédiaire. `recover_pending_posts` au redémarrage détecte et récupère ces états partiels (posts `publishing` → `approved`, posts `approved` sans `image_public_url`).

> **Séquence `approved → queued` dans `handle_approve` [DB-6] :**
> 1. Appel de `check_and_increment_daily_count()` — vérifie si `daily_post_count >= max_daily_posts`.
> 2. Si `check_and_increment_daily_count()` retourne `False` (limite atteinte) : `post.status = 'queued'`, commit. La vérification se fait **avant** de changer le statut.
> 3. Si retourne `True` : publication immédiate (`post.status = 'publishing'`, etc.).
>
> **En v1** : les posts `queued` restent bloqués (JOB-7 commenté). Déblocage manuel : `UPDATE posts SET status='approved' WHERE status='queued'` puis `/retry` (voir note TRANSVERSAL-2 ci-dessus).

---

## Utilitaires DB (`db/utils.py`)

```python
def compute_content_hash(text: str) -> str: ...
async def get_scheduler_state(session: AsyncSession, key: str) -> str | None: ...
async def set_scheduler_state(session: AsyncSession, key: str, value: str) -> None: ...
```

- `get_scheduler_state` : retourne `None` si la clé n'existe pas.
- `set_scheduler_state` : SQL exact —
  ```sql
  INSERT INTO scheduler_state (key, value, updated_at)
  VALUES (:key, :value, CURRENT_TIMESTAMP)
  ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
  ```
  `updated_at` est mis à jour par le statement lui-même (pas par un trigger) — cohérent avec une table non-ORM.

---

## Gestion des fichiers images

Les images générées par Pillow sont stockées dans `data/images/`. `job_cleanup` (JOB-6, quotidien à 3h) supprime les fichiers des posts `published`, `rejected`, `expired`, `skipped` dont `created_at < now - image_retention_days`. Après suppression, `image_path` est mis à `NULL` en DB. La clé de configuration est `content.image_retention_days` (défaut 7j, dans `ContentConfig`) — JOB-6 lit `config.content.image_retention_days`.

> **Avertissement [DB-8] — rétention basée sur `created_at` :** la rétention est calculée depuis la **création** du post, pas depuis sa publication. Si un post est créé lundi et publié vendredi, son image peut être nettoyée dès lundi+7j, soit seulement 3 jours après la publication — ce qui rendrait `/retry_ig` impossible si l'image locale est nécessaire.
> Recommandation : utiliser `COALESCE(published_at, created_at)` pour calculer la rétention depuis la publication effective plutôt que depuis la création.

---

## Initialisation et migrations

### Commandes CLI

```bash
python -m ancnouv db init      # crée la DB et applique toutes les migrations
python -m ancnouv db migrate   # applique les migrations en attente
python -m ancnouv db status    # affiche l'état des migrations (alembic current)
python -m ancnouv db backup    # sauvegarde data/ancnouv.db dans data/backups/ (conserve N fichiers selon database.backup_keep)
                               # Utilise VACUUM INTO 'data/ancnouv_YYYYMMDD_HHMMSS.db' (SQL SQLite natif).
                               # Ne pas utiliser shutil.copy2 : sur une DB en mode WAL, une copie fichier
                               # peut être incohérente si l'app écrit pendant la copie.
                               # VACUUM INTO garantit une copie cohérente même sous charge.
python -m ancnouv db reset     # DANGER : supprime et recrée la DB (dev uniquement)
```

### Migration initiale — procédure step-by-step [DB-9]

1. S'assurer que la DB est vide ou inexistante (`db init` la crée si absente).
2. `alembic revision --autogenerate -m "initial schema"` génère `versions/0001_initial.py`.
3. **Inspecter manuellement** la migration générée — si la DB existait déjà avec du contenu, Alembic peut générer une migration vide (aucune différence détectée entre le schéma existant et les modèles).
4. Ajouter `op.execute(...)` pour `scheduler_state` (hors ORM — non détectée par autogenerate) dans `upgrade()`.
5. `alembic upgrade head` pour appliquer.

### Séquence de démarrage

`init_db(db_path)` → retourne `engine` → `init_context(config, bot_app, engine)` → `scheduler.start()`

L'ordre est impératif : les jobs peuvent être déclenchés immédiatement après `scheduler.start()`, ils doivent donc trouver le contexte déjà initialisé.
