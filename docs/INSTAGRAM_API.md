# Publication Meta (Instagram + Facebook)

> Référence : [SPEC-3.4], C-4.2.3

---

## Vue d'ensemble du processus

La publication via l'API officielle Meta requiert un ensemble de comptes et d'entités liés entre eux. Ce document couvre l'intégralité du processus pour les deux plateformes.

> **Terminologie des tokens** : trois désignations coexistent pour le même objet selon le contexte :
> - **Diagramme [IG-0]** : `USER_ACCESS_TOKEN` (notation Meta officielle)
> - **Code / DB** : `user_long` (valeur de `token_kind` dans `meta_tokens`)
> - **Glossaire SPEC.md** : "Token utilisateur long"
> Ces trois désignations référencent le même token. `page` (token_kind) désigne le Page Access Token permanent.

---

## [IG-0] Chaîne de dépendances complète

```
Compte Facebook PERSONNEL (facebook.com)
    │
    ├── administre ──────────────────► App Meta (developers.facebook.com)
    │                                     (APP_ID + APP_SECRET)
    │
    └── administre ──────────────────► Page Facebook (publique)
                                          (PAGE_ID + PAGE_ACCESS_TOKEN)
                                              │
                                              └── liée à ──► Compte Instagram
                                                              Business/Créateur
                                                              (IG_USER_ID + USER_ACCESS_TOKEN)
```

**Conséquence** : un compte Facebook personnel est **obligatoire**. Il n'est pas visible publiquement mais sert de pivot d'administration.

---

## [IG-1] Prérequis et mise en place (à faire une seule fois)

### [IG-1.1] Créer un compte Facebook personnel

Si vous n'en avez pas déjà un :
1. Créer un compte sur facebook.com (compte personnel, pas une Page)
2. Ce compte sera le **propriétaire de l'App Meta** et **administrateur de la Page**
3. Il n'apparaît pas publiquement sur la Page ni sur Instagram

### [IG-1.2] Créer le compte Instagram

1. Créer un compte Instagram classique (si pas déjà fait)
2. Le convertir en **compte Créateur** ou **compte Professionnel (Business)** :
   - Paramètres → Compte → Passer à un compte professionnel
   - Choisir **Créateur** (adapté aux créateurs de contenu individuels)
   - Choisir une catégorie (ex : "Média/Actualités")

> **Pourquoi ?** L'API Instagram Graph ne supporte que les comptes Business et Créateur.

### [IG-1.3] Créer une Page Facebook et lier Instagram (Business Portfolio)

La Page Facebook sert à deux fins : (1) prérequis technique pour l'API Instagram, (2) canal de publication à part entière.

1. Connecté avec le compte Facebook personnel, aller sur facebook.com → Créer une Page
2. Choisir une catégorie (ex : "Site d'actualités/médias")
3. Nommer la Page (ex : "Anciennes Nouvelles")
4. Stocker l'ID de la Page dans `config.yml` : `facebook.page_id`

**Liaison Instagram ↔ Page (méthode Business Portfolio — interface Meta 2024+) :**

5. Aller sur [business.facebook.com](https://business.facebook.com) → créer ou utiliser un Business Portfolio existant
6. Dans le Portfolio → Paramètres → Comptes → **Comptes Instagram** → **Ajouter** → saisir les identifiants du compte Instagram Professional
7. Dans le Portfolio → Paramètres → Comptes → **Pages** → ajouter la Page Facebook créée ci-dessus

> L'ancienne méthode directe (Paramètres Instagram → Compte → Page Facebook liée) n'est plus disponible pour les apps Business récentes. Utiliser exclusivement la méthode Business Portfolio décrite ci-dessus.

### [IG-1.4] Créer une application Meta

1. Aller sur [developers.facebook.com](https://developers.facebook.com)
2. Se connecter avec le **compte Facebook personnel**
3. Cliquer "Créer une application" → Type **Business**
4. Ajouter le produit : **Instagram Graph API**
5. Dans "Paramètres de base" : noter l'**App ID** et l'**App Secret**
6. Dans "Rôles" : s'ajouter comme **Testeur** (pas Développeur — un Développeur a accès au code source de l'app dans le Dashboard, un Testeur a uniquement accès aux APIs en mode développement. Pour publier sur un compte Instagram personnel avec l'app, le compte Instagram doit être rattaché à un rôle **Testeur** ou supérieur)
7. Dans "Facebook Login" → "Paramètres OAuth du client" : ajouter `http://localhost:8080/callback` dans **"URI de redirection OAuth valides"**. Sans cette étape, le flux d'authentification échoue avec `redirect_uri does not match`.

### [IG-1.5] Obtenir les IDs Instagram et Facebook

```bash
# Avec un token temporaire (voir IG-2.1) :
# Note : v21.0 ci-dessous est illustratif. L'app utilise config.instagram.api_version (défaut : "v21.0").

# 1. Récupérer les Pages administrées + leur Page Access Token
curl "https://graph.facebook.com/v21.0/me/accounts?access_token=TOKEN"
# → [{"id": "123456789", "name": "Anciennes Nouvelles", "access_token": "PAGE_TOKEN", ...}]
# → Stocker id dans config.yml : facebook.page_id

# 2. Récupérer l'IG User ID depuis la Page
curl "https://graph.facebook.com/v21.0/{PAGE_ID}?fields=instagram_business_account&access_token=TOKEN"
# → {"instagram_business_account": {"id": "17841405822304884"}}
# → Stocker dans config.yml : instagram.user_id
```

La commande `python -m ancnouv auth meta` effectue tout cela automatiquement (voir [IG-6]).

---

## [IG-2] Authentification et tokens

### [IG-2.1] Obtenir un token court (Short-Lived)

Le token court est obtenu via le **flux OAuth web** (une seule fois lors du setup) :

```
URL d'autorisation (sauts de ligne pour lisibilité — à construire comme une URL sans sauts de ligne) :
https://www.facebook.com/dialog/oauth?
  client_id={APP_ID}
  &redirect_uri=http://localhost:8080/callback
  &scope=instagram_basic,instagram_content_publish,instagram_creator_manage_content,pages_read_engagement,pages_manage_posts,pages_show_list
  &response_type=code
```

> Les sauts de ligne ci-dessus sont pour la lisibilité uniquement. La commande `auth meta` construit l'URL automatiquement — ne pas copier-coller ce bloc dans un navigateur.

> **`pages_show_list`** : scope obligatoire pour que `/me/accounts` retourne la liste des Pages administrées. Sans ce scope, l'appel retourne un tableau vide même si des Pages existent — le setup Meta est bloqué sans message d'erreur explicite.

> **`instagram_creator_manage_content`** : requis pour les comptes Instagram de type **Créateur** (ignoré pour les comptes Business). L'inclure systématiquement dans l'URL OAuth garantit la compatibilité avec les deux types de comptes — Meta l'ignore silencieusement si le compte est Business.

Échange du code contre un token :

```bash
curl "https://graph.facebook.com/v21.0/oauth/access_token\
?client_id={APP_ID}\
&redirect_uri=http://localhost:8080/callback\
&client_secret={APP_SECRET}\
&code={CODE}"
# → {"access_token": "...", "token_type": "bearer"}
```

Ce token est valable **1 heure**.

### [IG-2.2] Obtenir un token long (Long-Lived)

```bash
curl "https://graph.facebook.com/v21.0/oauth/access_token\
?grant_type=fb_exchange_token\
&client_id={APP_ID}\
&client_secret={APP_SECRET}\
&fb_exchange_token={SHORT_LIVED_TOKEN}"
# → {"access_token": "...", "token_type": "bearer", "expires_in": 5183944}
```

Ce token est valable **~60 jours**.

### [IG-2.3] Renouveler un token long

Même endpoint que [IG-2.2] avec le token long en `fb_exchange_token`.

> **Règle importante** : un token long non utilisé pendant 60 jours **expire définitivement**. Ré-authentification manuelle via navigateur obligatoire.

> **Contrainte de renouvellement anticipé :** Meta peut refuser de renouveler un token long si la durée restante est **supérieure à un certain seuil** (généralement > 30 jours). Dans ce cas, l'API retourne un succès apparent mais ne prolonge pas réellement la durée. Ce comportement est irrégulier selon les versions API Meta. `get_valid_token` tente le refresh si `remaining <= 7` — ce seuil conservateur évite les problèmes de refus.

> **Vérification de progression :** en cas de refresh, vérifier que `expires_at` a progressé avant de logger un succès : `assert new_expires_at > old_expires_at`. Si `expires_at` n'a pas progressé après le refresh, considérer l'opération comme échouée et déclencher l'alerte J-7.

### [IG-2.4] Stratégie de gestion des tokens dans l'app

**Token Instagram vs Facebook :**
- Publication Instagram : `user_long` token (User Access Token)
- Publication Facebook : `page` token (Page Access Token — permanent)

`TokenManager.get_valid_token(session, token_kind='user_long')` pour Instagram, `get_valid_token(session, token_kind='page')` pour Facebook.

**Source de vérité unique : la base de données.** Les tokens sont stockés exclusivement dans `meta_tokens`. Le fichier `.env` ne contient jamais les tokens opérationnels.

**Alertes progressives** (job `job_check_token` — voir SCHEDULER.md [JOB-5], quotidien à 9h) :

> **Deux mécanismes distincts et complémentaires — non redondants :**
> - `MetaToken.last_alert_days_threshold` (table `meta_tokens`, type `INTEGER`) : mémorise l'entier du dernier seuil (30, 14, 7, 3, 1) pour lequel une alerte a été envoyée. Évite de renvoyer la même alerte plusieurs fois pour le même seuil. NULL = aucune alerte envoyée. Rôle : **anti-spam**.
> - `scheduler_state.token_alert_level` (table `scheduler_state`, type `TEXT`) : chaîne lisible (`"normal"`, `"30j"`, `"14j"`, `"7j"`, `"3j"`, `"1j"`, `"expired"`) pour l'affichage dans `/status`. Écrite par `job_check_token`, lue par `cmd_status`. Rôle : **affichage d'état**.
> Ces deux champs coexistent légitimement avec des rôles différents.

> **Anti-spam :** `job_check_token` compare le seuil courant avec `MetaToken.last_alert_days_threshold`. Si identique, l'alerte n'est pas renvoyée (une seule notification par seuil, même si le job tourne quotidiennement). Voir SCHEDULER.md [JOB-5] pour le détail du mécanisme.

| Jours restants | Action |
|---------------|--------|
| 30 | Notification Telegram informative |
| 14 | Avertissement |
| 7 | Alerte + tentative de refresh automatique |
| 3 | Alerte critique + refresh obligatoire |
| 1 | Alerte bloquante |
| ≤ 0 | Token expiré — publications suspendues |

**Fonctions `publisher/token_manager.py` :**

```python
ALERT_THRESHOLDS = [30, 14, 7, 3, 1]  # jours

def days_until_expiry(expires_at: datetime) -> int: ...
def get_alert_threshold(remaining: int) -> int | None: ...
```

`days_until_expiry` : retourne `(expires_at - now(utc)).days` — valeur négative si expiré.

> **Timezone de `expires_at` :** SQLite stocke les `datetime` sans information de timezone (naive). `expires_at` est stocké et lu comme datetime UTC naïf (sans `tzinfo`). `days_until_expiry` doit utiliser `datetime.now(timezone.utc).replace(tzinfo=None)` pour la comparaison — ou stocker/lire `expires_at` avec `tzinfo=timezone.utc` explicitement. La convention retenue : **`expires_at` est toujours UTC, stocké sans tzinfo, comparé avec `datetime.utcnow()`** (ou `datetime.now(timezone.utc).replace(tzinfo=None)`). Ne jamais utiliser `datetime.now()` sans timezone dans `days_until_expiry` — le calcul serait faux si le système tourne en heure locale.

`get_alert_threshold` :
- `remaining <= 0` → retourne `0` (token expiré ou expire aujourd'hui — distinct de `1` pour afficher "TOKEN EXPIRÉ" dans le message Telegram)
- `remaining` dans `ALERT_THRESHOLDS` → retourne `remaining`
- Sinon → retourne `None` (aucune notification ce jour — évite le spam quotidien)

```python
class TokenManager:
    def __init__(self, meta_app_id: str, meta_app_secret: str, notify_fn: Callable[[str], Awaitable[None]] | None = None) -> None:
        # ...
        self._lock = asyncio.Lock()  # protège les appels concurrents à get_valid_token/refresh
    async def get_valid_token(self, session: AsyncSession, token_kind: str = "user_long") -> str:
        # async with self._lock: — acquis avant vérification d'expiration
        # InstagramPublisher et FacebookPublisher partagent la même instance de TokenManager
        # (singleton passé depuis publish_to_all_platforms) — le verrou empêche deux refreshes simultanés
        ...
```

`get_valid_token` :
- `token_kind='page'` : retourne le token directement (permanent, pas d'expiration)
- `token_kind='user_long'` : vérifie l'expiration, tente le refresh si `remaining <= 7`. Lève `TokenExpiredError` **uniquement si `remaining <= 0`** (token expiré ou expire aujourd'hui). Entre J-7 et J-1, un échec du refresh est absorbé silencieusement — le token encore valide est retourné et la publication continue. `job_check_token` est le signal visible en cas de refresh raté.

> **Comportement si refresh échoue entre J-7 et J-1 :** l'échec de `_refresh_token` est loggué et la publication continue avec le token actuel (toujours valide). `get_valid_token` lève `TokenExpiredError` **uniquement si `remaining <= 0`**. Entre J-7 et J-2, un refresh raté est donc **absorbé silencieusement** — la notification Telegram (J-7, J-3, J-1) est le seul signal visible. `job_check_token` doit détecter les échecs de refresh en testant si `last_refreshed_at` n'a pas progressé depuis le dernier contrôle et envoyer l'alerte J-3 / J-1 avec le message "Renouvellement automatique échoué. Action requise."

Méthodes privées :
```python
    async def _load_token(self, session: AsyncSession, token_kind: str) -> MetaToken | None: ...
    async def _refresh_token(self, current_access_token: str) -> tuple[str, int]: ...
    async def _save_token(self, session: AsyncSession, new_access_token: str, expires_in_seconds: int) -> None: ...
```

`_refresh_token` : GET `https://graph.facebook.com/v21.0/oauth/access_token` avec `grant_type=fb_exchange_token`. Lit le corps JSON avant `raise_for_status()` — si `"error"` dans le JSON, lève `Exception` avec le message Meta. Retourne `(access_token, expires_in_seconds)`. **`expires_in` peut être absent** de la réponse dans certaines conditions (token permanent ou réponse partielle) : utiliser `data.get("expires_in", 5184000)` avec 60 jours comme valeur de repli — ne jamais accéder avec `data["expires_in"]` directement.

`_save_token` : `UPDATE meta_tokens SET access_token=..., expires_at=..., last_refreshed_at=now() WHERE token_kind='user_long'`, commit immédiat. **Contrainte intentionnelle :** `_save_token` ne renouvelle que le token `user_long` (le seul qui expire). Le token `page` est permanent et n'est jamais mis à jour par `_save_token` — seul `auth meta` peut recréer le token `page` si nécessaire.

> **Procédure si token expiré :** `python -m ancnouv auth meta` — renouvelle le token depuis zéro (nouveau flux OAuth). Les publications reprennent automatiquement.

### [IG-2.5] Permissions requises

| Permission | Rôle |
|-----------|------|
| `instagram_basic` | Lire les infos du compte Instagram |
| `instagram_content_publish` | Publier des médias sur Instagram |
| `pages_read_engagement` | Lire les données de la Page liée |
| `pages_manage_posts` | Publier des posts sur la Page Facebook |
| `pages_show_list` | Lister les Pages administrées — **obligatoire pour `/me/accounts`** |

> Ces permissions ne nécessitent pas l'App Review de Meta si l'application est utilisée uniquement par son propriétaire (mode développement suffisant pour usage personnel). **Limite du mode développement :** seuls 5 comptes maximum (propriétaires, administrateurs, développeurs, testeurs) peuvent interagir avec l'application en mode développement. Le compte Instagram cible doit être ajouté en tant que **Testeur** dans "Rôles" de l'App Meta Dashboard — sinon les publications échouent silencieusement (code `200` retourné mais le post n'apparaît pas). Si le compte n'est pas dans cette liste → ajouter via le Dashboard avant tout test.

> **Comptes Créateur vs Business (API v18+) :** pour les comptes de type **Créateur**, le scope `instagram_creator_manage_content` peut être requis en complément de `instagram_content_publish`. Si la création du container retourne l'erreur code `32` ou `10` (permission manquante) alors que tous les scopes ci-dessus sont accordés, vérifier le type du compte Instagram et consulter la documentation Meta à jour — les scopes exacts varient selon le type de compte et la version de l'API.

---

## [IG-3] Publication d'un post Instagram

### [IG-3.1] Étape 1 — Créer le container

```bash
POST https://graph.facebook.com/v21.0/{IG_USER_ID}/media
Content-Type: application/json

{
  "image_url": "https://monvps.com:8765/images/abc123.jpg",
  "caption": "Il y a 10 ans, le 21 mars 2016 : ...\n\n#histoire #onthisday",
  "access_token": "{LONG_LIVED_TOKEN}"
}

# Réponse :
# {"id": "17889615814769990"}  ← creation_id
```

**Contraintes sur `image_url`** : HTTPS, accessible publiquement, JPEG ou PNG, 320×320–1440×1800 px, ratio 0.8–1.91, taille max 8 Mo.

> **Ratio 4:5 (borne inférieure) :** le ratio 0.8 est la **borne inférieure stricte** du range accepté par Meta (0.8–1.91). Pour éviter tout rejet si Meta traite cette borne comme exclusive, une image `1080×1349` donne un ratio ≈ 0.800741…, légèrement supérieur à 0.8. En pratique, `1080×1350` est accepté sans problème — documenter comme "accepté mais à surveiller" si des rejets sont observés.

### [IG-3.2] Étape 2 — Publier le container

```bash
POST https://graph.facebook.com/v21.0/{IG_USER_ID}/media_publish
Content-Type: application/json

{
  "creation_id": "17889615814769990",
  "access_token": "{LONG_LIVED_TOKEN}"
}

# Réponse :
# {"id": "17896129349180833"}  ← instagram_post_id (à stocker en DB)
```

> Si la publication échoue avec code `9007` ("not finished processing"), attendre 5 secondes et retenter.

### [IG-3.3] Vérification statut du container

```bash
GET https://graph.facebook.com/v21.0/{CREATION_ID}?fields=status_code&access_token={TOKEN}
# → {"status_code": "FINISHED" | "IN_PROGRESS" | "ERROR" | "EXPIRED", "id": "..."}
```

**Statuts :** `FINISHED` = prêt à publier, `IN_PROGRESS` = traitement en cours, `ERROR` / `EXPIRED` = container inutilisable, recréer. **Durée de vie :** un container non publié expire automatiquement après **24 heures** (statut `EXPIRED`) — voir [IG-7] pour les conséquences. **Container `IN_PROGRESS` bloqué :** un container resté `IN_PROGRESS` pendant > 2 minutes est probablement bloqué (image inaccessible à l'URL fournie, format non supporté, etc.) — `_wait_for_container_ready` atteindra `max_wait` et lèvera `PublisherError` avec message "Container Meta bloqué — statut IN_PROGRESS après 60s".

### [IG-3.4] Contrat `InstagramPublisher` (`publisher/instagram.py`)

```python
class InstagramPublisher:
    def __init__(self, ig_user_id: str, token_manager: TokenManager, api_version: str = "v21.0") -> None: ...
    async def publish(self, post: Post, image_url: str, caption: str, session: AsyncSession) -> str: ...
    async def _get_or_create_container(self, post: Post, image_url: str, caption: str, access_token: str, session: AsyncSession) -> str: ...
    async def _wait_for_container_ready(self, creation_id: str, token: str, max_wait: int = 30) -> None: ...
    # max_wait : nombre maximum d'itérations, intervalle de polling = 2s → attente max = 60s.
    # Note : le code d'erreur 9007 sur _publish_container (section IG-3.2) est un symptôme distinct :
    # c'est une réponse HTTP de l'endpoint /media_publish si le container n'est pas encore FINISHED.
    # _wait_for_container_ready le prévient en attendant FINISHED avant d'appeler _publish_container.
    # L'intervalle de 5s de la section IG-3.2 ne s'applique plus — _wait garantit l'état FINISHED.
    # Lève PublisherError si status_code == "ERROR" ou "EXPIRED".
    # Appel systématique avant _publish_container — même si _get_container_status retourne "FINISHED"
    # au moment de l'appel, le premier poll peut retourner "IN_PROGRESS" (Meta est asynchrone).
    # Articulation avec _publish_container : flux = _get_or_create_container
    #   → _wait_for_container_ready (poll jusqu'à FINISHED ou timeout)
    #   → _publish_container (un seul appel, pas de retry 9007 ici — le wait garantit FINISHED)
    # Si _publish_container reçoit malgré tout le code 9007 (race condition très rare),
    # lever PublisherError sans retry (l'upload sera retente au prochain /retry).
    async def _create_media_container(self, ig_user_id: str, image_url: str, caption: str, access_token: str) -> str: ...
    async def _get_container_status(self, creation_id: str, access_token: str) -> str: ...
    async def _publish_container(self, ig_user_id: str, creation_id: str, access_token: str) -> str: ...
```

`publish` : retourne `instagram_post_id`. Résistant aux crashs — `ig_container_id` est persisté en DB immédiatement après `_create_media_container`, avant `_publish_container`.

`_get_or_create_container` : réutilise `post.ig_container_id` si le container est encore valide (`FINISHED` ou `IN_PROGRESS`), sinon recrée. Commit du `creation_id` immédiatement après création.

> **Container `IN_PROGRESS` bloqué :** si `post.ig_container_id` est non NULL mais que le container est en statut `IN_PROGRESS` depuis > 2 minutes, il est probablement bloqué. `_wait_for_container_ready` atteindra `max_wait` et lèvera `PublisherError`. Dans ce cas, `_get_or_create_container` doit forcer la recréation : effacer `post.ig_container_id`, committer, puis appeler `_create_media_container` avec la même `image_url`.

> **Guard `image_path NULL` :** si `post.image_path is None` (image nettoyée par `job_cleanup`), ne pas tenter la publication — lever `PublisherError("image_path NULL — image nettoyée par job_cleanup. Lancer /retry pour re-générer.")`. `recover_pending_posts` doit vérifier ce cas et notifier l'utilisateur de lancer `/retry`.

**Gestion des erreurs HTTP :** extraire le corps JSON **avant** `raise_for_status()`. Si `"error"` présent dans le JSON, lever `PublisherError` avec le message Meta détaillé. Les erreurs HTTP 400 de Meta contiennent un JSON `{"error": {"code": N, "message": "..."}}` plus utile que le message générique de `HTTPStatusError`.

**Codes d'erreur Meta Graph critiques à distinguer :**

| Code | Signification | Retriable |
|------|--------------|-----------|
| `190` | Token invalide ou révoqué | Non — `TokenExpiredError` |
| `368` | Compte suspendu / contenu spam — peut apparaître à **l'étape container** (`/media`) comme à l'étape **publish** (`/media_publish`) | Non — `PublisherError` définitive |
| `9007` | Container pas encore finalisé ("not finished processing") | Oui — attendre 2s et retenter via `_wait_for_container_ready` |
| `4` | Rate limit API global (hors quota journalier) | Oui — backoff exponentiel |
| `100` | Paramètre invalide (ex: image_url inatteignable, ratio hors plage) | Non — `PublisherError` définitive |
| `32` / `10` | Permission manquante | Non — vérifier les scopes OAuth |

---

## [FB-1] Publication sur la Page Facebook

### [FB-1.1] Page Access Token

Le **Page Access Token long durée ne possède pas de date d'expiration** tant que l'utilisateur n'a pas révoqué l'accès et que le **User Access Token parent reste valide**.

> **Dépendance au User Token :** si le User Access Token (`token_kind='user_long'`) expire sans renouvellement, le Page Token est automatiquement invalidé — les publications Facebook échouent avec code `190`. Voir [IG-7] — "Page Access Token et expiration".

```bash
curl "https://graph.facebook.com/v21.0/{PAGE_ID}?fields=access_token&access_token={LONG_LIVED_USER_TOKEN}"
# → {"access_token": "PAGE_TOKEN_PERMANENT", "id": "123456789"}
```

Stocké dans `meta_tokens` avec `token_kind='page'` (voir DATABASE.md).

### [FB-1.2] Publier une photo sur la Page

```bash
POST https://graph.facebook.com/v21.0/{PAGE_ID}/photos
Content-Type: application/json

{
  "url": "https://monvps.com:8765/images/abc123.jpg",
  "caption": "Il y a 10 ans...",
  "access_token": "{PAGE_ACCESS_TOKEN}"
}

# Réponse :
# {"post_id": "123456789_987654321", "id": "987654321"}
# → Stocker post_id en DB comme facebook_post_id
```

> **`post_id` vs `id` :** `post_id` (`PAGE_ID_PHOTO_ID`) est l'identifiant du post de la Page, visible dans les URL Facebook. `id` est l'identifiant interne de la photo. `post_id` peut être **absent** si la photo est publiée sans légende (`caption`) — dans ce cas, utiliser `id` comme `facebook_post_id`. Utiliser `data.get("post_id") or data.get("id")` — ne jamais accéder avec `data["post_id"]` directement.

**L'URL de l'image est la même que pour Instagram.** Un seul upload suffît pour les deux plateformes.

### [FB-1.3] Contrat `FacebookPublisher` (`publisher/facebook.py`)

```python
class FacebookPublisher:
    def __init__(self, page_id: str, token_manager: TokenManager, api_version: str = "v21.0") -> None: ...
    async def publish(self, post: Post, image_url: str, caption: str, session: AsyncSession) -> str: ...
```

`publish` : utilise `get_valid_token(session, token_kind='page')`. Retourne `facebook_post_id`. Extrait le corps JSON **avant** `raise_for_status()` — si `"error"` dans le JSON, lève `PublisherError` avec le message Meta.

---

## [IG-4] Publication parallèle (`publisher/__init__.py`)

```python
async def publish_to_all_platforms(
    post: Post,
    image_url: str,
    ig_publisher: InstagramPublisher | None,
    fb_publisher: FacebookPublisher | None,
    session: AsyncSession,  # session du contexte appelant — utilisée UNIQUEMENT pour les
                             # opérations sur post (status, error_message, published_count)
                             # Les publishers créent leurs propres sessions internes pour leur
                             # publication respective — ne pas utiliser session dans asyncio.gather.
    caption: str | None = None,
) -> dict: ...
```

Retourne `{"instagram": instagram_post_id | None, "facebook": facebook_post_id | None}`. `None` = plateforme désactivée ou échec.

Voir ARCHITECTURE.md — "Bot → Publisher" pour le contrat complet.

Points clés :
- `post.status = 'publishing'` avant les appels — crash-safe (recover_pending_posts remet à `approved`)
- Chaque publisher tourne dans sa **propre session** (`async with get_session()`) — ne pas partager `session` entre les deux publishers dans `asyncio.gather`
- `InstagramPublisher` et `FacebookPublisher` partagent **la même instance** de `TokenManager` (singleton créé dans `publisher/__init__.py` et passé aux deux publishers depuis `publish_to_all_platforms`). Le `asyncio.Lock` interne de `TokenManager` (`self._lock`) garantit qu'aucun double refresh ne survient lors d'appels `asyncio.gather` simultanés.
- `asyncio.gather(ig_task, fb_task, return_exceptions=True)` — un échec sur une plateforme ne bloque pas l'autre. Chaque exception est catchée et mappée vers `post.instagram_error` / `post.facebook_error` selon la plateforme.
- `post.status = 'published'` si au moins une plateforme réussit ; `'error'` si les deux échouent
- **Si les deux plateformes sont désactivées** (`instagram.enabled: false` ET `facebook.enabled: false`) : retourne `{"instagram": None, "facebook": None}` et le post est marqué `published` (les deux `_post_id` restent NULL). Ce comportement est intentionnel : la publication Telegram a validé le contenu, l'absence de plateforme active n'est pas une erreur. L'utilisateur est notifié "Publié (aucune plateforme active)".
- **Compteur journalier** : `check_and_increment_daily_count` n'est incrémenté que si Instagram est activé (`instagram.enabled: true`). La limite de 25 posts/jour est une limite Instagram — Facebook Pages n'impose pas de limite équivalente.
- Après succès : incrémente `published_count` sur l'event/article source (`last_used_at` est mis à jour à la **génération** dans `generate_post`, pas ici — voir DATABASE.md section "Requête de sélection des candidats")

---

## [IG-5] Publication Stories (v2 — SPEC-7)

Implémenté dans `ancnouv/publisher/instagram.py` (`publish_story`) et `ancnouv/publisher/facebook.py` (`publish_story`). Déclenché depuis `publisher/__init__.py` (`_publish_stories`) juste après la publication feed, si `stories.enabled: true` et `story_image_url` disponible.

### [IG-F5] Instagram Stories

**Flux :** création container → polling statut → publication. Identique au post feed ([IG-3]) à deux différences près : `media_type=STORIES` et pas de `caption`.

**Étape 1 — Création du container Story :**

```
POST https://graph.facebook.com/{api_version}/{IG_USER_ID}/media
Content-Type: application/json

{
  "image_url": "<URL publique de l'image 1080×1920>",
  "media_type": "STORIES",
  "access_token": "<USER_ACCESS_TOKEN>"
}
```

Réponse attendue :
```json
{ "id": "<creation_id>" }
```

> **Pas de champ `caption`** — ignoré pour les Stories. Les hashtags ne sont pas supportés via l'API Story.

**Étape 2 — Polling statut du container :** identique à [IG-3.3] (`status_code=FINISHED`).

**Étape 3 — Publication :**

```
POST https://graph.facebook.com/{api_version}/{IG_USER_ID}/media_publish
Content-Type: application/json

{
  "creation_id": "<creation_id>",
  "access_token": "<USER_ACCESS_TOKEN>"
}
```

Réponse attendue :
```json
{ "id": "<story_media_id>" }
```

**Spécifications de l'image :**

| Propriété | Valeur |
|-----------|--------|
| Dimensions | 1080 × 1920 px |
| Ratio | 9:16 |
| Format | JPEG |
| Zones de sécurité | 270 px haut, 400 px bas (UI Instagram) |
| Durée de vie | 24 heures |
| Token requis | USER_ACCESS_TOKEN (`user_long`) |

**Erreurs connues :**

| Code | Signification | Comportement |
|------|--------------|--------------|
| 190 | Token invalide ou révoqué | `TokenExpiredError` |
| 9007 | Container pas encore `FINISHED` (race condition) | `PublisherError` |
| Timeout polling | Container bloqué en `IN_PROGRESS` > 60s | `PublisherError` |

**Non-bloquant [RF-7.3.6] :** un échec Story ne modifie pas `post.status`. L'erreur est loggée en `WARNING` et la publication feed est conservée.

---

### [IG-F5B] Facebook Stories (photo)

**Endpoint unique** — pas de flux container/publish comme Instagram :

```
POST https://graph.facebook.com/{api_version}/{PAGE_ID}/photo_stories
Content-Type: application/json

{
  "url": "<URL publique de l'image 1080×1920>",
  "access_token": "<PAGE_ACCESS_TOKEN>"
}
```

Réponse attendue :
```json
{ "post_id": "<story_post_id>" }
```

> Fallback : si `post_id` absent, utilise `id`. Si aucun des deux n'est présent, `PublisherError` est levée.

**Différences vs Instagram Stories :**

| | Instagram | Facebook |
|--|-----------|----------|
| Endpoint | `/{IG_USER_ID}/media` + `media_publish` | `/{PAGE_ID}/photo_stories` |
| Flux | Container → polling → publish (3 appels) | Direct (1 appel) |
| Token | `user_long` | `page` |
| Champ image | `image_url` | `url` |

**Non-bloquant [RF-7.3.6] :** même comportement qu'Instagram Stories.

---

### [IG-F5C] Orchestration (`_publish_stories`)

```python
# publisher/__init__.py
async def _publish_stories(post, story_image_url, ig_publisher, fb_publisher, session):
    ...
```

Les deux Stories (IG + FB) sont lancées en parallèle via `asyncio.gather(*tasks, return_exceptions=True)`. Les exceptions sont loggées individuellement sans interrompre l'autre plateforme. Le premier `story_post_id` disponible (IG prioritaire) est persisté dans `post.story_post_id`.

**Chemin image :** `data/images/{uuid}_story.jpg` (même UUID que le post feed `{uuid}.jpg`). Même politique de rétention que les images feed (`image_retention_days`, nettoyage par JOB-6).

---

## [IG-5B] Hébergement de l'image (URL publique)

### Backend `local` : serveur HTTP embarqué (VPS)

Sur un VPS avec IP publique, l'application embarque un serveur HTTP statique via `aiohttp`.

```python
async def start_local_image_server(images_dir: Path, port: int) -> web.AppRunner: ...
async def run_image_server(port: int = 8765, token: str = "") -> int: ...
```

`start_local_image_server` : sert `data/images/` sous `/images/`. Appelé dans `main_async()` si `backend=local`, **avant** `recover_pending_posts`.

> **[IG-5A] Race condition au démarrage :** entre `start_local_image_server()` et le premier appel Meta, le serveur aiohttp peut ne pas être encore prêt à accepter des connexions. `start_local_image_server` retourne un `web.AppRunner` — attendre que le runner soit démarré (`await runner.setup()` puis `await web.TCPSite(runner, ...).start()`) avant d'appeler `recover_pending_posts`. Ne pas se contenter d'appeler `start_local_image_server` et passer immédiatement à la suite.

`run_image_server` : sert les images ET expose `POST /images/upload` (authentifié par Bearer token). Utilisé par la commande `images-server` (container `ancnouv-images` séparé). Le token est lu depuis `IMAGE_SERVER_TOKEN` (variable d'environnement) — pas depuis `config.yml`. **`IMAGE_SERVER_TOKEN`** doit être défini dans `.env` sur le VPS hébergeant `ancnouv-images`, ET dans `.env` sur la machine principale (utilisé comme `config.image_server_token` dans les requêtes d'upload). Voir CONFIGURATION.md — champ `image_server_token` (racine de Config).

> **HTTPS obligatoire :** Le serveur aiohttp tourne en HTTP. Il doit être derrière nginx (terminaison TLS) pour que Meta accepte les URLs d'images. Sans nginx, le backend `local` est inutilisable. Voir DEPLOYMENT.md.

**Sécurité du `handle_upload` :**
- Vérifier `Authorization: Bearer {token}` — retourner 401 si absent ou invalide
- `field.filename` peut être `None` → 400 si absent
- Extraire `Path(field.filename).name` pour éliminer les path traversal (`../`, chemins absolus)
- Ne pas écrire directement `images_dir / field.filename`

**Configuration `config.yml` :**
```yaml
image_hosting:
  backend: local
  public_base_url: "https://VOTRE-DOMAINE-OU-IP:8765"  # sans slash final, HTTPS via nginx
  local_port: 8765
```

### Backend `remote` : upload vers VPS distant (RPi/NAS)

```python
async def upload_to_remote(image_path: Path, config: Config) -> str: ...
```

POST multipart vers `config.image_hosting.remote_upload_url`. Header `Authorization: Bearer {config.image_server_token}` — `image_server_token` est un **champ racine** de `Config` (pas `config.image_hosting.token`). Voir CONFIGURATION.md — champ `image_server_token` (section racine de Config). Retry x3 interne avec backoff (1s, 2s, 4s) pour erreurs 5xx et timeouts. Erreurs 4xx (401, 413) : non-retriables, `ImageHostingError` immédiate. Réponse succès : `{"filename": "abc123.jpg"}`. URL retournée : `f"{config.image_hosting.public_base_url}/images/{filename}"`.

**Race condition upload/disponibilité :** après `upload_to_remote()`, l'image peut ne pas être immédiatement accessible via `public_base_url`. `upload_to_remote` attend un `{"filename": ...}` de succès du serveur avant de retourner — à ce stade l'image est écrite sur disque côté serveur. Il n'y a pas de délai supplémentaire requis. Si Meta retourne l'erreur `100` ("URL inatteignable") sur le premier essai, c'est un problème de configuration nginx (URL, port, TLS) — pas une race condition.

**Configuration `config.yml` (RPi) :**
```yaml
image_hosting:
  backend: remote
  public_base_url: "https://monvps.com:8765"
  remote_upload_url: "https://monvps.com:8765/images/upload"
```

```bash
# .env sur le RPi (même valeur que sur le VPS)
IMAGE_SERVER_TOKEN=<token 32+ caractères>
```

---

## [IG-6] Commande `auth meta`

```bash
python -m ancnouv auth meta
```

**Comportement** (implémenté dans `cli/auth.py`, fonction `cmd_auth_meta`) :

1. Construit l'URL d'autorisation OAuth avec tous les scopes (dont `pages_show_list`)
2. L'affiche dans le terminal
3. Démarre un serveur HTTP temporaire sur `localhost:8080` pour capturer le callback OAuth automatiquement — l'utilisateur n'a pas à saisir de code manuellement. Cas d'erreur du serveur :
   - **Port déjà occupé** : `OSError: [Errno 98] Address already in use` — l'erreur est propagée avec un message explicite ("Port 8080 déjà utilisé — arrêter le processus occupant ce port").
   - **Timeout (utilisateur ne complète pas le flux)** : délai d'attente de 120 secondes, puis `TimeoutError` levée avec message "Délai d'authentification dépassé".
   - **`?error=access_denied`** dans le callback : l'utilisateur a refusé l'accès — lever une exception explicite avec le message Meta retourné dans `?error_description=...`.
4. Attend la redirection sur `http://localhost:8080/callback?code=...`
5. Arrête le serveur temporaire après réception du code (succès ou erreur)
6. Échange le code contre un token court, puis contre un token long
7. Appelle `GET /me/accounts` (avec `pages_show_list`) pour récupérer les Pages administrées — si plusieurs Pages sont retournées, affiche la liste numérotée et demande à l'utilisateur de saisir le numéro correspondant (sélection interactive via `input()`)
8. Récupère le Page Access Token permanent et l'IG User ID via deux appels :
   - Page Access Token : `GET /v21.0/{PAGE_ID}?fields=access_token&access_token={LONG_LIVED_USER_TOKEN}`
   - IG User ID : `GET /v21.0/{PAGE_ID}?fields=instagram_business_account&access_token={PAGE_TOKEN}`
   (Ces deux champs ne sont PAS retournés par `/me/accounts` — des appels séparés sont nécessaires.)
9. Stocke les tokens en DB via UPSERT atomique : `INSERT INTO meta_tokens ... ON CONFLICT(token_kind) DO UPDATE SET ...` — ne pas utiliser DELETE+INSERT (non atomique, risque de perte de token en cas de crash entre les deux)
10. Affiche la date d'expiration du token utilisateur

**Idempotence par UPSERT :** `cmd_auth_meta` est **idempotente par design** grâce à la contrainte `UNIQUE(token_kind)` sur `meta_tokens`. L'UPSERT (`ON CONFLICT(token_kind) DO UPDATE`) garantit qu'un enregistrement existant est mis à jour plutôt que créé en doublon. Le Page Access Token est **toujours** régénéré à chaque exécution de `auth meta` (même s'il existe déjà en DB) — l'UPSERT écrase l'ancien token. Cette approche évite de laisser un token page potentiellement invalidé après un refresh manuel du User Token.

**Reprise en cas de crash :** le code OAuth (`?code=...`) est à usage unique (consommé à l'étape 6). Si un crash survient après l'échange OAuth mais avant le commit DB, relancer `auth meta` recommence l'ensemble du flux OAuth depuis le navigateur — le nouveau code généré est valide et l'UPSERT mettra à jour les tokens existants en DB.

**Usage sur VPS distant :** le serveur de callback tourne sur `localhost:8080` du VPS, inaccessible directement depuis un navigateur local. Un tunnel SSH est **obligatoire** :

```bash
# Sur la machine locale (avant d'ouvrir le navigateur)
ssh -L 8080:localhost:8080 user@votre-vps
# Puis ouvrir l'URL OAuth dans le navigateur local → le callback atteint le VPS via le tunnel
```

**Scopes dans `SCOPES` (`cli/auth.py`) :**
```
instagram_basic,instagram_content_publish,instagram_creator_manage_content,pages_read_engagement,pages_manage_posts,pages_show_list
```

> `instagram_creator_manage_content` : requis pour les comptes Instagram de type **Créateur** (optionnel pour les comptes Business). Inclus systématiquement dans `SCOPES` — Meta l'ignore silencieusement si le compte est Business, et son absence bloque la publication sur les comptes Créateur avec code `32` ou `10`.

> **Page Access Token systématiquement régénéré :** le Page Access Token (`token_kind='page'`) est **toujours** recréé à chaque exécution de `auth meta`, même s'il existe déjà en DB. L'UPSERT l'écrase. Cette approche garantit qu'un token page potentiellement invalidé (après un refresh manuel du User Token ou une révocation) est immédiatement remplacé par un token frais.

---

## [IG-7] Rate Limits

| Limite | Valeur | Comportement de l'app |
|--------|--------|----------------------|
| Appels API par heure | 200 par utilisateur | ~4-6 appels par publication en usage normal (voir note ci-dessous) |
| Posts par 24h | `instagram.max_daily_posts` (défaut : 25) | Compteur en DB, blocage si atteint |
| Containers non publiés | Expirent après 24h | Publier immédiatement après création ; si `status_code="EXPIRED"`, recréer le container |

**Scope du compteur 200 appels/heure :** le compteur est par `IG_USER_ID` et couvre **tous** les appels Graph API. Appels réels par cycle de publication :
- Étape 1 : `POST /{IG_USER_ID}/media` (1 appel IG)
- Étape 2 : `GET /{container_id}?fields=status_code` × N polls (variable, ~1-3 appels)
- Étape 3 : `POST /{IG_USER_ID}/media_publish` (1 appel IG)
- Facebook : `POST /{page_id}/photos` (1 appel FB — quota distinct)

Total estimé : **~4-6 appels par publication**. Bien en deçà de la limite de 200. `/debug_token` n'est pas utilisé par `TokenManager` — ne pas l'inclure dans l'estimation. Les appels `job_check_token` et `job_fetch_rss` (RSS, pas Meta) ne sont **pas** comptabilisés dans ce quota. Le rate limit Meta code `4` (global) se distingue du code `32` (permission) — `code 4` est retriable avec backoff.

> **Version API dans les exemples :** la version `v21.0` dans tous les exemples `curl` de ce document est illustrative. La valeur réelle utilisée par l'application est `config.instagram.api_version` (défaut : `"v21.0"`). Ne pas hardcoder `v21.0` dans le code — utiliser `config.instagram.api_version` partout.

**Note sur la limite journalière :** Meta indique une limite de 50 publications par 24h pour les comptes Business. Cette limite est une **fenêtre glissante** de 24h (pas un reset à minuit) — dépasser la limite en fin de journée peut bloquer les publications du lendemain matin. `max_daily_posts: 25` est une valeur conservative qui laisse une marge.

**Page Access Token et expiration :** le Page Access Token est permanent tant que le User Access Token parent est valide. Si le User Token expire sans renouvellement, le Page Token est automatiquement invalidé — les publications Facebook échouent avec code `190`. Ce cas est géré par `job_check_token` qui surveille le User Token (`token_kind='user_long'`) et émet des alertes progressives.

**Container expiré après 24h :** un container Instagram non publié dans les 24h suivant sa création passe à `status_code="EXPIRED"`. `_get_or_create_container` détecte ce statut et recrée le container avec la même `image_url`. Prérequis : l'image doit toujours être accessible à l'URL publique. Si l'image a été supprimée entre-temps (nettoyage par `job_cleanup`), la recréation du container échoue avec code `100` — la publication est abandonnée et le post passe en `'error'`, `error_message` renseigné.

**Politique de migration de la version API :** la version `v21.0` est fixée dans `config.yml` (`instagram.api_version: "v21.0"`). En cas de dépréciation par Meta (annoncée ~12 mois à l'avance via le Changelog developers.facebook.com), mettre à jour `api_version` dans `config.yml` sans redéploiement de code — `InstagramPublisher.__init__` et `FacebookPublisher.__init__` reçoivent `api_version` en paramètre. Un changement de version mineur (ex: `v21.0` → `v22.0`) ne nécessite généralement aucune modification de code, uniquement la config.
