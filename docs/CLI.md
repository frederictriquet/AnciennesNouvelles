# CLI — Interface en ligne de commande

> Référence : [SPEC-3.6], [SPEC-4.1]

> **[C-01] Commandes Telegram :** les commandes Telegram (`/start`, `/status`, `/pause`, `/resume`, `/force`, `/pending`, `/stats`, `/retry`, `/retry_ig`, `/retry_fb`, `/help`) sont documentées dans `docs/TELEGRAM_BOT.md`. Elles ne sont pas des commandes CLI (`python -m ancnouv`) — elles sont envoyées via Telegram.

---

## Entry point

```bash
python -m ancnouv <commande> [options]
```

Implémenté dans `ancnouv/__main__.py` avec `argparse` (stdlib — aucune dépendance supplémentaire).

---

## Structure des sous-commandes

```
ancnouv
├── start                          # Démarrer le scheduler + bot Telegram
├── setup
│   └── fonts                      # Télécharger les polices Google Fonts
│   # [CLI-C1] "setup" seul n'existe pas — seul "setup fonts" est défini en v1
├── auth
│   ├── meta                       # Flux OAuth Meta interactif (Instagram + Facebook)
│   └── test                       # Vérifier les tokens Meta stockés en DB [CLI-C3]
├── fetch [--prefetch]             # Collecter les événements Wikipedia (option, pas sous-cmd) [CLI-m4]
├── generate-test-image            # Générer une image de test
├── test
│   ├── telegram                   # Tester l'envoi Telegram
│   └── instagram                  # Tester la publication Instagram (publication réelle)
├── health                         # Vérification de santé de l'application
├── escalation
│   └── reset                      # Réinitialiser le niveau d'escalade du pool
├── images-server [--port PORT]    # Démarrer le serveur d'images (VPS uniquement)
└── db
    ├── init                       # Créer la DB et appliquer toutes les migrations
    ├── migrate                    # Appliquer les migrations en attente (alembic upgrade head)
    ├── status                     # Afficher l'état des migrations Alembic (alembic current)
    ├── backup                     # Sauvegarder data/ancnouv.db
    └── reset                      # DANGER : supprimer et recréer la DB (dev uniquement)
    # Note : il n'existe pas de commande `db rollback`. Pour revenir en arrière, utiliser
    # directement alembic downgrade -1 (ou alembic downgrade <revision>).
    # Voir DEPLOYMENT.md — section "Rollback de migration Alembic". [CLI-m7]
```

---

## Implémentation (`ancnouv/__main__.py`)

`main()` parse les arguments via `argparse` et appelle `_dispatch(args)`. Les deux fonctions sont dans le **même fichier** — Python résout les noms au moment de l'appel, pas de la définition. `_dispatch` catch toutes les exceptions non gérées et retourne un code de sortie propre (jamais de traceback brut).

```python
def main() -> None: ...
def _dispatch(args) -> int: ...
def _dispatch_inner(args) -> int: ...
```

`asyncio.run()` est utilisé à deux endroits dans `_dispatch_inner` [CLI-C4] :
1. Pour `start` : délègue à `scheduler.run(config)` qui appelle `asyncio.run(main_async(config))`
2. Pour `auth meta` et `auth test` : `_dispatch_inner` appelle directement `asyncio.run(async_main())` où `async_main` initialise la DB, ouvre une session, et délègue à la commande CLI

Dans les deux cas, `asyncio.run()` est le seul point d'entrée de la boucle événementielle. Aucune autre fonction du projet n'appelle `asyncio.run()` directement.

### Chargement de la config par commande

Les commandes sont divisées en deux groupes selon leur besoin de configuration :

| Groupe | Commandes | Chargement |
|--------|-----------|-----------|
| Sans config complète | `db`, `setup fonts`, `images-server` | Partiel ou aucun — `validate_meta` non déclenchée |
| Avec config complète | `start`, `auth meta`, `auth test`, `fetch`, `generate-test-image`, `test`, `health`, `escalation reset` | `Config()` complet avec tous les validators |

**Justification** : `db`, `setup fonts` et `images-server` doivent pouvoir s'exécuter avant `auth meta` (avant l'existence des tokens), ou sur un VPS sans configuration Meta/Telegram. `images-server` lit `IMAGE_SERVER_TOKEN` directement depuis l'environnement, sans instancier `Config()`. `generate-test-image` charge la config complète (palette, polices configurées, dimensions) — `validate_meta` s'exécute.

> **[CLI-M1] `db backup` et la config :** `db backup` est classé "Sans config complète" mais lit `database.backup_keep` depuis `Config()` pour la rotation des sauvegardes. Résolution : `db backup` charge **partiellement** `Config()` en isolant `DatabaseConfig` uniquement (pas les validators Meta/Telegram). Si `config.yml` est absent, `backup_keep` prend sa valeur par défaut Pydantic (`7`) sans lever d'erreur.

> **Gestion de `SystemExit(2)` :** `argparse` lève `SystemExit(2)` (qui hérite de `BaseException`, pas d'`Exception`) en cas d'arguments incorrects. `_dispatch` doit capturer `BaseException` (ou explicitement `SystemExit`) pour éviter une traceback non gérée. Le code de sortie `2` correspond à un usage incorrect (voir tableau des codes de retour).

### Routing vers les sous-modules CLI

Chaque sous-commande délègue à un module `ancnouv/cli/*.py` :

| Commande | Module | Fonction |
|----------|--------|---------|
| `start` | `ancnouv.scheduler` | `run(config) -> int` |
| `setup fonts` | `ancnouv.cli.setup` | `download_fonts() -> int` |
| `auth meta` | `ancnouv.cli.auth` | `cmd_auth_meta(config, session) -> int` |
| `auth test` | `ancnouv.cli.auth` | `cmd_auth_test(config, session) -> int` |
| `fetch` | `ancnouv.cli.fetch` | `run_fetch(config, prefetch: bool) -> int` |
| `generate-test-image` | `ancnouv.cli.generate` | `generate_test_image(config) -> int` |
| `test telegram/instagram` | `ancnouv.cli.test_commands` | `run_test(config, target: str) -> int` |
| `health` | `ancnouv.cli.health` | `run_health(config) -> int` |
| `escalation reset` | `ancnouv.cli.escalation` | `reset_escalation(config) -> int` |
| `db *` | `ancnouv.db.cli` | `run_db_command(subcommand: str) -> int` |
| `images-server` | `ancnouv.publisher.image_hosting` | `run_image_server(port: int, token: str) -> int` |

Chaque fonction retourne un code de sortie entier (`0` = succès, `1` = erreur applicative).

> **Paramètre `session` dans `auth meta` et `auth test` :** la session `AsyncSession` est créée par `_dispatch_inner` via `asyncio.run(async_main())` — `async_main` appelle `init_db(db_path)`, puis instancie une session via `async with get_session() as session:` avant de déléguer à `cmd_auth_meta(config, session)`. La session est gérée par `_dispatch_inner`, pas par la fonction CLI elle-même.

---

## Séquence d'initialisation obligatoire [CLI-C5]

Un `python -m ancnouv start` sans initialisation préalable échoue avec `DB not initialized`. Ordre obligatoire :

```bash
# 1. Initialiser la DB (crée ancnouv.db + migrations)
python -m ancnouv db init

# 2. Télécharger les polices (requis pour generate-test-image)
python -m ancnouv setup fonts

# 3. Authentifier Meta (requis pour instagram.enabled=true)
python -m ancnouv auth meta

# 4. Pré-collecter Wikipedia (optionnel mais recommandé)
python -m ancnouv fetch --prefetch

# 5. Vérifier l'état
python -m ancnouv health

# 6. Démarrer
python -m ancnouv start
```

---

## Variables d'environnement [CLI-C6]

| Variable | Utilisée par | Obligatoire | Description |
|----------|-------------|-------------|-------------|
| `TELEGRAM_BOT_TOKEN` | Toutes les commandes avec config complète | Oui | Token bot Telegram (`.env`) |
| `META_APP_ID` | `auth meta`, `auth test`, config complète | Oui si Meta activé | App ID Meta |
| `META_APP_SECRET` | `auth meta`, `auth test`, config complète | Oui si Meta activé | App Secret Meta |
| `IMAGE_SERVER_TOKEN` | `images-server`, `start` (backend=remote) | Oui si backend=remote | Token authentification upload |
| `ANCNOUV_DB_PATH` | `db init`, `db migrate`, `db status`, `db backup`, `db reset` | Non | Surcharge le chemin DB |
| `TELEGRAM_CHAT_ID` | Aucune commande Python — script curl systemd uniquement | Non | Notifications crash systemd |

---

## Référence complète

### `start`

```bash
python -m ancnouv start
```

Démarre l'application complète :

1. Charge et valide `config.yml` + `.env` (échec immédiat si invalide)
2. Vérifie que les migrations DB sont à jour (erreur si migrations en attente — lancer `db migrate`)
3. Si `image_hosting.backend = local` : démarre le serveur HTTP statique d'images
4. Démarre le bot Telegram en arrière-plan (polling)
5. Démarre l'`AsyncIOScheduler` APScheduler avec tous les jobs
6. Boucle asyncio jusqu'à SIGTERM/SIGINT

> **Coexistence avec `images-server`** : si `image_hosting.backend = 'local'`, `start` démarre automatiquement le serveur d'images embarqué. La commande `images-server` séparée est réservée au déploiement Docker (conteneur `ancnouv-images` dédié) ou à l'architecture hybride RPi+VPS (backend `remote`). Ne pas lancer les deux simultanément sur la même machine avec le même port.

> **[C-05] Port déjà occupé (`backend=local`) :** le serveur aiohttp lève `OSError` pendant l'étape 3 ("start local image server"). Le bot Telegram et APScheduler ne sont **pas** encore démarrés à ce stade → arrêt propre avec code `1`. Message : "Port {local_port} déjà utilisé. Arrêter le processus existant ou changer `image_hosting.local_port`."

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Arrêt propre (SIGTERM/SIGINT) |
| `1` | Config invalide, DB inaccessible, migrations en attente, erreur critique runtime |

---

### `setup fonts`

```bash
python -m ancnouv setup fonts
```

Télécharge les quatre polices Google Fonts dans `{CWD}/assets/fonts/` (chemin relatif au répertoire de travail courant — toujours lancer depuis la racine du projet). [CLI-m5]

| Fichier | Police | URL Google Fonts |
|---------|--------|-----------------|
| `PlayfairDisplay-Bold.ttf` | Playfair Display Bold | `https://fonts.gstatic.com/s/playfairdisplay/v37/nuFiD-vYSZviVYUb_rj3ij__anPXJzDwcbmjWBN2PKdFvUDQ.ttf` (static, weight=700) |
| `LibreBaskerville-Regular.ttf` | Libre Baskerville Regular | `https://fonts.gstatic.com/s/librebaskerville/v14/kmKnZrc3Hgbbcjq75U4uslyuy4kn0qNcaxYaDcs.ttf` |
| `LibreBaskerville-Italic.ttf` | Libre Baskerville Italic | `https://fonts.gstatic.com/s/librebaskerville/v14/kmKhZrc3Hgbbcjq75U4uslyuy4kn0qNXaxMaDg.ttf` |
| `IMFellEnglish-Regular.ttf` | IM Fell English Regular | `https://fonts.gstatic.com/s/imfellenglish/v17/Ktk1ALSLW8zDe0rthJysWo9SN7LhKbMXUQ.ttf` |

> Utiliser les URLs **static** (pas les variable fonts) — les polices variables ont un format différent incompatible avec `ImageFont.truetype` de Pillow.

Si une police existe déjà, elle n'est pas re-téléchargée (idempotent).

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Toutes les polices présentes ou téléchargées avec succès |
| `1` | Échec de téléchargement réseau |

---

### `auth meta`

```bash
python -m ancnouv auth meta
```

Guide interactif d'authentification Meta (voir INSTAGRAM_API.md — section `cmd_auth_meta`) :

> **[CLI-C2] Scopes OAuth requis :** configurer dans le tableau de bord de la Meta App → Permissions et fonctionnalités (voir DEPLOYMENT.md — section "Permissions (scopes) requis") : `instagram_basic`, `instagram_content_publish`, `instagram_creator_manage_content` (comptes Créateur), `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`.
>
> **[CLI-M5] Conflit de port :** `auth meta` écoute sur `localhost:8080`. La commande `images-server` écoute sur le port `8765` (configurable). Ces deux ports sont distincts et ne se conflitent pas. Vérifier uniquement que le port 8080 n'est pas déjà utilisé (`lsof -i :8080`).
>
> **Prérequis :** `db init` doit avoir été exécuté (`meta_tokens` n'existe que si la DB est initialisée). `META_APP_ID` et `META_APP_SECRET` dans `.env`. `instagram.enabled=false` et `facebook.enabled=false` dans `config.yml`.

1. Démarre un serveur HTTP temporaire sur `localhost:8080` pour capturer le callback OAuth. Si le port 8080 est déjà occupé, `auth meta` échoue avec `OSError: [Errno 98] Address already in use` — vérifier avec `lsof -i :8080` avant le lancement. Le serveur se ferme automatiquement après réception du callback (timeout : 120s).
2. Construit et affiche l'URL d'autorisation OAuth — l'utilisateur l'ouvre dans un navigateur
3. Après autorisation, Meta redirige vers `http://localhost:8080/callback?code=...`
4. Le serveur capture le code automatiquement (pas de saisie clavier)
5. Échange le code → token court → token long (60 jours)
6. Récupère les Pages administrées (sélection interactive si plusieurs)
7. Récupère le Page Access Token permanent et l'IG User ID
8. Stocke **exclusivement en DB** (`meta_tokens`) — aucun token écrit dans `.env`
9. Affiche la date d'expiration du token utilisateur

> **[C-06] Déploiement sur VPS sans accès au navigateur :**
> ```
> 1. Dans le terminal local : ouvrir un tunnel SSH
>    ssh -L 8080:localhost:8080 user@votre-vps
> 2. Dans un second terminal (sur le VPS ou via tmux) :
>    docker compose run --rm -p 8080:8080 ancnouv python -m ancnouv auth meta
> 3. Ouvrir l'URL affichée dans le navigateur local
> 4. Autoriser → callback capturé via le tunnel
> ```

> **Lever `publications_suspended` :** quand les publications sont suspendues (token expiré, `publications_suspended="true"` dans `scheduler_state`), `auth meta` est la commande pour les reprendre — elle renouvelle le token **et** réinitialise `publications_suspended="false"` à l'issue d'une authentification réussie. Il n'existe pas de commande séparée pour lever uniquement la suspension sans renouvellement du token (ne serait utile que si le token était encore valide, ce qui ne se produit pas en pratique).

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Authentification réussie, tokens stockés en DB |
| `1` | Erreur réseau, code OAuth invalide, DB inaccessible |

---

### `auth test`

```bash
python -m ancnouv auth test
```

Vérifie que les tokens Meta stockés en DB sont valides en effectuant un appel API de test (ex: `GET /me?fields=id` avec le token utilisateur, `GET /{page_id}?fields=id` avec le token Page). N'effectue aucune publication. Affiche l'identité Meta associée et la date d'expiration du token utilisateur.

> **[CLI-m1] Session SQLAlchemy :** `auth test` reçoit une `AsyncSession` de `_dispatch_inner` mais n'effectue pas de modifications DB (lecture uniquement des tokens existants via `SELECT`). La session est en lecture seule. Elle est passée en paramètre pour uniformité avec `auth meta` — permet d'utiliser la même infrastructure `async_main` pour les deux commandes.

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Tokens valides et accessibles |
| `1` | Token absent ou expiré, erreur réseau, DB inaccessible |

---

### `fetch`

```bash
python -m ancnouv fetch [--prefetch]
```

> **[CLI-M8] Prérequis :** `db init` doit avoir été exécuté (`events` n'existe que si la DB est initialisée).

| Option | Description |
|--------|-------------|
| _(sans option)_ | Collecte les événements Wikipedia pour aujourd'hui |
| `--prefetch` | Collecte pour les `content.prefetch_days` prochains jours (défaut : 30) |

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Collecte terminée (même si 0 nouveaux événements) |
| `1` | Wikipedia API inaccessible et cache vide |

> **[C-02] Comportement sur erreur réseau avec cache existant :** si l'API Wikipedia est inaccessible mais que la DB contient déjà des événements pour la date demandée, `fetch` retourne `0` (succès — le cache suffit). Si la DB est vide pour la date ET que l'API est inaccessible → code `1` avec message "Wikipedia API inaccessible et aucun événement en cache pour {date}".

---

### `generate-test-image`

```bash
python -m ancnouv generate-test-image
```

Génère une image avec un événement fictif et la sauvegarde dans `data/test_output.jpg`. Ouvre l'image dans le viewer par défaut du système si disponible.

> **[CLI-M9] Sur VPS headless (sans écran) :** le viewer est tenté via `subprocess.call(["xdg-open", ...])`. Sur un VPS sans display, cet appel échoue silencieusement (exception ignorée) — l'image est générée dans `data/test_output.jpg` et son chemin est affiché en stdout. Copier l'image via `scp` ou `docker cp` pour la visualiser localement.

Utile pour valider les polices, la texture papier et le rendu visuel sans toucher à la DB ni aux APIs. Charge la config complète — **`validate_meta` s'exécute** : s'assurer que `instagram.enabled=false` et `facebook.enabled=false` si les tokens ne sont pas encore configurés.

> **[C-03] Token absent avec `instagram.enabled: true` :** `validate_meta` dans `Config()` bloque si `instagram.user_id == ""` quand `instagram.enabled=true`. Garder `instagram.enabled=false` et `facebook.enabled=false` dans `config.yml` pendant les tests de génération d'image.

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Image générée (chemin affiché en stdout) |
| `1` | Polices manquantes, erreur Pillow, ou config invalide |

---

### `test telegram`

```bash
python -m ancnouv test telegram
```

Envoie un message de test au(x) `telegram.authorized_user_ids` configuré(s). Permet de valider le token du bot et les IDs utilisateurs sans publication Meta.

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Message envoyé avec succès |
| `1` | Token Telegram invalide, utilisateur non joignable |

---

### `test instagram`

```bash
python -m ancnouv test instagram
```

> **Publication réelle.** À utiliser uniquement avec des comptes de test.

> **[CLI-M4] Prérequis :**
> - Token Meta valide en DB (`auth meta` exécuté avec succès)
> - `instagram.enabled: true` et `instagram.user_id` renseigné dans `config.yml`
> - Serveur d'images opérationnel (`images-server` démarré si `backend=remote`, ou `start` pour déclencher `backend=local`)
> - `db init` exécuté
> - Police téléchargées (`setup fonts`)

Publie un post de test sur Instagram et Facebook (selon la config). Génère une image de test, l'uploade vers le serveur d'images, crée le container Instagram et publie.

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Publication réussie (post_id affiché en stdout) |
| `1` | Token expiré, erreur API Meta, upload image échoué |

---

### `health`

```bash
python -m ancnouv health
```

Vérifie l'état de tous les composants et affiche un rapport :

```
Base de données : OK (15 342 événements, 127 posts)
Wikipedia API  : OK
Telegram Bot   : OK (@ancnouv_bot)
Token Meta     : OK (expire dans 45 jours)
Scheduler      : ACTIF (prochain post : 21/03/2026 16:00)
Polices        : 2/4 présentes (lancer : python -m ancnouv setup fonts)
```

L'état du scheduler ("ACTIF", "PAUSE") est lu depuis `scheduler_state` en DB. Le "prochain post" est calculé depuis l'expression cron via `CronTrigger.from_crontab(config.scheduler.generation_cron).get_next_fire_time(None, datetime.now(tz))` — aucun accès à `scheduler.db` (le job store APScheduler) qui peut être verrouillé si l'app tourne. Cela permet à `health` de fonctionner même si le scheduler n'est pas démarré (ex: diagnostic post-crash).

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Tous les composants critiques OK (avertissements tolérés) |
| `1` | Au moins un composant critique en erreur |

Composants critiques (exit `1` si KO) : DB, token Meta. [CLI-M2] Justification : sans DB, aucune fonctionnalité n'est opérationnelle. Sans token Meta, les publications échouent systématiquement. Le bot Telegram est non-critique car son absence bloque uniquement la validation manuelle (en mode `auto_publish=true`, le système reste opérationnel sans bot).
Composants non-critiques (avertissement uniquement) : Wikipedia API (cache DB disponible), polices (image dégradée possible), Telegram Bot.

> **[C-04] Composant "serveur images" :**
> - `backend=local` : vérifier que le port `local_port` (8765) n'est pas déjà occupé (`socket.connect(("localhost", local_port))`) — status `ARRÊTÉ` si non accessible (normal si l'app ne tourne pas encore)
> - `backend=remote` : vérifier que `remote_upload_url` est accessible (HEAD avec timeout 3s) — status `KO` si inaccessible
>
> Composant non-critique : `health` retourne `0` même si le serveur images est KO (avertissement uniquement).

> **[CLI-M3] `health` avec `publications_suspended=true` ou `escalation_level > 0` :**
> - `publications_suspended="true"` : affiché comme avertissement `⚠️ Publications suspendues (token expiré)`. Pas d'exit `1` car ce n'est pas une erreur d'infrastructure — lancer `auth meta` pour lever la suspension.
> - `escalation_level > 0` : affiché comme info `ℹ️ Escalade niveau N/5`. Pas d'avertissement — c'est un comportement normal de gestion du stock.

---

### `escalation reset`

```bash
python -m ancnouv escalation reset
```

Remet `scheduler_state.escalation_level` à `0` (état nominal). Envoie une notification Telegram de confirmation.

> **[CLI-M6] Si le bot Telegram est inaccessible :** la notification est silencieusement ignorée (log WARNING). `escalation reset` retourne `0` même si la notification échoue — le reset de `escalation_level` est l'opération principale, la notification est secondaire.

À utiliser après avoir enrichi manuellement la DB d'événements, ou pour revenir à la stratégie de collecte de base.

> `escalation reset` remet uniquement `escalation_level = 0`. Il ne lève **pas** `publications_suspended` — si les publications sont suspendues (token expiré), relancer `auth meta` pour lever cette suspension.

> **[C-10] Import manuel d'événements :** pour enrichir la DB d'événements sans passer par l'API Wikipedia (ex: import CSV/JSON), utiliser directement SQLite : `sqlite3 data/ancnouv.db < events_import.sql`. Le format attendu est celui de la table `events` (voir DATABASE.md). Après import, lancer `escalation reset` si le niveau d'escalade était monté en raison d'un stock insuffisant.

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Escalade réinitialisée |
| `1` | DB inaccessible |

---

### `images-server`

```bash
python -m ancnouv images-server [--port PORT]
```

| Option | Défaut | Description |
|--------|--------|-------------|
| `--port PORT` | `8765` | Port d'écoute du serveur aiohttp |

Démarre uniquement le serveur d'images aiohttp (sans scheduler ni bot Telegram). Utilisé dans deux architectures :
- **Docker** : conteneur `ancnouv-images` dédié (voir DEPLOYMENT.md — Docker)
- **systemd sans Docker** : service `ancnouv-images.service` séparé sur le même VPS (voir DEPLOYMENT.md — systemd)

Dans les deux cas, `ancnouv` tourne avec `backend: "remote"` — le scheduler envoie les images via HTTP upload vers ce serveur.

**`run_image_server` (`ancnouv/publisher/image_hosting.py`) :**

```python
async def run_image_server(port: int = 8765, token: str = "") -> int: ...
```

`_dispatch_inner` lit `IMAGE_SERVER_TOKEN` depuis `os.environ` et le passe en argument : `run_image_server(port=port, token=os.environ.get("IMAGE_SERVER_TOKEN", ""))`. Si la variable est absente ou vide, `run_image_server` appelle `sys.exit(1)` avant de démarrer le listener. Ce design centralise la lecture de l'env dans `_dispatch_inner` et rend `run_image_server` testable avec un token explicite. `run_image_server` **ne charge pas `Config()`**, ce qui permet de le lancer sur un VPS sans configurer Meta/Telegram (voir INSTAGRAM_API.md — section `run_image_server`).

Routes exposées :

| Méthode | Route | Accès | Réponse succès |
|---------|-------|-------|----------------|
| `POST` | `/images/upload` | Protégé — `Authorization: Bearer <IMAGE_SERVER_TOKEN>` | HTTP 200 — `{"filename": "<nom_du_fichier>"}` |
| `GET` | `/images/{filename}` | Public | HTTP 200 — fichier image |

**Codes d'erreur :** `HTTP 401` (token invalide ou absent), `HTTP 400` (fichier manquant ou format non supporté).

> **[C-09] Comportement côté client sur `HTTP 400` :** l'upload échoue avec `ImageHostingError`. Le post reste en statut `approved` (non publié). L'utilisateur est notifié via Telegram : "Erreur upload image (HTTP 400) — vérifier le format du fichier." Le post peut être retryé via `/retry`.

**Prérequis :** `IMAGE_SERVER_TOKEN` dans l'environnement, non vide.

> **`IMAGE_SERVER_TOKEN` vide (`""`) :** traité comme absent — le serveur refuse de démarrer avec code `1`. Une valeur vide démarrerait le serveur sans authentification (toutes les requêtes acceptées), ce qui constitue une faille de sécurité. `run_image_server` vérifie `if not token: sys.exit(1)` avant de démarrer le listener.

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Arrêt propre (SIGTERM/SIGINT) |
| `1` | Port déjà utilisé, ou `IMAGE_SERVER_TOKEN` absent/vide |

---

### `db init`

```bash
python -m ancnouv db init
```

Crée `data/ancnouv.db` et applique toutes les migrations Alembic depuis zéro. Idempotent.

`db init` est dans le groupe "Sans config complète" — il ne charge **pas** `config.yml`. Il lit uniquement `ANCNOUV_DB_PATH` depuis l'environnement pour déterminer le chemin de la DB (défaut : `data/ancnouv.db`). Si `config.yml` est absent au premier lancement, `db init` réussit normalement — la config n'est pas requise pour initialiser la DB.

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | DB créée ou déjà à jour |
| `1` | Erreur disque, permissions insuffisantes |

---

### `db migrate`

```bash
python -m ancnouv db migrate
```

Applique les migrations Alembic en attente (`alembic upgrade head`). À lancer après chaque mise à jour de l'application.

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Migrations appliquées (ou déjà à jour) |
| `1` | Conflit de migration, DB corrompue |

---

### `db status`

```bash
python -m ancnouv db status
```

Affiche la révision Alembic courante et les migrations en attente.

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | DB accessible — affiche la révision courante |
| `1` | DB inaccessible ou erreur Alembic |

---

### `db backup`

```bash
python -m ancnouv db backup
```

Copie `data/ancnouv.db` vers `data/ancnouv_YYYYMMDD_HHMMSS.db` (format avec l'heure pour éviter les écrasements de sauvegardes multiples dans la journée). Conserve les `database.backup_keep` dernières sauvegardes (défaut : 7) et supprime les plus anciennes. [CLI-m6] Tri par nom de fichier **alphabétique décroissant** (le format `YYYYMMDD_HHMMSS` garantit que l'ordre alphabétique = ordre chronologique). Les sauvegardes les plus récentes ont un nom lexicographiquement plus grand.

> **`scheduler.db` non couvert :** `db backup` ne sauvegarde que `ancnouv.db`. La base APScheduler (`data/scheduler.db`) est sauvegardée séparément par le crontab documenté dans DEPLOYMENT.md, avec rotation bornée à **7 fichiers** (aligné sur `database.backup_keep: 7`).

**Mécanisme :** utilise `VACUUM INTO 'data/ancnouv_YYYY....db'` (SQL SQLite) pour garantir une copie cohérente en mode WAL — contrairement à `shutil.copy2` qui peut produire une sauvegarde incohérente si l'app écrit pendant la copie. Ne pas remplacer par `shutil.copy2`.

> **[C-07]** Cette commande est sûre à exécuter pendant que `start` tourne — `VACUUM INTO` crée une copie cohérente en mode WAL sans perturber les écritures en cours.

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | Sauvegarde créée |
| `1` | Erreur disque, espace insuffisant |

---

### `db reset`

```bash
python -m ancnouv db reset
```

> **Destructif — dev uniquement.** Demande une confirmation interactive avant d'exécuter.

Supprime `data/ancnouv.db` et le recrée depuis zéro (équivalent à `db init`).

**Codes de sortie :**

| Code | Cause |
|------|-------|
| `0` | DB réinitialisée |
| `3` | Annulé par l'utilisateur (confirmation refusée) — code distinct de `0` pour permettre la détection dans les scripts |
| `1` | Erreur disque |

---

## Codes de retour — récapitulatif

| Code | Signification |
|------|---------------|
| `0` | Succès |
| `1` | Erreur applicative (config invalide, DB inaccessible, API échouée) |
| `2` | Usage incorrect (argparse — arguments manquants ou inconnus) [CLI-m3] : `_dispatch` capture `SystemExit(2)` et `BaseException` pour garantir un code de sortie propre (jamais de traceback brut) |
| `3` | Action annulée par l'utilisateur (`db reset` — confirmation refusée) [CLI-m2] |
