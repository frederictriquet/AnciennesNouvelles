# Dashboard de configuration

> Phase 10 de la roadmap. Aucune référence SPEC.md existante — ce document fait office de spec.

---

## Vue d'ensemble

Le dashboard est une interface web légère permettant de modifier la configuration d'ancnouv sans éditer `config.yml` manuellement. Il s'appuie sur une table `config_overrides` dans la base SQLite existante : ancnouv charge ses paramètres depuis `config.yml` (baseline) puis applique les overrides DB au démarrage et à intervalles réguliers.

**Ce que le dashboard est :**
- Un éditeur de configuration CRUD sur `config_overrides`
- Un tableau de bord opérationnel (état du scheduler, posts récents, alertes token)

**Ce que le dashboard n'est pas :**
- Un remplacement de `config.yml` (qui reste la source de bootstrap)
- Un outil d'administration de la DB (pas de gestion des `events`, `posts` au-delà de la lecture)
- Un exposeur de secrets (`.env` jamais lisible via le dashboard)

---

## Architecture

```
VPS
├── reverse proxy (Traefik/nginx — géré hors repo)
│     ├── / → ancnouv-images:8765
│     └── /dashboard/ → ancnouv-dashboard:8766
│
├── ancnouv               (bot + scheduler — inchangé fonctionnellement)
│   └── volume: ./data/   (DB SQLite en écriture)
│
├── ancnouv-images        (serveur d'images — inchangé)
│   └── volume: ./data/images/
│
└── ancnouv-dashboard     [NOUVEAU]
    ├── volume: ./data/   (DB SQLite en écriture pour config_overrides uniquement)
    └── volume: ./config.yml:ro  (lecture des valeurs par défaut)
```

**Réseau :** tous les services sont sur `ancnouv-net` (bridge Docker). Le dashboard accède à la DB via le volume partagé `./data/`, pas via un appel réseau vers ancnouv.

**Partage de la DB SQLite :** SQLite en mode WAL (déjà activé) supporte un writer concurrent + N readers. Le dashboard est le seul writer sur `config_overrides`. ancnouv écrit sur toutes les autres tables. Pas de conflit structurel — mais voir Points de vigilance [DASH-W1].

---

## Table `config_overrides`

### Schéma SQL

```sql
CREATE TABLE config_overrides (
    key        TEXT     PRIMARY KEY NOT NULL,
    value      TEXT     NOT NULL,
    value_type TEXT     NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (value_type IN ('str', 'int', 'float', 'bool', 'list', 'dict'))
);
```

### Sémantique des colonnes

| Colonne | Rôle |
|---|---|
| `key` | Chemin dot-path du paramètre : `"scheduler.generation_cron"`, `"content.rss.enabled"` |
| `value` | Valeur JSON-encodée : `"\"0 */6 * * *\""`, `"true"`, `"42"`, `"[\"#foo\"]"` |
| `value_type` | Type Python original — utilisé par le dashboard pour sélectionner le widget d'édition |
| `updated_at` | Horodatage de la dernière modification (non géré par ORM, mis à jour manuellement) |

### Conventions d'encodage

- `str` → chaîne JSON : `"\"valeur\""` (guillemets inclus dans la valeur stockée)
- `int`, `float` → nombre JSON : `"42"`, `"0.2"`
- `bool` → `"true"` ou `"false"` (minuscules — JSON canonique)
- `list` → tableau JSON : `"[\"#histoire\", \"#onthisday\"]"`
- `dict` → objet JSON : `"{\"url\": \"https://...\", \"name\": \"Mon flux\"}"`

### Migration Alembic

Nouvelle révision après `0003_add_story_columns`. La migration est simple (`CREATE TABLE`) et ne nécessite pas de `render_as_batch`. Rollback : `DROP TABLE config_overrides` (aucune donnée applicative perdue — les settings reviennent aux valeurs `config.yml`).

---

## Mécanisme de config overlay dans ancnouv

### Principe

```
config.yml + .env
      │
      ▼
Config (Pydantic) — chargé une fois au démarrage
      │
      ▼ model_dump()
dict base
      │
      ▼ apply_dot_overrides(base_dict, overrides_flat)
dict fusionné
      │
      ▼ Config.model_validate()
Config effectif — mis en cache 30s [DASH-A1]
```

### Nouveau module `ancnouv/config_loader.py`

**Responsabilités :**
- Maintenir le cache du `Config` effectif avec TTL
- Exposer `get_effective_config()` pour les jobs
- Exposer `invalidate_config_cache()` appelé par le dashboard

**Contrat de `get_effective_config()`** :
- Entrée : aucune (lit la DB via `get_session()` interne)
- Sortie : `Config` Pydantic validé avec overrides appliqués
- Effets : met à jour le cache si TTL expiré ou si `config_reload_requested` dans `scheduler_state`
- Erreur : si la DB est inaccessible → retourne le dernier Config en cache (dégradé gracieux)
- Erreur : si un override est invalide → log warning, ignore l'override, continue [DASH-A2]

**Contrat de `apply_dot_overrides(base, overrides)`** :
- Entrée : `base` dict imbriqué (issu de `model_dump()`), `overrides` dict plat `{dot.path: valeur_Python}`
- Sortie : dict imbriqué fusionné (deep merge)
- Règle : une clé `"a.b.c"` crée `base["a"]["b"]["c"]` — ne supprime jamais les clés absentes des overrides
- Règle : si un segment de chemin intermédiaire n'existe pas dans `base`, levée d'une `KeyError` → override ignoré avec log warning [DASH-A3]

### Gestion du cache

| Événement | Comportement |
|---|---|
| TTL 30s expiré | Rechargement silencieux depuis DB |
| `config_reload_requested = "true"` dans `scheduler_state` | Rechargement immédiat + remise à `"false"` |
| DB inaccessible | Retour du dernier cache valide, log warning |
| Override invalide (validation Pydantic échoue) | Override ignoré, log warning, Config sans cet override |

### Changements dans les jobs existants

Les fonctions de job qui utilisent `get_config()` (singleton chargé une fois) doivent migrer vers `get_effective_config()`. La liste exhaustive est à établir lors de l'implémentation. Règle : toute logique métier (génération, sélection, publication) utilise `get_effective_config()`. L'initialisation du scheduler (avant `scheduler.start()`) utilise encore `get_config()` car la DB n'est pas encore accessible.

### Paramètres nécessitant un redémarrage

Deux paramètres ne peuvent pas être pris en compte par le rechargement à chaud :

| Paramètre | Raison |
|---|---|
| `scheduler.generation_cron` | APScheduler a déjà programmé le trigger — le reconfigurer nécessite `scheduler.reschedule_job()` ou un redémarrage |
| `image_hosting.local_port` | Le serveur `ancnouv-images` écoute sur ce port au démarrage |

Quand ces paramètres sont modifiés, le dashboard écrit `config_restart_required = "true"` dans `scheduler_state` et affiche un bandeau d'avertissement.

---

## Registre des settings exposés

> Clé · Label affiché · Type · Défaut · Validation · Redémarrage requis

### Section `scheduler`

| Clé | Label | Type | Défaut | Validation | Restart |
|---|---|---|---|---|---|
| `scheduler.generation_cron` | Cron de génération | `str` | `"0 */4 * * *"` | `CronTrigger.from_crontab()` valide | **Oui** |
| `scheduler.max_pending_posts` | Posts en attente max | `int` | `1` | `≥ 1` | Non |
| `scheduler.approval_timeout_hours` | Timeout approbation (h) | `int` | `48` | `≥ 1, ≤ 8760` | Non |
| `scheduler.auto_publish` | Publication automatique | `bool` | `false` | — | Non |

### Section `content`

| Clé | Label | Type | Défaut | Validation | Restart |
|---|---|---|---|---|---|
| `content.prefetch_days` | Jours de prefetch | `int` | `30` | `≥ 1` | Non |
| `content.wikipedia_event_types` | Types d'événements Wikipedia | `list` | `["events"]` | Chaque élément ∈ `{"events", "births", "deaths", "holidays", "selected"}` | Non |
| `content.wikipedia_min_events` | Minimum d'événements Wikipedia | `int` | `3` | `≥ 1` | Non |
| `content.deduplication_policy` | Politique de déduplication | `str` (enum) | `"never"` | ∈ `{"never", "window", "always"}` | Non |
| `content.deduplication_window_days` | Fenêtre de déduplication (jours) | `int` | `365` | `≥ 1` | Non |
| `content.image_retention_days` | Rétention des images (jours) | `int` | `7` | `≥ 1` | Non |
| `content.low_stock_threshold` | Seuil stock bas | `int` | `3` | `≥ 1` | Non |
| `content.mix_ratio` | Ratio RSS/Wikipedia | `float` | `0.2` | `≥ 0.0, ≤ 1.0` | Non |

### Section `content.rss`

| Clé | Label | Type | Défaut | Validation | Restart |
|---|---|---|---|---|---|
| `content.rss.enabled` | RSS activé | `bool` | `false` | — | Non |
| `content.rss.min_delay_days` | Délai minimum (jours) | `int` | `90` | `≥ 1`, `< max_age_days` | Non |
| `content.rss.max_age_days` | Âge maximum (jours) | `int` | `180` | `> min_delay_days` | Non |
| `content.rss.feeds` | Flux RSS | `list` | `[]` | Chaque élément : `{url: str HTTPS, name: str non vide}` | Non |

### Section `image`

| Clé | Label | Type | Défaut | Validation | Restart |
|---|---|---|---|---|---|
| `image.jpeg_quality` | Qualité JPEG | `int` | `95` | `≥ 1, ≤ 100` | Non |
| `image.paper_texture` | Texture papier | `bool` | `true` | — | Non |
| `image.paper_texture_intensity` | Intensité texture | `int` | `8` | `≥ 0` | Non |
| `image.masthead_text` | Texte masthead | `str` | `"ANCIENNES NOUVELLES"` | Non vide | Non |
| `image.force_template` | Template forcé | `str\|null` | `null` | ∈ `{null, "medieval", "moderne", "xix", "xx_first", "xx_second", "xxi"}` | Non |

### Section `caption`

| Clé | Label | Type | Défaut | Validation | Restart |
|---|---|---|---|---|---|
| `caption.hashtags` | Hashtags | `list` | `["#histoire", ...]` | Chaque élément commence par `#` | Non |
| `caption.hashtags_separator` | Séparateur hashtags | `str` | `"\n\n"` | — | Non |
| `caption.include_wikipedia_url` | Inclure URL Wikipedia | `bool` | `false` | — | Non |
| `caption.source_template_fr` | Template source (FR) | `str` | `"Source : Wikipédia"` | Non vide | Non |
| `caption.source_template_en` | Template source (EN) | `str` | `"Source : Wikipedia (EN)"` | Non vide | Non |

### Section `image_hosting`

| Clé | Label | Type | Défaut | Validation | Restart |
|---|---|---|---|---|---|
| `image_hosting.public_base_url` | URL publique du serveur d'images | `str` | `""` | HTTPS, sans placeholder, si plateforme activée | Non |
| `image_hosting.local_port` | Port du serveur d'images | `int` | `8765` | `≥ 1024, ≤ 65535` | **Oui (ancnouv-images)** |

> `image_hosting.backend` et `image_hosting.remote_upload_url` ne sont pas exposés — paramètres structurels définis dans `config.yml`.

### Section `instagram`

| Clé | Label | Type | Défaut | Validation | Restart |
|---|---|---|---|---|---|
| `instagram.enabled` | Instagram activé | `bool` | `false` | Si `true` : `user_id` non vide dans `config.yml` [DASH-R1] | **Oui** |
| `instagram.max_daily_posts` | Posts par jour max | `int` | `25` | `≥ 1, ≤ 50` | Non |

### Section `facebook`

| Clé | Label | Type | Défaut | Validation | Restart |
|---|---|---|---|---|---|
| `facebook.enabled` | Facebook activé | `bool` | `false` | Si `true` : `page_id` non vide dans `config.yml` [DASH-R1] | **Oui** |

### Section `telegram`

| Clé | Label | Type | Défaut | Validation | Restart |
|---|---|---|---|---|---|
| `telegram.notification_debounce` | Debounce notifications (s) | `int` | `2` | `≥ 0` | Non |

> `telegram.authorized_user_ids` n'est pas exposé — paramètre de sécurité, doit rester dans `config.yml`.

### Section `stories`

| Clé | Label | Type | Défaut | Validation | Restart |
|---|---|---|---|---|---|
| `stories.enabled` | Stories activées | `bool` | `false` | — | Non |
| `stories.max_text_chars` | Caractères max (stories) | `int` | `400` | `≥ 50, ≤ 1000` | Non |

### Racine

| Clé | Label | Type | Défaut | Validation | Restart |
|---|---|---|---|---|---|
| `log_level` | Niveau de log | `str` (enum) | `"INFO"` | ∈ `{"DEBUG", "INFO", "WARNING", "ERROR"}` | Non |

---

## Endpoints HTTP

### `GET /`

**Vue d'ensemble opérationnelle.**

Réponse : page HTML `overview.html`.

Données affichées :
- État du scheduler : `paused` (depuis `scheduler_state`)
- Compteur de posts du jour : `daily_post_count`
- Niveau d'escalade : `escalation_level`
- Statut publications : `publications_suspended`
- Bandeau si `config_restart_required = "true"`
- Bandeau si `config_reload_requested = "true"` (changement en attente de prise en compte)
- 5 derniers posts (status, date, plateforme)
- Expiration du token Meta (jours restants depuis `meta_tokens`)

---

### `GET /config`

**Éditeur de configuration complet.**

Réponse : page HTML `config.html` — sections accordéon par groupe.

Données : pour chaque setting du registre :
- Valeur effective (override DB si présent, sinon valeur `config.yml`)
- Source : `"override"` ou `"défaut"`
- Timestamp `updated_at` si override

---

### `POST /config/set`

**Écriture d'un override (appelé via htmx).**

Corps (form-encoded) :
```
key=scheduler.generation_cron&value=0+*/6+*+*+*
```

Traitement :
1. Vérifier que `key` est dans le registre des settings exposés — sinon 400
2. Désérialiser `value` selon le `value_type` du registre
3. Valider (cf. colonne "Validation" du registre)
4. Si validation échoue → retourner le fragment HTML du champ avec message d'erreur inline
5. Si clé nécessite un redémarrage → écrire `config_restart_required = "true"` dans `scheduler_state`
6. Écrire dans `config_overrides` (UPSERT)
7. Écrire `config_reload_requested = "true"` dans `scheduler_state`
8. Retourner le fragment HTML du champ mis à jour (badge "override", valeur sauvegardée)

Réponse : fragment HTML (htmx swap `outerHTML` du champ concerné).

---

### `POST /config/reset/{key}`

**Suppression d'un override (retour à la valeur `config.yml`).**

Chemin : `key` encodé URL (ex: `scheduler.generation_cron`).

Traitement :
1. Vérifier que `key` est dans le registre
2. `DELETE FROM config_overrides WHERE key = ?`
3. Si `key` nécessitait un redémarrage → vérifier si `config_restart_required` peut être levé (plus aucun override restart-required présent)
4. Écrire `config_reload_requested = "true"` dans `scheduler_state`
5. Retourner le fragment HTML avec la valeur par défaut et le badge "défaut"

Réponse : fragment HTML.

---

### `GET /posts`

**Liste des posts récents.**

Query params :
- `limit` (int, défaut 20, max 100)
- `status` (str, optionnel) — filtre sur `posts.status`

Réponse : page HTML `posts.html`.

Données par post :
- `id`, `status`, `created_at`, `published_at`
- Source : Wikipedia (event title) ou RSS (article title)
- Plateformes publiées (IG, FB, Story)
- Erreurs éventuelles

---

### `GET /health`

**Healthcheck du service dashboard.**

Réponse : `200 OK`, body `{"status": "ok"}`.

Utilisé par Docker pour le healthcheck du container.

---

## Composants UI

### Widgets par type de setting

| `value_type` | Widget HTML | Notes |
|---|---|---|
| `bool` | `<input type="checkbox">` (toggle) | `checked` si `true` |
| `int` | `<input type="number" step="1">` | Attributs `min`/`max` selon validation |
| `float` | `<input type="number" step="0.01">` | Attributs `min`/`max` selon validation |
| `str` libre | `<input type="text">` | — |
| `str` enum | `<select>` | Options générées depuis le registre |
| `str\|null` enum | `<select>` | Option vide = `null` |
| `list[str]` | Textarea (une valeur par ligne) | Sérialisé en JSON à l'envoi |
| `list[dict]` | Sous-formulaire CRUD | Utilisé pour `content.rss.feeds` uniquement |

### Structure d'un champ dans `config.html`

```
┌─────────────────────────────────────────────────────────────┐
│ [Label]                               [badge: override|défaut]
│ [Description courte]                  [updated_at si override]
│
│ [widget d'édition]
│
│ [message d'erreur inline — htmx]
│
│ [Sauvegarder]  [Réinitialiser]  (Réinitialiser grisé si pas d'override)
└─────────────────────────────────────────────────────────────┘
```

### Interactions htmx

| Action | Attributs htmx | Target |
|---|---|---|
| Sauvegarder | `hx-post="/config/set"` `hx-swap="outerHTML"` | `#field-{key-sanitized}` |
| Réinitialiser | `hx-post="/config/reset/{key}"` `hx-swap="outerHTML"` | `#field-{key-sanitized}` |
| Toggle bool (auto-save) | `hx-post="/config/set"` `hx-trigger="change"` | `#field-{key-sanitized}` |

Les champs de type `str`, `int`, `float` nécessitent un clic explicite sur "Sauvegarder" (pas de `hx-trigger="change"` pour éviter les sauvegardes partielles en cours de frappe).

### Bandeau "Redémarrage requis"

Affiché en haut de page si `config_restart_required = "true"` dans `scheduler_state` :

```
⚠ Des paramètres modifiés nécessitent un redémarrage d'ancnouv pour être pris en compte.
  Commande : docker compose restart ancnouv
```

### Bandeau "En cours de chargement"

Affiché si `config_reload_requested = "true"` dans `scheduler_state` (le cache ancnouv n'a pas encore été invalidé) :

```
ℹ Modifications en attente de prise en compte par ancnouv (≤ 30s).
```

---

## Sécurité

### Authentification

Le dashboard ne contient aucun code d'authentification. Le port `8766` n'est exposé que sur `127.0.0.1` (loopback) dans `docker-compose.yml` — l'accès est sécurisé par le reverse proxy (géré hors de ce repo).

### Ce qui n'est jamais exposé

- Secrets `.env` : `TELEGRAM_BOT_TOKEN`, `META_APP_ID`, `META_APP_SECRET`, `IMAGE_SERVER_TOKEN`
- Tokens Meta (`meta_tokens.access_token`)
- `telegram.authorized_user_ids` (peut permettre d'usurper l'accès bot)
- `instagram.user_id`, `facebook.page_id`, `instagram.api_version` (paramètres structurels)
- `image_hosting.backend`, `image_hosting.remote_upload_url` (paramètres infrastructure)
- `database.*` (paramètres de la DB elle-même)

### Surface d'attaque

Le dashboard peut écrire dans `config_overrides` et `scheduler_state`. Un attaquant ayant accès au dashboard peut :
- Modifier des paramètres de contenu (hashtags, cron, etc.)
- Mettre en pause le scheduler
- Provoquer un redémarrage requis

Il ne peut pas :
- Lire ou modifier les tokens Meta
- Accéder aux secrets `.env`
- Exécuter du code arbitraire

Mitigation : service exposé sur loopback uniquement, accès sécurisé par le reverse proxy.

---

## Déploiement

### Structure du répertoire `dashboard/`

```
dashboard/
├── Dockerfile
├── requirements.txt
├── main.py                 # FastAPI app, montage des routers
├── db.py                   # connexion SQLite (propre au dashboard, pas de réimport ancnouv)
├── config_meta.py          # registre des settings : label, type, validation, requires_restart
├── routers/
│   ├── overview.py
│   ├── config.py
│   └── posts.py
└── templates/
    ├── base.html           # layout commun, inclusion htmx CDN
    ├── overview.html
    ├── config.html
    └── posts.html
```

> Le dashboard n'importe rien depuis `ancnouv/`. Il a sa propre couche d'accès DB minimale (`db.py`) utilisant SQLAlchemy async sur la même DB file. Les modèles ORM ne sont pas partagés — le dashboard utilise des requêtes SQL directes ou des modèles Pydantic locaux.

### `requirements.txt` (dashboard)

```
fastapi>=0.110
uvicorn[standard]>=0.29
jinja2>=3.1
aiosqlite>=0.20.0
sqlalchemy>=2.0
pydantic>=2.0
```

### `Dockerfile` (dashboard)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8766
HEALTHCHECK CMD curl -f http://localhost:8766/health || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8766"]
```

### Ajout dans `docker-compose.yml`

```yaml
ancnouv-dashboard:
  build:
    context: ./dashboard
    dockerfile: Dockerfile
  restart: unless-stopped
  depends_on:
    - ancnouv
  ports:
    - "127.0.0.1:8766:8766"
  volumes:
    - ./data:/app/data          # DB SQLite partagée (rw — écriture config_overrides)
    - ./config.yml:/app/config.yml:ro  # lecture des valeurs par défaut
  environment:
    - DB_PATH=/app/data/ancnouv.db
    - TZ=Europe/Paris
  networks:
    - ancnouv-net
```


### Séquence d'initialisation

Le dashboard n'a pas de phase `db init` propre. Il suppose que la DB est déjà initialisée par ancnouv (table `config_overrides` créée par la migration Alembic). Ordre de démarrage recommandé : ancnouv démarre en premier (`depends_on: ancnouv`), applique les migrations, puis le dashboard démarre.

Si le dashboard démarre avant que la migration soit appliquée → les requêtes sur `config_overrides` retournent une erreur SQL → le dashboard affiche une page d'erreur "DB non initialisée" avec instruction `python -m ancnouv db migrate`.

---

## Points de vigilance

### [DASH-W1] Écritures concurrentes SQLite

SQLite WAL tolère N readers + 1 writer simultané. Si ancnouv et le dashboard écrivent en même temps (rare mais possible : ancnouv écrit dans `scheduler_state` pendant que le dashboard écrit dans `config_overrides`), le `busy_timeout = 10000ms` doit absorber les contentions. Ne pas augmenter la fréquence d'écriture du dashboard (sauvegardes unitaires à la demande uniquement).

### [DASH-W2] Parité des validations

Les validations dans `config_meta.py` (dashboard) doivent être exactement identiques à celles dans `ancnouv/config.py` (Pydantic). Toute divergence permet à un utilisateur de sauvegarder une valeur que ancnouv rejettera au rechargement (l'override invalide est alors ignoré silencieusement — voir [DASH-A2], ce qui peut désorienter l'utilisateur). Point de maintenance : si une validation change dans `config.py`, la mettre à jour dans `config_meta.py`.

En particulier, la validation croisée `content.rss.min_delay_days < max_age_days` nécessite de lire les deux valeurs (override ou défaut) simultanément au moment de la sauvegarde de l'une ou l'autre.

### [DASH-W3] Valeur `config.yml` comme baseline de référence

Le dashboard lit `config.yml` pour afficher les valeurs par défaut. Si `config.yml` est modifié sur le VPS sans redémarrer le dashboard, les "valeurs par défaut" affichées seront périmées jusqu'au redémarrage du container. Ce cas est acceptable (configuration manuelle rare, message explicite recommandé).

### [DASH-W4] Bootstrap circulaire du Config ancnouv

`get_effective_config()` a besoin de la DB pour charger les overrides. Mais la DB est initialisée via `init_db()` qui a besoin du `Config` (pour le chemin du fichier). Ordre strict : `get_config()` (YAML seul, sans DB) → `init_db()` → `get_effective_config()` (YAML + overrides DB). Ne jamais appeler `get_effective_config()` avant `init_db()`.

### [DASH-W5] Override invalide ignoré silencieusement

Si un override stocké en DB ne passe plus la validation Pydantic (ex: la spec a changé, ou la valeur est corrompue), il est ignoré avec un log warning et la valeur YAML est utilisée. L'utilisateur ne voit pas d'erreur dans le dashboard — il voit la valeur effective (YAML) mais l'override est toujours présent en DB. Le dashboard doit afficher un indicateur visuel "override ignoré" si la valeur effective diffère de l'override stocké [DASH-A2].

### [DASH-W6] `content.rss.feeds` — type complexe

`feeds` est une `list[dict]` avec deux champs chacun. L'interface CRUD est plus complexe que les autres champs. En v1, un textarea JSON (une ligne par flux, format `{"url": "...", "name": "..."}`) est acceptable. Un vrai formulaire CRUD peut être implémenté en v2.

### [DASH-W7] Redémarrage d'ancnouv depuis le dashboard

Le dashboard ne redémarre pas ancnouv directement (ce qui nécessiterait l'accès au socket Docker — risque de sécurité). Il affiche uniquement la commande à exécuter. Une amélioration future pourrait passer par une API de contrôle limitée (endpoint dédié + Docker socket avec permissions restreintes), mais hors périmètre v1.

### [DASH-W8] `instagram.enabled` / `facebook.enabled` — dépendances config.yml

Activer Instagram ou Facebook via le dashboard sans que `user_id` / `page_id` soient configurés dans `config.yml` provoque une erreur de validation Pydantic au rechargement. Le dashboard doit vérifier la présence de ces valeurs (lecture de `config.yml`) avant d'autoriser l'activation, et afficher un message explicite si elles sont absentes [DASH-R1].
