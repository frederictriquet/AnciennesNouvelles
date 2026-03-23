# Bot Telegram

> Référence : [SPEC-3.3]

---

## Rôle

Le bot Telegram est le seul point d'interaction humain de l'application. Il :
1. Soumet les posts générés à validation (image + légende + boutons)
2. Exécute les commandes de pilotage du scheduler
3. Envoie les notifications système (erreurs, alertes token, etc.)

**Mode de fonctionnement :** polling long (`run_polling`) — pas de webhook. Le polling ne nécessite pas d'URL HTTPS publique pour le bot, contrairement aux webhooks. Ce choix est intentionnel pour simplifier le déploiement sur des machines sans certificat valide exposé sur le port 80/443.

---

## Création du bot

1. Ouvrir Telegram → contacter **@BotFather**
2. Envoyer `/newbot`
3. Choisir un nom affiché (ex : `Anciennes Nouvelles Bot`)
4. Choisir un @username (ex : `@ancnouv_bot`)
5. Récupérer le **token** (format : `123456789:AABBccDDeeFF...`)
6. Stocker dans `.env` : `TELEGRAM_BOT_TOKEN=...`

**Récupérer son propre user_id :**
- Contacter @userinfobot sur Telegram → il renvoie votre `id` numérique
- Stocker dans `config.yml` : `telegram.authorized_user_ids: [123456789]`

---

## Sécurité

**Règle absolue** : le bot ne traite aucun message/callback provenant d'un utilisateur non autorisé.

Le décorateur `authorized_only` est appliqué à tous les handlers. Il lit `config.telegram.authorized_user_ids` depuis `context.bot_data["config"]`. Si l'utilisateur n'est pas autorisé, il répond "Accès non autorisé." via `update.effective_message` (fonctionne pour les messages ET les callback queries — `update.message` serait `None` pour un callback). `@functools.wraps` est obligatoire pour préserver `__name__` et `__doc__` utilisés par le routage PTB.

```python
def authorized_only(handler): ...
```

La `config` est injectée dans `bot_data` au démarrage : `bot_app.bot_data["config"] = config` (voir ARCHITECTURE.md — `main_async`).

---

## Commandes disponibles

> **TC-12 — Noms de commandes** : l'API Telegram n'accepte que les underscores dans les noms de commandes (pas les tirets). Les commandes `/retry_ig` et `/retry_fb` utilisent des underscores. Tout document mentionnant `/retry-ig` ou `/retry-fb` est erroné.

| Commande | Description |
|----------|-------------|
| `/start` | Message de bienvenue + état actuel du système |
| `/status` | État détaillé : scheduler (actif/pause), dernier post publié, posts en attente |
| `/pause` | Met le scheduler en pause (plus de génération automatique) |
| `/resume` | Reprend le scheduler |
| `/force` | Génère et soumet immédiatement un post (hors cycle). Bypass `max_pending_posts` — permet d'ajouter une proposition même si la limite est atteinte. Ne bypasse pas la limite journalière Instagram. |
| `/stats` | Statistiques : total publié, rejeté, taux d'approbation, posts/semaine |
| `/pending` | Liste les posts en attente d'approbation |
| `/retry` | Retente la publication du dernier post en statut `error` (les deux plateformes) |
| `/retry_ig` | Retente Instagram uniquement sur le dernier post `published` avec `instagram_error` non NULL |
| `/retry_fb` | Retente Facebook uniquement sur le dernier post `published` avec `facebook_error` non NULL |
| `/help` | Liste des commandes |

### Signatures des handlers de commandes (`bot/handlers.py`)

```python
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def cmd_force(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def cmd_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def cmd_retry_ig(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def cmd_retry_fb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
```

### Comportement des commandes

**`cmd_start`** : envoie un message de bienvenue avec l'état courant du système. Contenu requis [SPEC-RF-3.3.5] : nom du bot, confirmation que l'app est opérationnelle, état du scheduler (`"actif"` / `"en pause"` depuis `scheduler_state.paused`), prochain déclenchement estimé (voir TG-M3 ci-dessous), nombre de posts en attente (`pending_approval` count), commandes disponibles en abrégé. Exemple de sortie :
```
Anciennes Nouvelles démarré ✓
Scheduler : actif | Prochain post : 10h30
Posts en attente : 2
Tapez /help pour la liste des commandes.
```

**`cmd_help`** : envoie la liste des commandes sous forme statique (contenu fixe, pas de requête DB). Affiche chaque commande avec une description courte sur une ligne. Exemple :
```
/status — État détaillé du système
/force  — Générer un post immédiatement
/pause  — Suspendre la génération automatique
/resume — Reprendre la génération automatique
/pending — Posts en attente d'approbation
/stats  — Statistiques
/retry  — Retenter le dernier post en erreur
/retry_ig | /retry_fb — Retry plateforme seule
```

**`cmd_stats`** : lit les données de la DB et affiche un tableau de statistiques. Requêtes SQL :
- `COUNT(*) WHERE status = 'published'` → total publié (depuis le début)
- `COUNT(*) WHERE status = 'rejected'` → total rejeté
- `COUNT(*) WHERE status = 'published' AND published_at >= now() - interval '7 days'` → posts publiés sur les **7 derniers jours calendaires** (fenêtre glissante depuis `datetime.now(UTC) - timedelta(days=7)`)
- Taux d'approbation = `published / (published + rejected)` × 100 (ignorer `skipped` et `expired`). **Guard division par zéro (TG-F13) :** `total = published + rejected` ; si `total == 0` afficher `"N/A"` à la place du taux. Implémentation : `approval_rate = f"{published / total * 100:.0f}%" if total > 0 else "N/A"`

Exemple de sortie :
```
Statistiques Anciennes Nouvelles
━━━━━━━━━━━━━━━━━━━━
Total publié : 42
Total rejeté : 8
Posts (7 derniers jours) : 5
Taux d'approbation : 84%
```

**`cmd_status`** : lit `scheduler_state.paused`, compte les posts `pending_approval`, récupère le dernier post `published` (trié par `published_at DESC`), lit le compteur journalier depuis `scheduler_state.daily_post_count`, lit `scheduler_state.token_alert_level`. Exemple de sortie :
```
Anciennes Nouvelles — État
━━━━━━━━━━━━━━━━━━━━
Scheduler : actif | Prochain post : 10h30
Posts aujourd'hui : 2 / 25
Posts en attente : 1
Dernier publié : 21/03/2026 à 08:15
Token Meta : normal (expire dans 45 jours)
```
Valeurs de `token_alert_level` affichées : `"normal"` → "normal", `"30j"` → "attention — 30 jours", `"14j"` → "avertissement — 14 jours", `"7j"` → "alerte — 7 jours", `"3j"` → "CRITIQUE — 3 jours", `"1j"` → "EXPIRÉ DEMAIN", `"expired"` → "EXPIRÉ — publications suspendues". `token_alert_level` est écrite par `job_check_token` dans `scheduler/jobs.py`. L'état du scheduler (actif/pause, prochain déclenchement) est lu depuis `scheduler_state` en DB — pas depuis l'instance APScheduler en mémoire, pour que `/status` fonctionne même si le scheduler n'est pas démarré.

> **[TRANSVERSAL-4] Rôle de `token_alert_level` ici :** `scheduler_state.token_alert_level` est la chaîne lisible exposée par `/status` — c'est son unique rôle dans ce module. Ne pas confondre avec `MetaToken.last_alert_days_threshold` qui est le mécanisme anti-doublon d'alerte dans la table `meta_tokens` (voir SCHEDULER.md pour ce mécanisme distinct).

> **Mécanisme anti-répétition des alertes token (TG-M9) :** `job_check_token` tourne quotidiennement (JOB-5). Sans déduplication, il enverrait l'alerte "30 jours" tous les jours pendant 16 jours. La déduplication est assurée par `scheduler_state.token_alert_level` en DB : avant d'envoyer une alerte, `job_check_token` compare le niveau courant (calculé via `get_alert_threshold(remaining)`) avec la valeur stockée. Si identique, pas d'envoi. Si supérieur (ex: `"30j"` → `"14j"`), envoyer l'alerte et mettre à jour `token_alert_level`. Réinitialiser à `"normal"` après renouvellement réussi. Ce mécanisme garantit **une seule notification par seuil** même si le job tourne plusieurs fois par jour.

**`cmd_pending`** : liste les posts `pending_approval` triés par `created_at ASC`. Pour chaque post, affiche l'ID, l'âge (calculé via `int(delta.total_seconds()) // 3600` — **ne pas utiliser `delta.seconds`** qui retourne la partie secondes du delta, pas le total : un post de 25h afficherait "1h") et un extrait de 50 caractères de la légende. **Timezone d'affichage :** `created_at` est stocké en UTC (naive) — calculer l'âge en secondes écoulées depuis `datetime.utcnow()`, pas depuis `datetime.now()`. **Lien Telegram direct (TG-F7) :** quand `max_pending_posts > 1`, ajouter pour chaque post le lien vers le message d'approbation si disponible. Pour chaque `(user_id, message_id)` dans `post.telegram_message_ids`, construire `t.me/c/{abs(int(user_id))}/{message_id}` (les chat_ids négatifs pour les groupes — `abs()` obligatoire). Si `post.telegram_message_ids` est vide pour un post, afficher `"(message non retrouvé)"` à la place du lien. Exemple de sortie :
```
Posts en attente (2) :
• #42 — il y a 3h — "Il y a 10 ans, le 21 mars 2016 : l..." — t.me/c/123456789/42
• #43 — il y a 0h — "L'attentat de Nice : chronologie d..." — (message non retrouvé)
```
Si aucun post : répondre "Aucun post en attente."

**`cmd_pause`** / **`cmd_resume`** : appellent `set_scheduler_state(session, "paused", "true"/"false")` (voir DATABASE.md — `scheduler_state`).

**`cmd_force`** : appelle `generate_post(session)` directement puis `send_approval_request`. Le bypass de `max_pending_posts` est une conséquence directe de ce court-circuit : JOB-3 vérifie le compteur avant d'appeler `generate_post`, mais `cmd_force` appelle `generate_post` sans passer par cette vérification — il n'y a pas de flag "force" explicite. La limite journalière Instagram n'est **pas** contournée (vérifiée lors de l'approbation dans `handle_approve`). Utilise des imports inline (`from ancnouv.generator import generate_post`) pour éviter les imports circulaires. **Si `generate_post` retourne `None`** : répondre à l'utilisateur "Aucun événement disponible — base de données épuisée ou tous les candidats exclus par les filtres." via `update.message.reply_text(...)`. Ne pas appeler `send_approval_request`.

**`cmd_retry`** : gère uniquement les posts `status='error'` — les posts `status='queued'` ne sont **pas** traités par `/retry` en v1 (voir TG-F1). Utilise un verrou optimiste pour éviter la double invocation : `UPDATE posts SET status='publishing', retry_count=retry_count+1 WHERE id=:id AND status='error'`. Si 0 lignes affectées → un retry parallèle a déjà pris le verrou, retourner silencieusement. Sinon : rafraîchir l'ORM (`session.refresh`), annuler `error_message`, commit, puis appeler `_publish_approved_post`. Pas de `SELECT FOR UPDATE` en SQLite (non supporté) — le `UPDATE` conditionnel est le seul mécanisme d'atomicité disponible. **Idempotence de l'upload (TG-F6) :** si un crash s'est produit après `upload_image` mais avant le commit, `post.image_public_url` peut être partiellement renseigné. `_publish_approved_post` vérifie ce champ avant d'appeler `upload_image` — si déjà renseigné, l'upload est ignoré et l'URL existante est réutilisée (voir `_publish_approved_post` ci-dessous).

**`cmd_retry_ig`** : sélectionne le post `published` avec `instagram_error IS NOT NULL` le plus récent, incrémente `retry_count`, annule `instagram_error`, appelle `_retry_single_platform(post, "instagram", ...)`. Si aucun post éligible trouvé : répondre `"Aucun post avec erreur Instagram en attente de retry."` (TG-F16).

**`cmd_retry_fb`** : idem pour `facebook_error`, appelle `_retry_single_platform(post, "facebook", ...)`. Si aucun post éligible trouvé : répondre `"Aucun post avec erreur Facebook en attente de retry."` (TG-F16).

**`retry_count`** : compteur purement informatif (pas de limite imposée), incrémenté par chaque handler `/retry*` avant de re-déclencher la publication.

---

## Workflow d'approbation

### Format du message de validation

**Mode A (Wikipedia) :**
```
PROPOSITION DE POST

Il y a 10 ans, le 21 mars 2016 :
[texte de l'événement]

Source : Wikipedia (fr)
─────────────────────────
Généré le 21/03/2026 à 10:00
```

**Mode B (RSS) :**
```
PROPOSITION DE POST (Actualité RSS)

[titre de l'article]
[résumé tronqué à 200 chars]

Source : Le Monde
Publié le : 21/09/2025
─────────────────────────
Généré le 21/03/2026 à 10:00
```

Accompagné du fichier image en prévisualisation (`send_photo`). Si `post.image_path` est absent : `send_message` (texte seul, sans photo).

> **Limite caption `send_photo` (TG-F11) :** Telegram impose 1024 caractères maximum pour le paramètre `caption` de `send_photo` (contre 4096 pour `send_message`). Tronquer la légende à 1024 chars **en mémoire** uniquement pour l'appel `send_photo` Telegram. La valeur stockée en DB dans `post.caption` reste la légende complète non tronquée. La publication Instagram (qui supporte 2200 chars) utilise la valeur DB — pas la valeur tronquée. La troncature n'est donc qu'un artefact de l'affichage Telegram, sans impact sur le contenu publié.

> **Image Mode B :** le layout de l'image RSS est identique à Mode A (masthead, border, zones) mais avec des données différentes — zone date : `"ACTUALITÉ RSS"` + date de publication de l'article ; zone texte : titre + résumé ; footer : `f"Source : {article.feed_name}"`. Voir IMAGE_GENERATION.md — section `_generate_image_inner`.

> **Légende Instagram Mode B :** générée par `format_caption_rss(article, config)` — titre + résumé tronqué + attribution + hashtags. Voir IMAGE_GENERATION.md — section `format_caption_rss`.

### Clavier inline

```
[ Publier ]  [ Rejeter ]
[ Autre événement ]
[ Modifier la légende ]
```

Les boutons utilisent des **callback_data** structurées :
```
approve:{post_id}
reject:{post_id}
skip:{post_id}
edit:{post_id}
```

Le `post_id` est toujours encodé dans le `callback_data` — ne jamais stocker l'ID "courant" dans un état global, particulièrement avec `max_pending_posts > 1` où plusieurs messages peuvent coexister.

---

## Machine à états des interactions

```
[message reçu avec boutons]
         |
    +----+----+----+
    |         |    |
 approve    reject skip
    |         |    |
    v         v    v
[limite    [marquer   [marquer skipped]
 journa-    rejeté]   [générer nouveau]
 lière ?]              |
    |                  +—→ generate_post retourne None ?
    |                  |        Oui → notifier "Aucun événement"
    |                  |        Non → [envoyer nouveau post]
    v
    Oui → status='queued'
    Non → [publier]
           [status='published' ou 'error']

         edit
           |
           v
    [ConversationHandler]
    Bot: "Envoyez la nouvelle légende :"
           |
    [utilisateur envoie texte]
           |
           v
    [mettre à jour caption en DB]
    [renvoyer le post avec nouvelle légende]
    [retour aux boutons Publier Rejeter Autre]
```

---

## Callbacks d'approbation (`bot/handlers.py`)

```python
async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def handle_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
async def _publish_approved_post(post: Post, session: AsyncSession, bot: Bot, config: Config) -> None: ...
async def _retry_single_platform(post: Post, platform: str, session: AsyncSession, bot: Bot, config: Config) -> None: ...
```

### `handle_approve` — flux complet

1. Extraire `post_id` du `callback_data` (`"approve:42"` → `42`).
2. Appeler `update.callback_query.answer()` immédiatement pour acquitter le callback côté Telegram (évite le spinner infini sur le bouton).
3. Charger le post via `session.get(Post, post_id)`. Si introuvable ou `status != "pending_approval"`, éditer le message et retourner. Verrou optimiste : `UPDATE posts SET status='approved' WHERE id=:id AND status='pending_approval'` via `session.execute(text(...))` — si 0 lignes affectées, un autre admin a déjà approuvé, retourner silencieusement. Appeler `await session.refresh(post)` immédiatement après l'UPDATE SQL brut pour synchroniser l'état ORM (sans refresh, l'objet conserve `status='pending_approval'` en mémoire et le commit suivant l'écraserait).

   **Protection contre les double-clics (TG-F5) :** deux mécanismes distincts s'appliquent ici. (1) Le verrou optimiste de l'étape 3 (`UPDATE ... WHERE status='pending_approval'`) garantit qu'un seul admin peut approuver un post : si deux admins cliquent simultanément, l'un obtient 1 ligne affectée et continue, l'autre obtient 0 lignes et retourne silencieusement. (2) `check_and_increment_daily_count` est protégé par `BEGIN EXCLUSIVE` (voir SCHEDULER.md) — deux approbations simultanées ne peuvent pas dépasser `max_daily_posts` même dans une fenêtre de race condition. Ces deux protections sont indépendantes et complémentaires.
4. Écrire `approved_at = now(UTC)`, commit. (Ne pas réécrire `status` — déjà `'approved'` via SQL, reflété après refresh.)
5. **Guard `image_path NULL` dans `handle_approve` (fait foi) :** si `post.image_path` est NULL (nettoyé par `job_cleanup`), éditer le message ("image introuvable — post non publiable") et retourner sans appeler `_publish_approved_post`. Ce guard est la **vérification définitive** — `_publish_approved_post` n't effectue aucun guard sur `image_path` et présuppose que le chemin est valide. La responsabilité du guard appartient exclusivement à `handle_approve`.
6. Vérifier la limite journalière via `check_and_increment_daily_count(get_engine(), config.instagram.max_daily_posts)` — importer `get_engine` depuis `ancnouv.scheduler.context` (**pas** `ancnouv.db.session`). Si limite atteinte : écrire `status = "queued"`, commit, **éditer le message uniquement** (pas `notify_all` — éviter la double notification pour l'utilisateur qui a cliqué). Message Telegram à afficher : `"⚠️ Limite journalière atteinte (25 posts). Post mis en file d'attente. En v1, déblocage manuel requis : lancer /retry demain ou exécuter directement UPDATE posts SET status='approved' WHERE status='queued' puis /retry."` Note : `/retry` ne gère que les posts `status='error'` — les posts `queued` ne sont pas pris en charge automatiquement en v1 et nécessitent cette procédure manuelle.
7. Appeler `await _publish_approved_post(post, session, bot, config)` — cette fonction délègue upload + publication + notifications système (`notify_all`). Voir ci-dessous.
8. **Retrait des boutons inline (comportement multi-admins, TG-F10) :** après approbation, `handle_approve` itère sur `post.telegram_message_ids` et édite **tous** les messages de tous les admins (boutons désactivés + message de confirmation). Ne pas éditer uniquement le message de l'admin qui a cliqué. Séquence : pour chaque `(user_id, message_id)` dans `post.telegram_message_ids.items()`, appeler `bot.edit_message_reply_markup(chat_id=int(user_id), message_id=message_id, reply_markup=None)` pour retirer les boutons partout. Puis appeler `update.callback_query.edit_message_text(...)` pour afficher le résultat sur le message de l'admin courant : lire `post.status`, `post.instagram_error`, `post.facebook_error` après retour de `_publish_approved_post`. Afficher Instagram OK/KO, Facebook OK/KO, commandes `/retry_*` si erreur partielle. Les boutons sont **toujours** retirés après approbation chez tous les admins — les admins B, C auraient pu cliquer après l'admin A, mais le verrou optimiste (étape 3) les aurait arrêtés silencieusement. Le retrait visuel des boutons chez tous les admins est le comportement documenté correct.

`check_and_increment_daily_count` et `get_engine` sont importés en inline dans `handle_approve` pour éviter l'import circulaire : `bot/handlers.py → scheduler/jobs.py → bot/notifications.py → bot/handlers.py`.

### `handle_reject`

1. Charger le post, vérifier `status == "pending_approval"`.
2. Appeler `update.callback_query.answer()` immédiatement pour acquitter le callback (Telegram impose un délai max de 10s — ne pas attendre la fin des accès DB).
3. Écrire `status = "rejected"`.
4. Si `post.article_id IS NOT NULL` (Mode B) : écrire `status = "blocked"` sur l'`RssArticle` correspondant — la valeur `'rejected'` violerait le `CHECK (status IN ('available', 'blocked'))` de la table `rss_articles`.
5. Sinon si `post.event_id IS NOT NULL` (Mode A) : écrire `status = "blocked"` sur l'`Event` correspondant.
6. Commit, éditer le message de confirmation.

> **[ARCH-I4]** Si `post.event_id IS NULL` et `post.article_id IS NULL` (cas anormal), la mise à jour ne touche aucune ligne — aucune erreur levée, comportement silencieux attendu.

### `handle_skip`

1. Charger le post, vérifier `status == "pending_approval"`.
2. Appeler `update.callback_query.answer()` pour acquitter le callback.
3. Écrire `status = "skipped"`, commit.
4. Éditer le message ("Post ignoré").
5. Ouvrir une **nouvelle session** via `async with get_session() as new_session:` pour appeler `generate_post(new_session)`. Ne pas réutiliser la session du skip — la session doit être fermée après le commit du skip (durée de vie excessive d'une session AsyncSession — anti-pattern DATABASE.md).
6. Si un post est généré : appeler `send_approval_request`. Sinon : notifier "Aucun autre événement disponible".

> **[SPEC-B2]** La vérification `pending_count >= max_pending_posts` ne s'applique **pas** dans `handle_skip` — la fonctionnalité "générer immédiatement le suivant" contourne cette limite par conception.

### `send_approval_request`

```python
async def send_approval_request(post: Post, bot: Bot, session: AsyncSession, config: Config) -> None: ...
```

Envoie le message de validation (image + légende + boutons inline) à tous les `authorized_user_ids`. Peuple `post.telegram_message_ids` avec `{str(user_id): message_id}` pour chaque envoi réussi.

Comportement d'envoi :
1. Construire le texte du message selon le format Mode A / Mode B (section "Format du message de validation").
2. Pour chaque `user_id` dans `config.telegram.authorized_user_ids` :
   - `send_photo(chat_id=user_id, photo=..., caption=..., reply_markup=...)` si `post.image_path` valide — **tronquer la légende à 1024 chars pour l'appel Telegram uniquement** (voir TG-F11 ci-dessous). La valeur en DB dans `post.caption` reste la légende complète.
   - `send_message(...)` sinon
   - Stocker `message.message_id` dans `telegram_message_ids[str(user_id)]`
   - **Stocker le type de message (TG-F4) :** écrire `context.chat_data["edit_message_type"] = "photo"` (si `send_photo`) ou `"text"` (si `send_message`). `handle_edit_timeout` lit cette valeur pour choisir entre `bot.edit_message_caption` et `bot.edit_message_text`.
3. Commit de `post.telegram_message_ids` après tous les envois.

**Validation `authorized_user_ids` (TG-F9) :** si `config.telegram.authorized_user_ids` est vide à l'entrée de `send_approval_request`, c'est une erreur de configuration — `validate_meta` aurait dû bloquer le démarrage. Comportement défensif : logger `ERROR "authorized_user_ids vide — impossible d'envoyer le post en approbation"`, écrire `post.status = "error"` avec `post.error_message = "No authorized users configured"`, appeler `notify_all` (qui ne fera rien si la liste est vide — safe), commit, et retourner sans lever d'exception.

**Comportement en cas d'échec partiel :** si l'envoi échoue pour un `user_id` parmi plusieurs (Telegram KO pour cet admin), l'exception est catchée, l'entrée dans `telegram_message_ids` n'est pas créée pour ce `user_id`, et l'envoi continue vers les autres admins. Le post n'est **pas** annulé. Après commit, `telegram_message_ids` peut donc être incomplet — `recover_pending_posts` le renverra au redémarrage uniquement si le dict est vide (voir section "Anti-duplication").

> **Conséquence sur `recover_pending_posts` :** si l'envoi a réussi pour au moins un admin, `telegram_message_ids != {}`, et `recover_pending_posts` ne renverra pas le post — les admins ayant manqué l'envoi initial ne recevront jamais le message après redémarrage. Ce comportement est documenté comme acceptable (cas d'admin temporairement inaccessible) — see [TG-M10].

### `_publish_approved_post`

Flux partagé entre `handle_approve` et `cmd_retry`. Séquence :
1. **Upload (TG-F6 — gestion idempotence) :** si `post.image_public_url` est déjà renseigné au moment du retry (upload réussi lors d'un appel précédent, éventuellement avant un crash entre l'upload et le commit), utiliser cette URL existante sans re-uploader. `upload_image` n'est pas idempotente — le même fichier peut générer une URL différente à chaque appel, ce qui créerait deux ressources hébergées distinctes pour la même image. Si `post.image_public_url` est vide : appeler `upload_image(post.image_path, config)` → écrire `post.image_public_url = url`, commit. En cas d'`ImageHostingError` : notifier via `notify_all` et retourner (le post reste `approved` pour retry ultérieur — ne pas changer le statut).
2. **Instancier les publishers :** `TokenManager(config.meta_app_id, config.meta_app_secret, notify_fn)` où `notify_fn` est une coroutine locale `async def _notify(msg: str): await notify_all(bot, config, msg)`. Instancier `InstagramPublisher` (si `config.instagram.enabled`), `FacebookPublisher` (si `config.facebook.enabled`).
3. Appeler `publish_to_all_platforms(post, post.image_public_url, ig_publisher, fb_publisher, session)`.
4. Notifier le résultat via `notify_all` (voir tableau notifications système — "Post publié", erreurs partielles ou totales).

### `_retry_single_platform`

Appelle directement le publisher de la plateforme ciblée (`platform in {"instagram", "facebook"}`).

**Colonnes mises à jour selon le résultat :**

| Cas | Colonnes |
|-----|---------|
| Succès Instagram | `instagram_post_id = <id>`, `instagram_error = None`, commit |
| Succès Facebook | `facebook_post_id = <id>`, `facebook_error = None`, commit |
| Échec Instagram | `instagram_error = <message>`, commit |
| Échec Facebook | `facebook_error = <message>`, commit |

**Statut du post après retry :** `post.status` reste `'published'` dans tous les cas (le post a réussi au moins partiellement lors du premier `publish_to_all_platforms`). `_retry_single_platform` ne modifie jamais `post.status`. Si le retry réussit et que **les deux plateformes** sont maintenant OK (`instagram_error IS NULL` et `facebook_error IS NULL`), `post.status` reste `'published'` — aucune mise à jour de statut nécessaire.

---

## Flux d'édition de légende (`ConversationHandler`)

```python
WAITING_CAPTION = 1

edit_conv_handler: ConversationHandler  # défini dans bot/handlers.py
async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: ...
async def handle_new_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: ...
async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int: ...
```

**Configuration du `ConversationHandler` :**
- `entry_points` : `CallbackQueryHandler(handle_edit, pattern=r"^edit:\d+$")`
- `states` : `{WAITING_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_caption)]}`
- `fallbacks` : `[CommandHandler("cancel", cancel_edit)]`
- `per_user=True, per_chat=False` — une conversation par utilisateur, indépendamment du chat. Plusieurs boutons "Modifier" actifs simultanément (`max_pending_posts > 1`) sont isolés via la clé `chat_data` (voir `handle_edit`). **Comportement multi-appareils (TG-F18) :** avec `per_user=True, per_chat=False`, la conversation est trackée par `user_id` indépendamment du chat. Si un admin utilise deux appareils (deux chats différents mais même `user_id`), la conversation est partagée — une réponse sur le mobile peut continuer une conversation commencée sur le desktop. Ce comportement est intentionnel et acceptable pour un usage single-admin.

> **Pourquoi `per_user=True` et non `per_message=True` :** PTB v21 recommande `per_message=True` uniquement pour les conversations entièrement composées de `CallbackQueryHandler`. Avec un `MessageHandler` dans l'état `WAITING_CAPTION`, PTB ne peut pas router un message entrant vers la bonne conversation par `message_id` — le routing serait cassé pour plusieurs sessions d'édition simultanées.

- `conversation_timeout=300` — conversation automatiquement terminée après 5 min sans réponse. Ajouter un handler de timeout dans `states` : `{ConversationHandler.TIMEOUT: [TypeHandler(Update, handle_edit_timeout)]}`.
- **Relance après timeout ou `/cancel` (TG-F14) :** après timeout ou annulation, un re-clic sur le bouton "Modifier la légende" relance la conversation via l'`entry_point` du `ConversationHandler` — c'est le comportement natif PTB avec `per_user=True, per_chat=False`. Si la conversation précédente n'est pas terminée proprement (ex: timeout non traité), PTB la réinitialise au re-clic sur l'entry_point. L'utilisateur retrouve donc toujours un point d'entrée fonctionnel.

**`handle_edit_timeout`** : lit `message_id = context.chat_data.get("pending_edit_message_id")`. Si `None` (timeout déclenché deux fois ou clé déjà nettoyée) : ignorer silencieusement. Sinon : supprimer `f"edit_{message_id}"` et `pending_edit_message_id` de `chat_data`, **éditer le message pour retirer les boutons ET ajouter le texte** `"(délai d'édition expiré)"` à la fin de la légende — afin que l'utilisateur comprenne pourquoi les boutons ont disparu. **Choix de la méthode d'édition (TG-F4) :** lire `context.chat_data.get("edit_message_type")` — valeur `"photo"` ou `"text"`. Si `"photo"` : appeler `bot.edit_message_caption(...)` ; si `"text"` : appeler `bot.edit_message_text(...)`. `send_approval_request` stocke toujours `"photo"` dans `context.chat_data["edit_message_type"]` car elle envoie une photo quand `image_path` est valide. Ne pas accéder à `update.callback_query` — le handler de timeout reçoit le dernier Update connu mais il peut ne pas avoir de `callback_query`.

**`handle_edit`** : extrait `post_id` du `callback_data`. Stocke dans `context.chat_data` à la fois le `post_id` **et** le `message_id` du message bouton :

```python
message_id = update.callback_query.message.message_id
context.chat_data[f"edit_{message_id}"] = post_id
context.chat_data["pending_edit_message_id"] = message_id  # clé de recherche pour handle_new_caption
```

Appelle `update.callback_query.answer()`. Demande la nouvelle légende.

> **Isolation avec `max_pending_posts > 1` :** `per_user=True` garantit une conversation par utilisateur — une seule édition active simultanément par utilisateur. Si l'utilisateur clique sur "Modifier" d'un post B alors qu'il est en cours de modification du post A, PTB redémarre la conversation : l'état `WAITING_CAPTION` du post A est écrasé et `handle_edit` est appelé pour le post B. Le post A reste inchangé en DB (aucune modification partielle). L'utilisateur revient aux boutons et peut re-cliquer "Modifier" sur le post A si nécessaire. `pending_edit_message_id` est l'entrée en cours ; `f"edit_{message_id}"` permet aussi à `handle_edit_timeout` de nettoyer la bonne clé.

**`handle_new_caption`** : lit `message_id = context.chat_data["pending_edit_message_id"]`, puis `post_id = context.chat_data[f"edit_{message_id}"]`. Dans l'état `WAITING_CAPTION`, le `context` ne contient pas de `callback_query` — `pending_edit_message_id` est le seul moyen de retrouver la clé. Met à jour `post.caption` en DB. **Gestion de l'ancien message Telegram :** appeler `bot.edit_message_reply_markup(chat_id=user_id, message_id=message_id, reply_markup=None)` pour **retirer les boutons** de l'ancien message avant d'envoyer le nouveau — sans cette étape, deux messages avec des boutons "Publier" coexistent pour le même post. Puis réinitialiser `post.telegram_message_ids = {}`, appeler `send_approval_request` (qui renverra un nouveau message avec la légende à jour et repopulera `telegram_message_ids`). Supprimer les deux clés `chat_data` après usage. **Comportement légende (TG-F11) :** écrire en DB la nouvelle légende **complète** telle que saisie par l'utilisateur — pas de troncature en DB. Instagram supporte jusqu'à 2200 chars pour la publication. La troncature à 1024 chars s'applique uniquement à l'appel `send_photo` Telegram (dans `send_approval_request`), pas à la légende stockée ni à la publication Instagram.

**`cancel_edit`** : retourne `ConversationHandler.END` sans modification en DB. Envoie un message de confirmation à l'utilisateur : `"Édition annulée."` via `update.message.reply_text(...)`. Ne pas éditer le message original avec les boutons — les boutons restent actifs.

---

## Gestion du timeout d'approbation (JOB-4)

Voir SCHEDULER.md — section [JOB-4] `job_check_expired`.

**Valeur de `approval_timeout_hours`** : lue depuis `config.scheduler.approval_timeout_hours` (défaut : `48` heures — voir CONFIGURATION.md). Cette valeur est définie dans CONFIGURATION.md et SPEC.md [RF-3.3.3] fait foi. SCHEDULER.md ne redéfinit pas la valeur par défaut.

```python
async def job_check_expired() -> None: ...
```

Comportement :
1. Sélectionner les posts `pending_approval` dont `created_at < now - approval_timeout_hours`.
2. Écrire `status = "expired"`.
3. Désactiver les boutons inline : `post.telegram_message_ids` est un JSON `{str(user_id): message_id}` — itérer sur `.items()` avec guard `if message_id is not None`, appeler `bot.edit_message_reply_markup(chat_id=int(user_id), message_id=message_id, reply_markup=None)`. Ne pas itérer sur `authorized_user_ids` avec lookup : un admin pour lequel l'envoi initial avait échoué n'aurait pas d'entrée dans le dict → `KeyError`. Chaque chat Telegram a son propre espace de numérotation — un `message_id` n'est valide que dans le chat où il a été envoyé. Les exceptions (message supprimé, `MessageNotModified`) sont silenciées.
4. **Notification améliorée (TG-F12) :** inclure un extrait de 100 chars de la légende dans le message de notification pour faciliter l'identification du post même si le message Telegram original a été supprimé. Format : `"⚠️ Post #{post.id} expiré sans validation : \"{post.caption[:100]}...\""` (tronquer à 100 chars, ajouter `"..."` si troncature). Appeler `notify_all` avec ce message enrichi.
5. Commit.

`send_approval_request` peuple `post.telegram_message_ids` au moment de l'envoi initial : pour chaque `user_id` dans `authorized_user_ids`, envoyer le post et stocker le `message_id` retourné dans le dict.

---

## Notifications système

Le bot envoie proactivement des notifications dans ces cas :

| Événement | Message |
|-----------|---------|
| Démarrage | "Anciennes Nouvelles démarré. Scheduler actif. Prochain post : [heure]" (voir note TG-M3 ci-dessous) |
| Mise en pause | "Scheduler mis en pause." |
| Reprise | "Scheduler repris." |
| Post publié | "Publié sur Instagram : [url]" |
| Aucun événement disponible | "Aucun événement disponible pour le [date]. Vérifier la base de données." |
| Token expire dans 30j | "Token Meta : expiration dans 30 jours. Renouvellement automatique prévu à J-7." |
| Token expire dans 14j | "Token Meta : expiration dans 14 jours. Vérifier que le renouvellement automatique fonctionnera." |
| Token expire dans 7j | "Token Meta : expiration dans 7 jours. Tentative de renouvellement automatique en cours..." |
| Token expire dans 3j (refresh échoué) | "Token Meta : expiration dans 3 jours. Renouvellement automatique échoué. Publications actives mais intervention requise. Lancer : `python -m ancnouv auth meta`" |
| Token expire dans 1j (refresh échoué) | "Token Meta : expiration DEMAIN. Action manuelle requise immédiatement. Lancer : `python -m ancnouv auth meta`" |
| Token expiré | "Token Meta expiré. Publications suspendues. Lancer : `python -m ancnouv auth meta`" |
| Token renouvelé automatiquement | "Token Meta renouvelé automatiquement (expire dans 60 jours)." |
| Erreur publication (les deux plateformes) | "Échec publication Instagram + Facebook : [message d'erreur]. Post conservé pour retry." |
| Erreur publication partielle (Instagram KO) | "Facebook publié, Instagram échoué : [erreur]. Utiliser /retry_ig pour retenter." |
| Erreur publication partielle (Facebook KO) | "Instagram publié, Facebook échoué : [erreur]. Utiliser /retry_fb pour retenter." |
| Image hosting échoué | "Upload image échoué après 3 tentatives. Vérifier le service d'hébergement." |
| Limite journalière atteinte | "Limite journalière Instagram atteinte (25 posts). Votre post est en file d'attente — il ne sera pas publié automatiquement en v1 (JOB-7 désactivé). Utiliser /retry demain pour le publier manuellement." |

> **[TG-M3] "Prochain post" dans les messages de démarrage et `/status` :** obtenu via `scheduler.get_job("job_generate").next_run_time` (instance APScheduler en mémoire). Retourné sous forme de `datetime` UTC, affiché en heure locale (`Europe/Paris`) avec le format `%Hh%M`. **Si le scheduler est en pause :** `next_run_time` peut être `None` ou non actualisé — afficher `"—"` à la place de l'heure. Si `scheduler` n'est pas encore démarré (fenêtre de démarrage), afficher `"—"`.

> **[TG-I4] Limite journalière dans `handle_approve`** : quand la limite est atteinte après clic sur le bouton, utiliser `query.edit_message_text(...)` uniquement — **ne pas appeler `notify_all`** en plus. L'utilisateur qui a cliqué voit déjà le message édité ; un appel supplémentaire à `notify_all` produirait une double notification dans son chat.

> **`notification_debounce` dans `notify_all` :** `config.telegram.notification_debounce` (défaut : `2` secondes — voir CONFIGURATION.md section `telegram`) est un délai `await asyncio.sleep(debounce)` inséré entre chaque envoi vers les différents `authorized_user_ids`. Il limite les rafales de requêtes vers l'API Telegram lorsque `authorized_user_ids` contient plusieurs entrées. N'affecte pas les `send_message` des commandes individuelles (qui n'utilisent pas `notify_all`).

> **Comportement de `notify_all` si l'envoi échoue pour un admin :** chaque envoi passe par `send_with_retry` (5 tentatives). Si les 5 tentatives échouent, l'exception est loggée et `notify_all` **continue vers les autres admins** (ne pas lever l'exception). `notify_all` ne retourne aucune erreur à l'appelant — il garantit le meilleur effort mais ne garantit pas la livraison.

> **[TG-F15] Risque de blocage depuis APScheduler :** `send_with_retry` avec 5 retries et backoff exponentiel (1s, 2s, 4s, 8s, 16s ≈ 31s total par admin, plus délais inter-tentatives ≈ 160s dans le pire cas) peut bloquer longtemps si appelée depuis un job APScheduler. `notify_all` appelle `send_with_retry` pour chaque admin — le pire cas est N × 160s bloquant le job. Recommandation : depuis les contextes APScheduler, appeler `notify_all` dans `asyncio.create_task(notify_all(...))` pour ne pas bloquer le job. Depuis les handlers Telegram (contexte PTB), l'appel direct est acceptable car PTB gère sa propre boucle.

---

## Perte d'état au redémarrage (ConversationHandler)

python-telegram-bot ne persiste pas les états de conversation entre redémarrages. Si l'utilisateur était en cours de modification de légende (état `WAITING_CAPTION`) au moment d'un arrêt, l'état est perdu.

**`recover_pending_posts` — signature complète (TG-F2 / TRANSVERSAL-3) :**

```python
# Dans scheduler/jobs.py
async def recover_pending_posts(
    session: AsyncSession,  # session pour les opérations initiales SELECT/UPDATE uniquement
    bot: Bot,               # instance PTB Bot pour l'envoi des messages
    config: Config          # configuration complète
) -> None:
```

Cette fonction est appelée depuis `main_async()` dans la séquence de démarrage (voir section "Intégration avec la boucle asyncio principale"), **pas** depuis un handler Telegram. Pour l'implémentation détaillée, voir SCHEDULER.md.

**Comportement au redémarrage :**
1. L'app re-envoie les posts `pending_approval` sur Telegram (voir SCHEDULER.md — `recover_pending_posts`).
2. Les boutons sont à nouveau actifs sur ces messages.
3. Si l'utilisateur envoie du texte libre (réponse à une conversation interrompue), le bot l'ignore silencieusement.
4. L'utilisateur clique à nouveau sur le bouton d'édition pour rouvrir le flux.

> **Anti-duplication :** avant de renvoyer un post `pending_approval`, `recover_pending_posts` vérifie si `post.telegram_message_ids` est non-vide. Si oui, le post a déjà été envoyé lors d'un démarrage précédent — ne pas renvoyer, les anciens messages ont déjà leurs boutons actifs. Ne renvoyer que si `telegram_message_ids == {}` (premier envoi jamais tenté ou envoi échoué silencieusement).

> **[TG-M10] Envoi partiel non récupérable :** si `send_approval_request` a réussi pour l'admin A mais échoué pour l'admin B, `telegram_message_ids = {str(A): msg_id_A}` (non-vide). Au redémarrage, `recover_pending_posts` voit `telegram_message_ids != {}` et ne renvoie pas le post — l'admin B ne recevra jamais le message (sauf si un admin approuve/rejette le post, auquel cas le post disparaît de toute façon). Ce comportement est documenté comme acceptable pour v1 : avec un seul admin (usage courant), ce cas ne se produit pas. **Conséquence pour les posts `approved` :** `recover_pending_posts` couvre uniquement les posts `pending_approval`. Les posts `status='approved'` (approuvés mais non publiés avant crash) sont repris directement par `_publish_approved_post` — voir section "Gestion hors-ligne".

Ce comportement est acceptable : aucune donnée n'est perdue, seule l'interaction est interrompue.

---

## Pattern de session SQLAlchemy dans les handlers (TRANSVERSAL-6)

Chaque handler appelle `async with get_session() as session:` directement pour obtenir une session. `get_session()` fonctionne grâce à la `_session_factory` globale configurée par `init_context()` — aucun paramètre supplémentaire n'est nécessaire, et il n'est pas nécessaire de passer l'engine via `bot_data`.

```python
# Pattern standard pour tous les handlers
@authorized_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with get_session() as session:
        # Toutes les opérations DB dans ce bloc
        state = await get_scheduler_state(session, "paused")
        ...
    await update.message.reply_text(...)
```

`get_session()` est importé depuis `ancnouv.db.session`. La session factory est configurée globalement par `init_context()` au démarrage de l'application — tous les handlers partagent la même factory sans qu'il soit nécessaire de la transmettre explicitement.

---

## Configuration python-telegram-bot (`bot/bot.py`)

```python
def create_application(token: str) -> Application: ...
```

`create_application` construit l'`Application` via `Application.builder().token(token).build()` et enregistre tous les handlers **dans cet ordre** (PTB route vers le premier handler qui correspond) :

1. `edit_conv_handler` (ConversationHandler — doit être enregistré en **premier** car il gère les patterns `r"^edit:\d+$"` qui seraient sinon capturés par un `CallbackQueryHandler` générique)
2. `CallbackQueryHandler` pour `approve`, `reject`, `skip` (patterns `r"^approve:\d+$"`, etc.)
3. `CommandHandler` pour chaque commande (`start`, `status`, `pause`, `resume`, `force`, `stats`, `pending`, `retry`, `retry_ig`, `retry_fb`, `help`)

> **Pourquoi `edit_conv_handler` en premier :** si un `CallbackQueryHandler(pattern=r"^edit:\d+$")` autonome est enregistré avant le `ConversationHandler`, il capturera les callbacks `edit:*` et le `ConversationHandler` ne sera jamais activé. PTB résout l'ambiguïté par ordre d'enregistrement — le premier handler correspondant gagne.

---

## Intégration avec la boucle asyncio principale

Voir ARCHITECTURE.md — section `main_async` pour la séquence canonique complète.

```python
async def main_async(config: Config) -> None: ...
def run(config: Config) -> int: ...
```

Points clés :
- `run(config)` est le point d'entrée CLI (dans `scheduler/__init__.py`) : `asyncio.run(main_async(config))`, retourne `0` ou `1`
- `config.telegram_bot_token` est un champ **racine** de `Config` (**pas** `config.telegram.bot_token` — voir CONFIGURATION.md)
- `bot_app.bot_data["config"] = config` est injecté **avant** `init_context` pour que `authorized_only` fonctionne dès le premier message reçu
- `start_local_image_server` est démarré **avant** `recover_pending_posts` (les URLs d'images doivent être accessibles)
- `await bot_app.run_polling(stop_signals=None)` — PTB ne capture pas les signaux (APScheduler les gère)
- `scheduler.shutdown(wait=False)` après retour de `run_polling`

**Ordre exact de démarrage (TG-F17) :**
1. `await bot_app.initialize()` — initialise l'Application PTB
2. `await bot_app.start()` — démarre le polling (handlers actifs, messages reçus)
3. `await recover_pending_posts(session, bot_app.bot, config)` — APRÈS `start()` pour que les messages puissent être envoyés
4. `await bot_app.run_polling()` — boucle principale (bloquante)

> **Note PTB 20.x :** `run_polling()` appelle implicitement `initialize()` et `start()` s'ils n'ont pas encore été appelés. Si l'appel explicite à `start()` avant `recover_pending_posts` n'est pas compatible avec la version PTB utilisée (comportement non documenté officiel), vérifier si `bot_app.start()` est idempotent ou lever le problème. En pratique, l'appel explicite à `start()` suivi de `run_polling()` est le pattern recommandé pour exécuter du code entre le démarrage du polling et la boucle principale.

---

## Gestion hors-ligne

Si Telegram est inaccessible au moment d'envoyer un message, le bot :
- Retente x5 avec backoff exponentiel (2s, 4s, 8s, 16s, 32s)
- Après 5 échecs : logue l'erreur et continue (ne pas bloquer le scheduler)

Cette logique est encapsulée dans `send_with_retry` (`bot/notifications.py`) :

```python
async def send_with_retry(coro_factory: Callable[[], Awaitable[Message]], max_attempts: int = 5) -> Message | None: ...
```

`coro_factory` est une lambda retournant la coroutine Telegram à appeler (ex : `lambda: bot.send_message(...)`). Chaque tentative recrée la coroutine depuis la factory. Retourne `Message` en cas de succès, `None` après épuisement des tentatives (l'exception est loggée mais non propagée). Tous les `send_message` / `send_photo` dans `notify_all` et `send_approval_request` passent par `send_with_retry`.

Les posts `approved` non encore publiés ne sont pas perdus même si le bot est inaccessible : `recover_pending_posts` traite **uniquement les posts `pending_approval`** (renvoi du message de validation). Les posts `status='approved'` (approuvés mais non publiés avant crash) sont traités séparément : `recover_pending_posts` appelle `_publish_approved_post` directement pour chaque post `approved` trouvé en DB au démarrage. Ces deux traitements sont distincts dans `recover_pending_posts`.
