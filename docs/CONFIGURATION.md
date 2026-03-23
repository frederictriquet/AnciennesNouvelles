# Configuration

> Référence : [SPEC-3.6]

---

## Principe

- **`config.yml`** : tous les paramètres non-sensibles (versionnables)
- **`.env`** : tous les secrets (non versionnable, dans `.gitignore`)
- **Validation** : Pydantic Settings au démarrage — échec immédiat si invalide. Voir section [Validation au démarrage](#validation-au-démarrage) pour la liste complète des règles.

**Résolution du chemin de `config.yml` :** chargé depuis le répertoire de travail courant (CWD) au démarrage.
- Docker : monté en volume (`./config.yml:/app/config.yml:ro`) — CWD = `/app`
- systemd : `WorkingDirectory=/home/ancnouv/AnciennesNouvelles` dans l'unité
- Développement local : lancer depuis la racine du projet (`python -m ancnouv start`)

---

## Fichier `config.yml` (complet et annoté)

```yaml
# ─────────────────────────────────────────────────
# ANCIENNES NOUVELLES — Configuration principale
# ─────────────────────────────────────────────────

# Répertoire de données (DB, images générées, logs)
data_dir: "data"

# Niveau de log : DEBUG, INFO, WARNING, ERROR
log_level: "INFO"

# ─── BASE DE DONNÉES ──────────────────────────────
database:
  filename: "ancnouv.db"
  # true = backup automatique avant chaque migration
  auto_backup: true
  # Conserver N sauvegardes maximum
  backup_keep: 7

# ─── SCHEDULER ────────────────────────────────────
scheduler:
  timezone: "Europe/Paris"
  # Expression cron pour la génération de posts (défaut : 6 fois/jour)
  # Avec max_pending_posts=1 (défaut), la majorité des déclenchements sont silencieusement
  # skippés si un post est déjà en attente — comportement attendu, pas une erreur.
  generation_cron: "0 */4 * * *"
  # Nombre maximum de posts en attente simultanément
  max_pending_posts: 1
  # Délai d'expiration d'un post en attente (heures)
  approval_timeout_hours: 48
  # false (défaut) : validation Telegram requise avant publication
  # true : publication automatique sans validation manuelle
  auto_publish: false

# ─── CONTENU ──────────────────────────────────────
content:
  prefetch_days: 30
  # Valeurs possibles : events, births, deaths, holidays
  # Défaut : events uniquement. births et deaths sont activés automatiquement par l'escalade
  # niveau 1 (voir DATA_SOURCES.md [DS-1.4b]) — ne pas les ajouter ici sans raison explicite.
  wikipedia_event_types:
    - events
  wikipedia_lang_primary: "fr"
  wikipedia_lang_fallback: "en"
  # Nombre minimum d'événements avant bascule sur la langue de fallback
  wikipedia_min_events: 3
  # never | window | always
  deduplication_policy: "never"
  deduplication_window_days: 365
  image_retention_days: 7
  # Seuil de stock sur les 7 prochains jours déclenchant l'escalade
  low_stock_threshold: 3
  # Proportion Mode B : 0.0 = 100% Wikipedia, 1.0 = 100% RSS (ignoré si rss.enabled=false)
  mix_ratio: 0.2

  # ─── Mode B : Actualités RSS (optionnel) ───────
  rss:
    enabled: false
    # Délai minimal avant de publier (calculé depuis fetched_at, pas published_at)
    min_delay_days: 90
    # Articles plus vieux que N jours à la collecte sont ignorés
    max_age_days: 180
    # Note : rss.enabled=true avec feeds=[] est silencieusement inutile
    feeds:
      - url: "https://www.lemonde.fr/rss/une.xml"
        name: "Le Monde"
      - url: "https://www.francetvinfo.fr/rss/"
        name: "France Info"

# ─── IMAGE ────────────────────────────────────────
image:
  width: 1080
  height: 1350
  jpeg_quality: 95
  paper_texture: true
  paper_texture_intensity: 8

# ─── LÉGENDE INSTAGRAM ────────────────────────────
caption:
  hashtags:
    - "#histoire"
    - "#onthisday"
    - "#anciennesnews"
    - "#memoireducollectif"
    - "#ephemeride"
  hashtags_separator: "\n\n"
  include_wikipedia_url: false
  source_template_fr: "Source : Wikipédia"
  source_template_en: "Source : Wikipedia (EN)"

# ─── HÉBERGEMENT D'IMAGES ─────────────────────────
image_hosting:
  # local : serveur HTTP embarqué (VPS avec IP publique, single-container ou dev local)
  # remote : upload vers VPS distant (RPi/NAS) — OBLIGATOIRE pour le déploiement Docker
  #          à deux conteneurs (ancnouv + ancnouv-images).
  # ⚠️ PIÈGE : backend: "local" passe la validation Pydantic même en contexte Docker.
  # L'application démarre normalement mais aucune image n'est uploadée vers ancnouv-images.
  # Les publications échouent silencieusement jusqu'au premier post. Corriger AVANT le premier start.
  # Règle : si docker-compose.yml définit le service ancnouv-images → backend doit être "remote".
  backend: "local"

  # OBLIGATOIRE pour les deux backends — laisser vide provoque une erreur au démarrage
  # ex VPS : "https://monvps.com:8765"
  public_base_url: "https://VOTRE-IP-OU-DOMAINE:8765"

  local_port: 8765

  # Options backend=remote uniquement
  remote_upload_url: ""  # ex: "https://monvps.com:8765/images/upload"

# ─── INSTAGRAM ────────────────────────────────────
instagram:
  # false par défaut — activer APRÈS python -m ancnouv auth meta
  enabled: false
  user_id: ""  # ex: "17841405822304884"
  api_version: "v21.0"
  # Meta autorise jusqu'à 50 ; valeur conservative recommandée : 25
  max_daily_posts: 25

# ─── FACEBOOK ─────────────────────────────────────
facebook:
  # false par défaut — activer APRÈS python -m ancnouv auth meta
  enabled: false
  page_id: ""  # ex: "123456789012345"

# ─── TELEGRAM ─────────────────────────────────────
telegram:
  # Obtenir son ID : contacter @userinfobot sur Telegram
  authorized_user_ids:
    - 123456789  # Remplacer par votre ID
  # Délai (secondes) pour regrouper les notifications rapides dans notify_all()
  notification_debounce: 2
```

---

## Fichier `.env` (secrets)

```bash
# NE PAS VERSIONNER CE FICHIER

# Token du bot Telegram (obtenu via @BotFather)
TELEGRAM_BOT_TOKEN=123456789:AABBccDDeeFFggHH...

# Identifiants de l'application Meta
META_APP_ID=1234567890123456
META_APP_SECRET=abcdef1234567890abcdef1234567890

# Les tokens Meta NE sont PAS stockés dans .env.
# Source de vérité unique : table meta_tokens dans data/ancnouv.db
# Gestion via : python -m ancnouv auth meta

# Token d'authentification pour le serveur d'images distant
# Requis si image_hosting.backend = remote (ou pour le serveur VPS lui-même)
# Générer : python -c "import secrets; print(secrets.token_hex(32))"
# ⚠️ Une valeur vide ("") bloque le démarrage de images-server avec code de sortie 1.
IMAGE_SERVER_TOKEN=

# ID numérique du chat Telegram pour les notifications de crash systemd (facultatif)
# Utilisé uniquement par le service ancnouv-notify@.service (DEPLOYMENT.md — section systemd)
# Ce service exécute un script curl qui envoie un message Telegram lors d'un crash du service
# ancnouv.service. La valeur ici est le destinataire de ces alertes. Généralement le même que
# telegram.authorized_user_ids[0] — obtenir via @userinfobot.
# Non utilisé par l'application Python (Config) — uniquement par le script shell systemd.
# Laisser vide désactive silencieusement les notifications de crash systemd.
TELEGRAM_CHAT_ID=

# Chemin absolu vers la base SQLite — OPTIONNEL
# Surcharge database.filename dans config.yml
# Utile en CI/CD ou tests : ANCNOUV_DB_PATH=/tmp/test.db
ANCNOUV_DB_PATH=
```

---

## Modèle de configuration (`ancnouv/config.py`)

La configuration est implémentée via `pydantic-settings` avec `BaseSettings`. La structure reflète directement les sections `config.yml`.

### Chargement YAML — dépendance critique

`pydantic-settings` ne lit **pas** les fichiers YAML nativement. L'extra `[yaml]` est **obligatoire** :

```
pydantic-settings[yaml]
```

Sans cet extra, `yaml_file` dans `SettingsConfigDict` est ignoré silencieusement — `config.yml` n'est jamais lu, toutes les valeurs prennent leur défaut ou proviennent des variables d'environnement uniquement.

La source YAML est activée via :

```python
@classmethod
def settings_customise_sources(cls, settings_cls, **kwargs):
    return (
        kwargs["env_settings"],
        YamlConfigSettingsSource(settings_cls),
        kwargs["init_settings"],
    )
```

`YamlConfigSettingsSource` est importé depuis `pydantic_settings`. La priorité est : variables d'environnement > `config.yml` > valeurs par défaut Pydantic.

### Champs racines de `Config`

| Champ | Type Pydantic | Source | Description |
|-------|--------------|--------|-------------|
| `telegram_bot_token` | `str` | `.env` `TELEGRAM_BOT_TOKEN` | Token bot Telegram — **champ racine**, pas `config.telegram.bot_token`. Type `str` (pas `SecretStr`) car PTB l'utilise directement dans `.token(...)` qui attend `str`. |
| `meta_app_id` | `str` | `.env` `META_APP_ID` | |
| `meta_app_secret` | `str` | `.env` `META_APP_SECRET` | |
| `image_server_token` | `str` | `.env` `IMAGE_SERVER_TOKEN` | Token serveur images — **champ racine**, pas `config.image_hosting.token`. Défaut `""` acceptable (images-server non activé si vide). |
| `data_dir` | `str` | `config.yml` | Défaut : `"data"` |
| `log_level` | `Literal["DEBUG", "INFO", "WARNING", "ERROR"]` | `config.yml` | Défaut : `"INFO"` |
| `database` | `DatabaseConfig` | `config.yml` | |
| `scheduler` | `SchedulerConfig` | `config.yml` | |
| `content` | `ContentConfig` | `config.yml` | |
| `image` | `ImageConfig` | `config.yml` | |
| `caption` | `CaptionConfig` | `config.yml` | |
| `image_hosting` | `ImageHostingConfig` | `config.yml` | |
| `instagram` | `InstagramConfig` | `config.yml` | Défaut `enabled=False` |
| `facebook` | `FacebookConfig` | `config.yml` | Défaut `enabled=False` |
| `telegram` | `TelegramConfig` | `config.yml` | |

### Priorité des sources (du plus au moins prioritaire)

1. Variables d'environnement shell (ex : `export SCHEDULER__TIMEZONE=UTC` ou variable CI)
2. `.env` (lu par `pydantic-settings` comme fallback si la variable n'est pas dans le shell)
3. `config.yml`
4. Valeurs par défaut Pydantic

> **Variable shell vs `.env` :** si le même paramètre est défini dans le shell **et** dans `.env`, la valeur du shell l'emporte. La valeur dans `.env` n'est lue que si la variable est absente du shell.

### Règle générale `env_nested_delimiter="__"` [CONF-M6]

Tout champ de tout sous-modèle peut être surchargé via une variable d'environnement en utilisant `__` comme séparateur entre les niveaux de la hiérarchie (en majuscules) :

| Champ `config.yml` | Variable d'environnement |
|--------------------|--------------------------|
| `scheduler.timezone` | `SCHEDULER__TIMEZONE` |
| `content.rss.enabled` | `CONTENT__RSS__ENABLED` |
| `image.jpeg_quality` | `IMAGE__JPEG_QUALITY` |
| `image_hosting.public_base_url` | `IMAGE_HOSTING__PUBLIC_BASE_URL` |
| `instagram.max_daily_posts` | `INSTAGRAM__MAX_DAILY_POSTS` |

Les variables d'environnement sont sans préfixe (`env_prefix=""` dans `SettingsConfigDict`). Les champs racines (`data_dir`, `log_level`, `telegram_bot_token`, etc.) utilisent directement leur nom en majuscules : `DATA_DIR`, `LOG_LEVEL`, `TELEGRAM_BOT_TOKEN`.

### Sous-modèles

`DatabaseConfig`, `SchedulerConfig`, `RssFeedConfig`, `RssConfig`, `ContentConfig`, `ImageConfig`, `CaptionConfig`, `ImageHostingConfig`, `InstagramConfig`, `FacebookConfig`, `TelegramConfig` — un sous-modèle par section `config.yml`.

`mix_ratio` est un champ de `ContentConfig` (chemin `config.content.mix_ratio`), **pas** de `RssConfig`. Il est lisible même si `rss.enabled=false` (utilisé dans `generate_post` uniquement quand RSS activé).

Champs v2 commentés dans `SchedulerConfig` (`max_queue_size`), `ImageConfig` (`force_template`), et un modèle `StoriesConfig` stub — tous marqués `# v2`.

**Types des champs par sous-modèle :**

`DatabaseConfig` :

| Champ | Type Pydantic | Défaut |
|-------|--------------|--------|
| `filename` | `str` | `"ancnouv.db"` |
| `auto_backup` | `bool` | `True` |
| `backup_keep` | `int` | `7` |

`SchedulerConfig` :

| Champ | Type Pydantic | Défaut |
|-------|--------------|--------|
| `timezone` | `str` | `"Europe/Paris"` |
| `generation_cron` | `str` | `"0 */4 * * *"` |
| `max_pending_posts` | `int` | `1` |
| `approval_timeout_hours` | `int` | `48` |
| `auto_publish` | `bool` | `False` |

`ContentConfig` :

| Champ | Type Pydantic | Défaut |
|-------|--------------|--------|
| `prefetch_days` | `int` | `30` |
| `wikipedia_event_types` | `list[str]` | `["events"]` |
| `wikipedia_lang_primary` | `str` | `"fr"` |
| `wikipedia_lang_fallback` | `str` | `"en"` |
| `wikipedia_min_events` | `int` | `3` |
| `deduplication_policy` | `Literal["never", "window", "always"]` | `"never"` |
| `deduplication_window_days` | `int` | `365` |
| `image_retention_days` | `int` | `7` |
| `low_stock_threshold` | `int` | `3` |
| `mix_ratio` | `float` | `0.2` |
| `rss` | `RssConfig` | — |

`RssConfig` :

| Champ | Type Pydantic | Défaut |
|-------|--------------|--------|
| `enabled` | `bool` | `False` |
| `min_delay_days` | `int` | `90` |
| `max_age_days` | `int` | `180` |
| `feeds` | `list[RssFeedConfig]` | `[]` |

`RssFeedConfig` :

| Champ | Type Pydantic | Défaut |
|-------|--------------|--------|
| `url` | `str` | requis |
| `name` | `str` | requis |

> **[CONF-12] Validation du champ `url` :** le champ `url` est de type `str` sans validation de format URL — une URL mal formée n'est détectée qu'à la première collecte RSS. Recommandation : ajouter un `@field_validator("url")` avec `AnyUrl` ou un check minimal `url.startswith(("http://", "https://"))`.

> **[CONF-08] `@model_validator` de `RssConfig` :** un validator de modèle vérifie que `min_delay_days < max_age_days` :
>
> ```python
> @model_validator(mode="after")
> def validate_rss_delays(self) -> "RssConfig":
>     if self.min_delay_days >= self.max_age_days:
>         raise ValueError(
>             f"rss.min_delay_days ({self.min_delay_days}) doit être < "
>             f"rss.max_age_days ({self.max_age_days})"
>         )
>     return self
> ```
>
> Ce validator s'assure que le délai minimum de publication est strictement inférieur à l'âge maximum d'un article. Une valeur `min_delay_days >= max_age_days` signifierait qu'aucun article ne pourrait jamais être publié (l'article serait considéré "trop vieux" avant d'atteindre le délai minimum).

> Note : `rss.enabled=false` avec des entrées `feeds` non vides est silencieusement ignoré — les feeds ne sont ni validés ni collectés. Les URLs dans `config.yml.example` sont fournies à titre illustratif.

`ImageConfig` :

| Champ | Type Pydantic | Défaut |
|-------|--------------|--------|
| `width` | `int` | `1080` |
| `height` | `int` | `1350` |
| `jpeg_quality` | `int` | `95` |
| `paper_texture` | `bool` | `True` |
| `paper_texture_intensity` | `int` | `8` |

`CaptionConfig` :

| Champ | Type Pydantic | Défaut |
|-------|--------------|--------|
| `hashtags` | `list[str]` | `["#histoire", "#onthisday", "#anciennesnews", "#memoireducollectif", "#ephemeride"]` |
| `hashtags_separator` | `str` | `"\n\n"` |
| `include_wikipedia_url` | `bool` | `False` |
| `source_template_fr` | `str` | `"Source : Wikipédia"` |
| `source_template_en` | `str` | `"Source : Wikipedia (EN)"` |

`ImageHostingConfig` :

| Champ | Type Pydantic | Défaut |
|-------|--------------|--------|
| `backend` | `Literal["local", "remote"]` | `"local"` |
| `public_base_url` | `str` | `""` (défaut Pydantic ; enforced comme obligatoire par `validate_image_hosting`) |
| `local_port` | `int` | `8765` |
| `remote_upload_url` | `str` | `""` (obligatoire si `backend="remote"`) |

> **[CONF-14] Caractère "obligatoire" de `public_base_url` :** le champ a `default=""` au niveau Pydantic (champ techniquement optionnel) mais est enforced comme obligatoire par `validate_image_hosting` (validator model-level qui rejette la valeur vide). La distinction est intentionnelle : Pydantic accepte la valeur vide pour charger l'objet sans erreur, puis le validator s'exécute et la rejette. Ce comportement en deux temps permet à Pydantic de collecter toutes les erreurs de validation avant d'interrompre le démarrage, plutôt que d'échouer dès le parsing du champ.

> **[CONF-C2] Développement local :** `localhost` est dans la liste de rejet en v1 (aucune exception). `"example"` est également dans la liste de rejet — `"https://dev.example.com:8765"` est donc rejeté par `validate_image_hosting`.
>
> Pour les tests unitaires qui instancient `Config()` directement, deux approches :
> - Passer `instagram.enabled=False` et `facebook.enabled=False` — `validate_image_hosting` ne vérifie **pas** `public_base_url` si aucune plateforme n'est activée. C'est l'approche recommandée.
> - Utiliser `mock.patch.dict(os.environ, {"IMAGE_HOSTING__PUBLIC_BASE_URL": "https://10.0.0.1:8765"})` — une IP privée n'est pas dans la liste de rejet.
>
> Pour le développement local (app qui tourne), utiliser une IP privée non bloquée : `IMAGE_HOSTING__PUBLIC_BASE_URL=https://10.0.0.1:8765` (variable d'environnement, ne pas modifier `config.yml` versionné). Ne pas utiliser `https://dev.example.com:8765` — `"example"` est dans la liste de rejet.
>
> **[CONF-M8] Format de `remote_upload_url` :** doit contenir l'URL **complète** incluant le chemin de l'endpoint d'upload (ex : `"https://monvps.com:8765/images/upload"`). Ne pas mettre la base URL seule — `"https://monvps.com:8765"` produit un 404. Le serveur d'images écoute sur `/images/upload` (voir DEPLOYMENT.md — section `images-server`).

`InstagramConfig` :

| Champ | Type Pydantic | Défaut |
|-------|--------------|--------|
| `enabled` | `bool` | `False` |
| `user_id` | `str` | `""` |
| `api_version` | `str` | `"v21.0"` |
| `max_daily_posts` | `int` | `25` |

`FacebookConfig` :

| Champ | Type Pydantic | Défaut |
|-------|--------------|--------|
| `enabled` | `bool` | `False` |
| `page_id` | `str` | `""` (obligatoire si `enabled=True` — vérifié par `validate_meta`) |

> **[CONF-11] `api_version` partagée :** `FacebookPublisher` utilise `config.instagram.api_version` (pas un champ propre à `FacebookConfig`). Les deux publishers partagent la même version API Meta Graph. Si vous souhaitez changer la version API, modifier `instagram.api_version` dans `config.yml` — le changement s'applique simultanément aux deux publishers.

`TelegramConfig` :

| Champ | Type Pydantic | Défaut |
|-------|--------------|--------|
| `authorized_user_ids` | `list[int]` | `[]` (défaut intentionnel — toujours rejeté par `validate_meta` si non renseigné) |
| `notification_debounce` | `int` | `2` |

> **[CONF-C4] `authorized_user_ids: []` comme défaut :** le défaut `[]` est intentionnellement invalide. Il force la configuration explicite avant le premier démarrage (rejeté par `validate_meta`). Il n'est jamais possible de démarrer l'application sans avoir renseigné au moins un ID utilisateur.

### Validators de champs individuels [CONF-C3]

Certains champs numériques ont des contraintes Pydantic (`ge`/`le`) qui rejettent les valeurs hors-plage au démarrage :

| Modèle | Champ | Contrainte | Raison |
|--------|-------|------------|--------|
| `SchedulerConfig` | `max_pending_posts` | `ge=1` | Au moins 1 post en attente autorisé |
| `SchedulerConfig` | `approval_timeout_hours` | `ge=1, le=8760` | Timeout d'au moins 1 heure ; 8760h = 1 an max (au-delà, RF-3.3.3 deviendrait inapplicable en pratique) |
| `ContentConfig` | `deduplication_window_days` | `ge=1` | Une valeur 0 rendrait la politique `window` identique à `always` |
| `ContentConfig` | `prefetch_days` | `ge=1` | Une valeur 0 ou négative désactiverait le prefetch |
| `ContentConfig` | `image_retention_days` | `ge=1` | Une valeur 0 supprimerait les images immédiatement après publication |
| `ContentConfig` | `low_stock_threshold` | `ge=1` | Une valeur 0 désactiverait silencieusement l'escalade |
| `ContentConfig` | `mix_ratio` | `ge=0.0, le=1.0` | Proportion valide (0% à 100%) |
| `DatabaseConfig` | `backup_keep` | `ge=1` | Une valeur 0 supprimerait tous les backups |
| `TelegramConfig` | `notification_debounce` | `ge=0` | Une valeur négative serait silencieusement acceptée |
| `InstagramConfig` | `max_daily_posts` | `ge=1, le=50` | Limite Meta : maximum 50 publications/jour (SPEC.md RF-3.4.7) |
| `ImageConfig` | `jpeg_quality` | `ge=1, le=100` | Qualité JPEG valide (95 est la valeur par défaut recommandée pour le rapport qualité/taille, mais rien n'interdit techniquement 100) |

Ces contraintes sont déclarées comme arguments Pydantic (`Field(ge=..., le=...)`) — pas comme `@field_validator`.

---

## Champs v2 (réservés)

Ces champs existent dans les modèles Pydantic en tant que stubs commentés `# v2`. Ils ne sont pas lus par l'application en v1 mais leur présence dans les modèles évite une erreur Pydantic si renseignés par inadvertance.

`SchedulerConfig` — champ v2 :

| Champ | Type | Défaut | Ref spec |
|-------|------|--------|----------|
| `max_queue_size` | `int` | `10` | RF-7ter.5 — taille maximale de la file d'attente |

`ImageConfig` — champ v2 :

| Champ | Type | Défaut | Ref spec |
|-------|------|--------|----------|
| `force_template` | `str \| None` | `None` | RF-7bis.4 — forcer un template par époque (ex : `"medieval"`) |

`StoriesConfig` — modèle stub v2 (non encore intégré à `Config`) :

| Champ | Type | Défaut | Ref spec |
|-------|------|--------|----------|
| `enabled` | `bool` | `False` | RF-7.3.5 — activation des Stories Instagram |

> Ces champs ne doivent **pas** être documentés dans `config.yml.example` en v1 (pour éviter les questions de support). Les ajouter uniquement lors du développement v2.

---

## Validation au démarrage

### `validate_image_hosting` (`@model_validator(mode="after")`)

Vérifie :
1. `image_hosting.public_base_url` non vide — obligatoire pour les deux backends. Erreur : `"image_hosting.public_base_url est vide..."`.
2. `image_hosting.public_base_url` ne doit pas contenir de placeholder — rejeter les valeurs contenant `"VOTRE"`, `"VOTRE-IP"`, `"example"`, ou `"localhost"`. Un placeholder passe la validation non-vide mais Meta recevrait une URL invalide au moment de la publication (erreur silencieuse jusqu'au premier post). L'exception `localhost` n'est pas implémentée en v1 — les tests qui instancient `Config()` directement doivent utiliser une valeur non-`localhost` (ex : `"https://test.example.com:8765"`). Voir TESTING.md pour les patterns complets (`mock.patch.dict`, fixtures Pydantic).
3. Si `backend == "remote"` : `remote_upload_url` non vide.
4. Si `backend == "remote"` : `image_server_token` non vide.

### `validate_cron` (`@model_validator(mode="after")`)

Valide `scheduler.generation_cron` via `CronTrigger.from_crontab()` d'APScheduler. Détecte les expressions syntaxiquement invalides avant le premier déclenchement.

> **[CONF-16] Dépendance `apscheduler` au chargement de la config :** `config.py` importe `CronTrigger` depuis `apscheduler.triggers.cron`. Cette dépendance croisée (config → scheduler library) est intentionnelle mais a une implication importante : si APScheduler n'est pas installé, `Config()` ne peut pas être instancié (erreur à l'import). **Prérequis :** `apscheduler~=3.10` doit être installé (inclus dans `requirements.txt`) avant d'utiliser la config. Incompatible avec APScheduler 4.x (API modifiée — `CronTrigger.from_crontab` peut ne plus exister).

### `validate_meta` (`@model_validator(mode="after")`)

Vérifie :
1. Si `instagram.enabled` et `instagram.user_id == ""` → erreur avec message `"instagram.user_id est vide. Lancer : python -m ancnouv auth meta"`.
2. Si `facebook.enabled` et `facebook.page_id == ""` → erreur avec message `"facebook.page_id est vide. Lancer : python -m ancnouv auth meta"`.
3. Si `telegram.authorized_user_ids` est vide → erreur avec message `"telegram.authorized_user_ids est vide — au moins un ID utilisateur est requis"`.

> **[CONF-15] Périmètre de `validate_meta` :** ce validator couvre à la fois les plateformes Meta (Instagram, Facebook) et Telegram. Le nom `validate_meta` est un abus de langage historique — il vérifie aussi `telegram.authorized_user_ids`. Ce comportement est intentionnel : centraliser toutes les vérifications "compte actif avec ID renseigné" dans un seul validator.

> **N'exige PAS qu'au moins une plateforme soit active** : `instagram.enabled=false` + `facebook.enabled=false` est un état valide, notamment pendant `auth meta` et pour tester le bot Telegram seul.

### `validate_telegram_token` (`@field_validator("telegram_bot_token")`)

Valide le format du token bot Telegram : `\d+:[A-Za-z0-9_-]{35,}` (ID numérique + `:` + chaîne alphanumérique ≥ 35 caractères). Erreur si le token a clairement une forme incorrecte (ex: valeur vide, placeholder). Ne pas vérifier la validité auprès de l'API Telegram (nécessiterait un appel réseau au démarrage).

> **Bootstrap `auth meta`** : `auth meta` doit pouvoir s'exécuter avec `instagram.enabled=false` et `facebook.enabled=false` (état avant l'existence des tokens). `validate_meta` ne bloque pas cette configuration — c'est intentionnel.

---

## Fichier `config.yml.example`

Un fichier `config.yml.example` est versionné dans le repo. L'utilisateur copie ce fichier :

> **[CONF-13] Synchronisation `config.yml.example` ↔ CONFIGURATION.md :** le contenu de `config.yml.example` est reproduit dans la section "Fichier `config.yml` (complet et annoté)" de ce document. Les deux doivent être synchronisés lors de tout ajout de champ. **Référence canonique : `config.yml.example` versionné dans le repo.** En cas de divergence, `config.yml.example` fait foi.

> **[CONF-m8] Synchronisation `config.yml.example` ↔ CONFIGURATION.md :** `config.yml.example` est la référence canonique pour les valeurs par défaut et les options de configuration. Lors de tout ajout ou modification d'un champ de configuration, les deux doivent être mis à jour simultanément : `config.yml.example` (valeur par défaut commentée) **et** CONFIGURATION.md (tableau de types, défauts, section concernée). Un champ présent dans l'un et absent de l'autre est une divergence de spec.

```bash
cp config.yml.example config.yml
```

> **Champs obligatoires à renseigner avant le premier démarrage :**
>
> 1. `image_hosting.public_base_url` — le placeholder `"https://VOTRE-IP-OU-DOMAINE:8765"` provoque une erreur (`validate_image_hosting`)
> 2. `telegram.authorized_user_ids` — laisser vide provoque une erreur (`validate_meta`)
>
> **[CONF-C1] Commande `setup` :** la commande `python -m ancnouv setup` seule **n'existe pas**. Seule `python -m ancnouv setup fonts` est définie (CLI.md). SPEC.md RF-3.6.3 fait référence à `setup` de manière générique — l'implémentation réelle est `setup fonts` uniquement en v1.
>
> **Workflow initial recommandé :**
>
> ```bash
> # 1. Renseigner public_base_url et telegram.authorized_user_ids
> #    Garder instagram.enabled: false et facebook.enabled: false
> # 2. Renseigner .env (TELEGRAM_BOT_TOKEN, META_APP_ID, META_APP_SECRET)
> # 3. Télécharger les polices (prérequis pour generate-test-image)
> python -m ancnouv setup fonts
> # 4. Initialiser la base de données (obligatoire avant auth meta)
> python -m ancnouv db init
> # 5. Lancer l'authentification Meta
> python -m ancnouv auth meta
> # 6. Une fois les tokens obtenus, passer enabled: true et renseigner user_id/page_id
> # 7. Démarrer
> python -m ancnouv start
> ```

---

## Variables d'environnement vs config.yml

| Paramètre | Emplacement | Raison |
|-----------|------------|--------|
| Tokens, clés API, secrets | `.env` | Sécurité |
| Paramètres fonctionnels | `config.yml` | Lisibilité, versionnabilité |
| `instagram.user_id` | `config.yml` | Non secret (ID public) |
| `telegram.authorized_user_ids` | `config.yml` | Non secret |
| `ANCNOUV_DB_PATH` | `.env` ou env var | Surcharge le chemin DB (CI/tests) |

`config.yml` peut être versionné (pas de secrets). `.env` ne doit jamais être commité (dans `.gitignore`).

### Mécanisme de surcharge `ANCNOUV_DB_PATH`

La variable `ANCNOUV_DB_PATH` n'est pas un champ standard de `DatabaseConfig` — elle est lue par un `@model_validator(mode="after")` sur `Config` :

```python
@model_validator(mode="after")
def apply_db_path_override(self) -> "Config":
    db_path = os.environ.get("ANCNOUV_DB_PATH")
    if db_path:
        self.database.filename = db_path
    return self
```

`os` est importé dans `config.py`. Ce validator s'exécute **après** la résolution complète de `config.yml` et `.env`, garantissant que `ANCNOUV_DB_PATH` a la priorité absolue (y compris sur la valeur YAML). En CI/CD, fixer `ANCNOUV_DB_PATH=/tmp/test.db` — le fichier est créé par `db init` au démarrage des tests.

**Construction du chemin absolu de la DB :** dans `_dispatch_inner` (et dans `main_async`), le chemin passé à `init_db(db_path)` est calculé comme suit :

```python
import os
db_path = config.database.filename
if not os.path.isabs(db_path):
    db_path = os.path.join(config.data_dir, db_path)
```

Ce calcul garantit que `database.filename = "ancnouv.db"` (valeur relative) produit `data/ancnouv.db`, tandis que `ANCNOUV_DB_PATH=/tmp/test.db` (chemin absolu, déjà écrasé dans `config.database.filename` par `apply_db_path_override`) est utilisé tel quel, sans concaténation erronée.
