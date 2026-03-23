# Scheduler

> Référence : [SPEC-3.5]

---

## Choix technique

**APScheduler 3.x** avec `AsyncIOScheduler`.

Justification :
- Async natif (compatible avec python-telegram-bot v20+)
- Persistance des jobs en SQLite (via `SQLAlchemyJobStore`)
- Support des expressions cron
- Récupération automatique des jobs après redémarrage

---

## Jobs définis

> **État `paused` et couverture :** seul **JOB-3** (`job_generate`) vérifie `scheduler_state.paused` en début d'exécution. JOB-1 (collecte Wikipedia), JOB-2 (collecte RSS), JOB-4 (expiration), JOB-5 (token), JOB-6 (nettoyage) continuent de s'exécuter pendant une pause opérateur. Conséquence : JOB-4 expire silencieusement des posts `pending_approval` pendant une pause si `approval_timeout_hours` est dépassé. Comportement intentionnel — la pause bloque uniquement la génération de nouveaux posts, pas la maintenance.

### [JOB-1] `job_fetch_wiki` — Collecte Wikipedia

| Paramètre | Valeur |
|-----------|--------|
| Type | `cron` |
| Expression | `0 2 * * *` (2h du matin chaque jour) |
| Timezone | `config.scheduler.timezone` |
| Persistant | Oui (SQLAlchemyJobStore) |

**Comportement :**
1. Calcule les `prefetch_days` prochains jours (défaut : 30) à partir d'aujourd'hui
2. Pour chaque date, calcule les paramètres effectifs via `get_effective_query_params(session, config)` (niveau d'escalade inclus)
3. Appelle `WikipediaFetcher(config, effective_params).fetch(date)` pour chaque date
4. Stocke les nouveaux événements en DB (`fetcher.store(items, session)`)
5. En fin de collecte, vérifie le stock sur les 7 prochains jours — si < `low_stock_threshold`, incrémente `escalation_level` via `increment_escalation_level(session)`

> **[SC-11] Correspondance `escalation_level → EffectiveQueryParams` :** voir DATA_SOURCES.md [DS-1.4b] pour le tableau complet `escalation_level → event_types/use_fallback_en/dedup_policy`.

> **Cliquet à sens unique :** `escalation_level` n'est jamais décrémenté automatiquement. Seule la commande CLI `escalation reset` le remet à 0. Ce choix est intentionnel : un stock bas est un signal d'alarme que l'opérateur doit traiter explicitement — un retour automatique masquerait le problème.

**Gestion d'erreur :** Si l'API Wikipedia est inaccessible → `with_retry()` x3 → si échec, log WARNING et passer à la date suivante (pas d'arrêt du job entier).

> **[SC-M1] Concurrence JOB-1 / JOB-3 à 2h00 :** les deux jobs se déclenchent à 2h00 (JOB-1 : `0 2 * * *`, JOB-3 si l'expression cron inclut 2h). SQLite autorise les lectures parallèles mais sérialise les écritures. En cas de contention, SQLAlchemy retourne `OperationalError: database is locked`. JOB-1 et JOB-3 doivent tous les deux capturer `OperationalError` et retenter x3 avec backoff (1s, 2s, 4s) avant d'abandonner avec log ERROR. L'expression cron par défaut de JOB-3 (`0 */4 * * *`) déclenche aussi à 0h et 4h — 2h est un cas de déclenchement simultané. Pour l'éviter, décaler JOB-3 via la config (`scheduler.job_generate_cron: "30 */4 * * *"`) ou configurer `connect_args={"timeout": 15}` (voir DATABASE.md).

---

### [JOB-2] `job_fetch_rss` — Collecte RSS (optionnel)

| Paramètre | Valeur |
|-----------|--------|
| Type | `interval` |
| Intervalle | 6 heures |
| Actif si | `config.content.rss.enabled = true` |
| Enregistré uniquement si | `config.content.rss.enabled = true` (vérification dans `create_scheduler`) |
| Persistant | Oui (SQLAlchemyJobStore) |
| `start_date` | Non défini — voir note ci-dessous |

> **Déclenchement au redémarrage (SC-C1) :** sans `start_date`, APScheduler peut déclencher JOB-2 immédiatement au redémarrage si le dernier déclenchement remonte à plus de 6 heures. Ce comportement est **intentionnel** : une collecte RSS au démarrage garantit un stock à jour. La déduplication par `article_url` (table `rss_articles`) rend la re-collecte idempotente. Si ce comportement est indésirable (ex: surcharge au démarrage), ajouter `start_date=datetime.now(UTC) + timedelta(hours=6)` dans `scheduler.add_job(...)`.
>
> **Note SC-C1 et `misfire_grace_time` :** le déclenchement immédiat au redémarrage ne s'applique que si le délai depuis le dernier déclenchement est < `misfire_grace_time` (300s). Si le délai > 300s, JOB-2 est **skipé** (pas de déclenchement immédiat) et sera déclenché normalement au prochain intervalle de 6 heures. Voir aussi section "Récupération APScheduler après arrêt".

> **[SC-6] Timezone :** la timezone est configurée au niveau de l'`AsyncIOScheduler` globalement via `timezone=config.scheduler.timezone` dans `create_scheduler()`. Les jobs `interval` héritent de la timezone du scheduler.

**Comportement :**
1. Appelle `RssFetcher().fetch_all(config)` — itère sur `config.content.rss.feeds`, wrappé avec `asyncio.to_thread()` (feedparser est synchrone). Chaque feed est traité séquentiellement (pas `asyncio.gather`) pour éviter la saturation du thread pool par des feeds à timeout long — un feed bloquant à 30s mobiliserait N threads du default executor si N feeds tournent en parallèle.
2. Stocke les articles en DB (`store(articles, session)`) — déduplication par `article_url`

---

### [JOB-3] `job_generate` — Génération et soumission

| Paramètre | Valeur |
|-----------|--------|
| Type | `cron` |
| Expression | `config.scheduler.job_generate_cron` (défaut : `"0 */4 * * *"`, soit 6 fois/jour) |
| Timezone | `config.scheduler.timezone` |
| Persistant | Oui (SQLAlchemyJobStore) |
| `max_instances` | 1 — pas d'exécution parallèle (voir note SC-C7) |

**Comportement — mode `auto_publish = false` (défaut) :**
1. Vérifie `scheduler_state.paused = 'true'` — si oui, retourne immédiatement
2. Compte les posts `pending_approval` en DB — si `count >= max_pending_posts`, skip
3. Appelle `generate_post(session)` (voir ARCHITECTURE.md — Génération hybride) → retourne un `Post` ou `None`
4. Si un post est généré : `send_approval_request(bot, post, config, session)` — envoie image + boutons inline
5. Si `generate_post` retourne `None` : `notify_all(bot, config, "⚠️ Aucun événement disponible pour le [date].")`

> **[SC-C7] Comportement APScheduler quand `max_instances=1` et instance active :** si un déclenchement de JOB-3 arrive alors qu'une instance est encore en cours d'exécution, APScheduler **skippe silencieusement** ce déclenchement (sans log WARNING ni exception). Ce comportement garantit l'absence de génération parallèle mais peut faire manquer un créneau de 4h si une génération est exceptionnellement longue (ex: upload image lent). La limite de `max_daily_posts` reste cohérente car les deux instances ne s'exécutent jamais simultanément.

> La limite journalière n'est **pas** vérifiée ici : en mode `auto_publish = false`, le post n'est pas encore publié. La limite est vérifiée uniquement au moment de la publication effective, dans `handle_approve` (via `check_and_increment_daily_count`).

**Comportement — mode `auto_publish = true` :**
1–2 : identiques (pause, pending_count)
3. Appelle `generate_post(session)` — si retourne `None`, skip (notifier "aucun événement") sans incrémenter le compteur
4. Vérifie `check_and_increment_daily_count(engine, max_daily_posts)` — si limite atteinte, skip
5. Upload image → `publish_to_all_platforms(...)` → notification résultat

> **Ordre critique :** `generate_post` est appelé **avant** `check_and_increment_daily_count`. Inverser l'ordre incrémenterait le compteur journalier même si aucun événement n'est disponible — le quota serait consommé sans publication.

**Politique d'erreur JOB-3 :** toutes les exceptions non catchées dans `job_generate` sont propagées à APScheduler qui les logue (`ERROR`) et reschedule normalement. États partiels en DB : `generate_post` opère dans une transaction et rollback en cas d'exception — aucun enregistrement partiel de post. `send_approval_request` peut avoir envoyé à certains admins avant l'exception : les admins ayant reçu le message peuvent approuver/rejeter normalement. `telegram_message_ids` sera incomplet pour les admins non atteints. En pratique, wrapper le corps de `job_generate` dans un `try/except Exception as e: logger.error("JOB-3 erreur: %s", e, exc_info=True)` pour logguer avec traceback sans interrompre APScheduler.

**Comportement après arrêt prolongé :** après un arrêt de 12h, les exécutions manquées de JOB-3 dépassent le `misfire_grace_time` (300s) et sont toutes **skippées**. Aucune rafale de posts au redémarrage. Pour forcer une exécution immédiate, utiliser `/force` via le bot Telegram. (Voir section "Récupération APScheduler après arrêt" pour le comportement général `coalesce=True`.)

---

### [JOB-4] `job_check_expired` — Expiration des posts

| Paramètre | Valeur |
|-----------|--------|
| Type | `interval` |
| Intervalle | 1 heure |
| Persistant | Non (MemoryJobStore) |

**Comportement :**
1. Cherche les posts `pending_approval` créés depuis > `config.scheduler.approval_timeout_hours` heures. Clé de config : `scheduler.approval_timeout_hours`, défaut : **48** (voir CONFIGURATION.md). SPEC.md [RF-3.3.3] fait foi.
2. Les marque `expired`
3. Désactive les boutons Telegram : `post.telegram_message_ids` est un dict `{user_id: message_id}` — itérer sur `.items()` avec guard `if message_id is not None`, appeler `bot.edit_message_reply_markup(chat_id=int(user_id), message_id=message_id, reply_markup=None)` pour chaque entrée valide. Ne pas itérer sur `config.telegram.authorized_user_ids` avec lookup : un admin pour lequel l'envoi initial avait échoué n'aurait pas d'entrée dans le dict → `KeyError`.
4. Notifie l'utilisateur : "⚠️ Post expiré sans validation"

> **[SC-M2] Non-persistance de JOB-4 et redémarrages longs :** JOB-4 tourne dans `MemoryJobStore` — il n'est pas persisté entre redémarrages. Après un arrêt de > 48h (valeur de `approval_timeout_hours`), des posts `pending_approval` peuvent avoir dépassé le délai sans être marqués `expired`. Au redémarrage, JOB-4 ne se déclenche qu'une heure après le démarrage (intervalle de 1h). Ces posts restent `pending_approval` avec leurs boutons actifs jusqu'au prochain déclenchement de JOB-4. Comportement acceptable en v1 : `recover_pending_posts` renvoie les messages (boutons actifs) et JOB-4 les expirera dans l'heure.

---

### [JOB-5] `job_check_token` — Surveillance token Meta

| Paramètre | Valeur |
|-----------|--------|
| Type | `cron` |
| Expression | `0 9 * * *` (quotidien à 9h) |
| Persistant | Non (MemoryJobStore) |

> **[SC-M4] Vérification du token au démarrage :** JOB-5 tourne à 9h (`0 9 * * *`). Après un redémarrage tardif (ex: 20h), la prochaine vérification est à 9h le lendemain — soit ~13h sans contrôle du token. Pour combler cette fenêtre, `main_async()` **appelle `job_check_token()` directement** au démarrage (après `init_context`), avant `scheduler.start()`. Cet appel initial utilise le même mécanisme anti-doublon (`last_alert_days_threshold`) — il n'enverra pas de notification si le seuil n'a pas changé depuis la dernière exécution normale.

> **Pourquoi quotidien ?** Un token peut passer de 8 jours restants à 7 en 24h. Un job hebdomadaire pourrait manquer les seuils d'alerte progressifs (30j, 14j, 7j, 3j, 1j) définis dans INSTAGRAM_API.md — section [IG-2.4].

**Comportement :**
1. Lit le token `user_long` dans `meta_tokens`
2. Calcule les jours restants avant expiration (`days_until_expiry`)
3. Détermine le seuil d'alerte courant via `get_alert_threshold(remaining)` — retourne `0` si expiré/aujourd'hui, `1` pour demain, un entier du tableau [30,14,7,3,1] si seuil atteint, `None` sinon
4. **Anti-doublon — deux mécanismes complémentaires [TRANSVERSAL-4] :**
   - `MetaToken.last_alert_days_threshold` (INTEGER dans `meta_tokens`) : seuil numérique de la dernière alerte envoyée — anti-doublon par comparaison de seuil. Si `get_alert_threshold(remaining) == meta_token.last_alert_days_threshold: return  # déjà alerté pour ce seuil`
   - `scheduler_state.token_alert_level` (TEXT dans `scheduler_state`) : niveau lisible pour `/status` (ex : `"7j"`, `"expired"`)
   - Ces deux champs sont mis à jour ensemble après chaque alerte.
5. Seuils 30j, 14j : notification Telegram uniquement
6. Seuils 7j, 3j : notification + tentative refresh automatique via `TokenManager.get_valid_token()`
7. Seuil 1j ou expiré + refresh échoue : écrit `scheduler_state.publications_suspended = "true"` — bloque les publications. Alerte bloquante. La suspension est levée uniquement par `auth meta` réussi

---

### [JOB-6] `job_cleanup` — Nettoyage des fichiers

| Paramètre | Valeur |
|-----------|--------|
| Type | `cron` |
| Expression | `0 3 * * *` (3h du matin) |
| Persistant | Non (MemoryJobStore) |

**Comportement :**
1. Cherche les posts `published`, `rejected`, `expired`, `skipped` dont `created_at < now - config.content.image_retention_days` jours. Clé de config : `content.image_retention_days`, défaut : **7** jours (voir CONFIGURATION.md — `ContentConfig`). **[SC-3 / TRANSVERSAL-5]** La clé canonique est `content.image_retention_days` (dans `ContentConfig`), pas `scheduler.image_retention_days`.
2. Pour chaque post, tente `Path(post.image_path).unlink()`. Si le fichier n'existe pas (`FileNotFoundError`) : **ignorer silencieusement** — écrire quand même `image_path = NULL` en DB. Ce cas est normal après un crash ou une suppression manuelle. Log DEBUG uniquement (pas WARNING — c'est un nettoyage idempotent).
3. Met `image_path = NULL` en DB
4. Logue l'espace libéré (somme des tailles avant suppression)

---

### [JOB-7] `job_publish_queued` — Publication des posts planifiés (v2)

> **Désactivé en v1** — commenté dans `create_scheduler()`. Si des posts `queued` existent en DB v1 (via `handle_approve` quand limite journalière atteinte), ils resteront bloqués indéfiniment car JOB-7 ne s'exécute pas.
>
> **[SC-15 / TRANSVERSAL-2] Procédure de déblocage manuel en v1 :** les posts bloqués en `queued` peuvent être débloqués en exécutant `UPDATE posts SET status='approved' WHERE status='queued'` via `python -m ancnouv db reset` (dev) ou en se connectant directement à SQLite (`sqlite3 data/ancnouv.db`), puis en lançant `/retry` depuis Telegram. Voir aussi section `recover_pending_posts`.

**Comportement (v2) :**
1. Cherche les posts `status = 'queued'` ET `scheduled_for <= now()`
2. Pour chaque post éligible (ordre d'approbation) : vérifie la limite journalière, si atteinte stoppe l'itération et notifie
3. Sinon : `status = 'publishing'`, déclenche la publication

---

## Configuration APScheduler (`scheduler/__init__.py`)

```python
def create_scheduler(config: Config) -> AsyncIOScheduler: ...
```

`create_scheduler` reçoit uniquement `config`. Les dépendances des jobs (`bot`, `session`, `engine`) sont **injectées via le contexte partagé** (`get_bot_app()`, `get_session()`, `get_engine()`) — pas via `args=` ou `kwargs=` APScheduler. `init_context(config, bot_app, engine)` doit être appelé avant `create_scheduler` pour que les getters fonctionnent au premier déclenchement. Avantage : signature de job simplifiée (`async def job_generate() -> None:`), sans dépendances passées en paramètre.

> **[TRANSVERSAL-2] Posts `queued` bloqués en v1 :** JOB-7 est commenté dans cette fonction. Si des posts `queued` existent (via `handle_approve` quand la limite journalière est atteinte), ils resteront bloqués indéfiniment. Procédure de déblocage : `UPDATE posts SET status='approved' WHERE status='queued'` (voir JOB-7 et section `recover_pending_posts`).

Paramètres :
- Deux jobstores : `"default"` = `SQLAlchemyJobStore(url="sqlite:///{config.data_dir}/scheduler.db")` (SQLAlchemy **synchrone** — URL sans `aiosqlite`), `"memory"` = `MemoryJobStore()`
- `timezone=config.scheduler.timezone` — **[SC-6]** tous les jobs `cron` et `interval` héritent de cette timezone
- `coalesce=True`, `max_instances=1`, `misfire_grace_time=300`
- JOB-1, JOB-2 (si `rss.enabled`), JOB-3 : persistants (jobstore `default`), `replace_existing=True`
- JOB-4, JOB-5, JOB-6 : non-persistants (jobstore `memory`), pas besoin de `replace_existing`
- JOB-7 : commenté (v2)

**`replace_existing=True` est obligatoire** pour les jobs avec un id fixe sur le jobstore persistant — au redémarrage, le job existe déjà en DB et sans ce paramètre `ConflictingIdError` est levée.

---

## Table `scheduler_state`

Voir DATABASE.md — section `scheduler_state` pour le schéma DDL complet. Résumé des clés utilisées par les jobs :

| Clé | Type | Utilisé par |
|-----|------|------------|
| `paused` | `"true"` / `"false"` | JOB-3, `cmd_pause`, `cmd_resume` |
| `daily_post_count` | JSON `{"date": "YYYY-MM-DD", "count": N}` | `check_and_increment_daily_count`, JOB-3, `handle_approve` |
| `escalation_level` | entier stringifié (`"0"`..`"4"`) | JOB-1, `job_generate`, CLI `escalation reset` |
| `token_alert_level` | `"normal"` \| `"30j"` \| `"14j"` \| `"7j"` \| `"3j"` \| `"1j"` \| `"expired"` | JOB-5, `cmd_status` |
| `publications_suspended` | `"true"` / absent | JOB-5, `check_and_increment_daily_count` |

---

## Contexte partagé (`scheduler/context.py`)

Voir ARCHITECTURE.md — section "Contexte partagé". Les singletons sont accessibles depuis les jobs via `get_config()`, `get_bot_app()`, `get_engine()`. Les getters lèvent `RuntimeError` si le contexte n'est pas initialisé (pas `assert` — désactivé avec `python -O`).

> **Relation `get_engine()` → `get_session()` :** `get_session()` (défini dans `ancnouv/db/session.py`) utilise une `_session_factory` créée à partir du moteur fourni par `set_engine(engine)` (défini dans `ancnouv/scheduler/context.py`). `init_context(config, bot_app, engine)` appelle `set_engine(engine)` en interne, rebindant la `_session_factory` globale. Les jobs appellent `get_session()` — qui retourne une session sur l'engine correct — sans connaître l'engine directement. En test, `set_engine(test_engine)` redirige toutes les sessions vers la DB en mémoire.
>
> **Deux patterns d'accès à `scheduler_state` — justification :** les opérations normales sur `scheduler_state` (lecture/écriture de `paused`, `publications_suspended`, etc.) utilisent `async with get_session() as session:` — cohérent avec l'ORM et les autres tables. L'opération `check_and_increment_daily_count` utilise `engine.connect()` + `BEGIN EXCLUSIVE` car elle requiert un verrou exclusif atomique que SQLAlchemy ORM ne peut pas obtenir via `AsyncSession` (la transaction ORM ne peut pas envoyer `BEGIN EXCLUSIVE` directement). Ces deux styles coexistent intentionnellement : AsyncSession pour les accès simples, `engine.connect()` exclusivement pour les opérations nécessitant un verrou de niveau base.

**Séquence canonique de `main_async(config)` [SC-C6] :**

1. `engine = await init_db(db_path)` — moteur SQLAlchemy créé, migrations appliquées
2. `bot_app = create_application(config.telegram_bot_token)` — Application PTB construite, handlers enregistrés
3. `bot_app.bot_data["config"] = config` — config injectée avant `init_context`
4. `init_context(config, bot_app, engine)` — singletons initialisés (setters `set_config`, `set_bot_app`, `set_engine`, `set_session_factory`)
5. `scheduler = create_scheduler(config)` — jobs ajoutés, **pas encore démarré**
6. `runner = await start_local_image_server(...)` si `backend=local` — serveur images démarré avant les publications
7. `await recover_pending_posts(...)` — posts `publishing`→`approved`, posts `approved` republications, posts `pending_approval` renvoyés
8. `scheduler.start()` — les jobs peuvent appeler `get_config()`, `get_engine()`, `get_session()`
9. `await bot_app.run_polling(stop_signals=None)` — boucle principale PTB (bloquante)
10. `scheduler.shutdown(wait=False)` — à la sortie de `run_polling`

> Inverser 4 et 8 → `RuntimeError: contexte non initialisé` dès le premier job déclenché.
> Inverser 6 et 7 → `recover_pending_posts` tente de renvoyer des posts avec des URLs d'images inaccessibles.

**Accès dans les jobs — pattern standard :**

```python
async def job_generate() -> None:
    config = get_config()
    bot = get_bot_app().bot
    async with get_session() as session:
        ...
```

---

## Gestion de la pause/reprise

La pause est gérée via `scheduler_state.paused` — pas via l'API APScheduler (pour persister entre redémarrages).

```python
async def pause_scheduler(session: AsyncSession) -> None: ...
async def resume_scheduler(session: AsyncSession) -> None: ...
async def is_paused(session: AsyncSession) -> bool: ...
```

`pause_scheduler` : écrit `scheduler_state.paused = "true"`. Les jobs continuent d'être déclenchés par APScheduler mais vérifient ce flag en début d'exécution. `is_paused` : retourne `True` si `scheduler_state.paused = "true"`.

> La pause ne bloque que la génération automatique (JOB-3). Les posts `pending_approval` générés avant la pause restent disponibles pour approbation manuelle.

> **[SC-14] `handle_approve` pendant une pause :** `handle_approve` fonctionne normalement pendant une pause scheduler — les approbations manuelles ne sont jamais bloquées par l'état `paused`. La pause bloque uniquement la génération automatique (JOB-3), pas les interactions Telegram manuelles.

> **[SC-M3] JOB-4 continue pendant la pause :** JOB-4 (`job_check_expired`) n'est pas soumis à `scheduler_state.paused` — il expire silencieusement les posts `pending_approval` même pendant une pause. Si la pause dure > `approval_timeout_hours`, tous les posts en attente seront expirés avant la reprise. Avant de reprendre le scheduler, utiliser `/pending` pour vérifier quels posts expirent bientôt et les approuver manuellement si nécessaire.

---

## Persistance : deux mécanismes distincts [SC-M8]

| Mécanisme | Fichier | Contient | Survit au redémarrage |
|-----------|---------|----------|----------------------|
| **APScheduler jobstore** | `data/scheduler.db` | Définitions des jobs (id, expression cron, état APScheduler) | Oui pour jobs persistants (JOB-1/2/3), Non pour `memory` (JOB-4/5/6) |
| **État métier** | `data/ancnouv.db` — table `scheduler_state` | État opérationnel : `paused`, compteur journalier, escalade, alertes | Oui (table SQLite standard) |

Ces deux mécanismes sont **indépendants**. Supprimer `scheduler.db` réinitialise les jobs APScheduler (ils seront recrées via `replace_existing=True` au prochain démarrage) sans toucher à l'état métier. `scheduler_state` est géré exclusivement par l'application — APScheduler n'y écrit jamais.

---

## Récupération après crash (`scheduler/jobs.py`)

```python
async def recover_pending_posts(session: AsyncSession, bot: Bot, config: Config) -> None: ...
```

> Définie dans `scheduler/jobs.py`. **[TRANSVERSAL-3]** Signature complète : `recover_pending_posts(session: AsyncSession, bot: Bot, config: Config) -> None`.

> **[SC-m7] Session externe vs opérations longues :** `recover_pending_posts` reçoit une `session` externe pour les lectures initiales (requêtes `posts`). Les publications Meta (`publish_to_all_platforms`) créent leurs propres sessions internes. La session externe **ne doit pas** être utilisée à l'intérieur de `publish_to_all_platforms` — elle resterait ouverte pendant les appels réseau Meta (potentiellement longue). Concrètement : la session externe sert uniquement aux requêtes `SELECT` et `UPDATE status='approved'/'error'` au début de la fonction, puis elle est clôturée (via `async with`) avant les publications.

Appelée dans `main_async()` **après** `start_local_image_server()` et **avant** `scheduler.start()` — les URLs d'images doivent être accessibles avant de re-envoyer les posts sur Telegram.

> **[SC-16] Mode `backend=remote` :** si `image_hosting.backend='remote'`, les URLs dans `image_public_url` référencent le serveur distant. Au redémarrage, ces URLs peuvent être inaccessibles si `ancnouv-images` n'est pas encore démarré. La contrainte "appeler `recover_pending_posts` après `start_local_image_server`" vaut aussi pour le mode remote : s'assurer que le serveur distant est accessible avant `recover_pending_posts`.

Séquence :
1. Remet les posts `publishing` → `approved` (crash pendant publication)
2. Pour les posts `approved` avec `image_public_url` non-NULL : tente la publication immédiate via `publish_to_all_platforms`. **[SC-4]** Avant de relancer la publication, vérifier que `post.instagram_post_id IS NULL` (pour Instagram) et `post.facebook_post_id IS NULL` (pour Facebook). Si l'une est déjà renseignée, cette plateforme a déjà publié — ne pas re-publier sur cette plateforme.
3. Pour les posts `approved` sans `image_public_url` : notifie l'utilisateur de lancer `/retry` manuellement
4. Re-envoie sur Telegram les posts `pending_approval` dont `telegram_message_ids == {}` (pas encore envoyés ou envoi initial échoué). Ne pas renvoyer les posts déjà dans `telegram_message_ids` pour éviter les doublons en cas de crash loop.

> **Posts `queued` en v1 :** `recover_pending_posts` ne traite **pas** les posts `queued`. En v1, JOB-7 est commenté — les posts `queued` restent bloqués indéfiniment après un redémarrage. Résolution manuelle : `UPDATE posts SET status='approved' WHERE status='queued'` via `db reset` (dev) ou intervention SQL directe (prod). En v2, JOB-7 reprend ces posts automatiquement.

> **Posts `approved` au redémarrage :** les posts `approved` sans `image_public_url` (upload non terminé avant le crash) ne sont pas automatiquement re-publiés. L'utilisateur doit lancer `/retry` manuellement. Ce comportement est intentionnel — évite de tenter un upload vers un serveur potentiellement indisponible au démarrage.

> **[SC-9] Posts `pending_approval` chronologiquement expirés :** les posts `pending_approval` renvoyés au redémarrage peuvent avoir dépassé `approval_timeout_hours` si l'arrêt a duré suffisamment longtemps, mais ne sont pas encore marqués `expired` (JOB-4 n'a pas encore tourné). Ces posts ont des boutons actifs après renvoi. JOB-4 les expirera dans l'heure. Comportement acceptable : l'utilisateur peut approuver un post techniquement expiré pendant cette fenêtre d'~1h — l'approbation est acceptée normalement (la vérification d'expiration n'a lieu que dans JOB-4, pas dans `handle_approve`).

> **[SC-10] Posts `pending_approval` partiellement envoyés :** les posts avec `telegram_message_ids` partiellement rempli (certains admins ont reçu, d'autres non, après crash partiel) ne sont **pas** renvoyés — seuls les posts avec `telegram_message_ids == {}` sont renvoyés. Pour les posts partiellement envoyés, les admins qui ont reçu le message peuvent approuver normalement ; les admins qui n'ont pas reçu devront passer par `/pending` pour identifier les posts en attente.

---

## Compteur journalier Instagram

```python
async def check_and_increment_daily_count(engine: AsyncEngine, max_daily_posts: int = 25) -> bool: ...
```

Défini dans `scheduler/jobs.py`. Retourne `True` si la publication est autorisée (compteur incrémenté), `False` sinon.

**Atomicité :** pattern exact avec SQLAlchemy 2.x async + aiosqlite :

```python
async with engine.connect() as conn:
    await conn.execution_options(isolation_level="AUTOCOMMIT")
    await conn.execute(text("BEGIN EXCLUSIVE"))
    # lecture de scheduler_state.daily_post_count
    # vérification + mise à jour
    await conn.execute(text("COMMIT"))
```

`execution_options(isolation_level="AUTOCOMMIT")` empêche SQLAlchemy d'émettre son propre `BEGIN` implicite avant le `BEGIN EXCLUSIVE` manuel. Sans ce paramètre, SQLAlchemy émet `BEGIN` → `BEGIN EXCLUSIVE` échoue (`cannot start a transaction within a transaction`).

> **Compatibilité aiosqlite :** ce pattern est testé et validé avec **aiosqlite ≥ 0.20.0**. Les versions 0.17–0.19 maintiennent leur propre état de transaction dans le thread dédié, indépendamment du mode `AUTOCOMMIT` SQLAlchemy — `BEGIN EXCLUSIVE` peut lever `sqlite3.OperationalError` sur ces versions. Épingler `aiosqlite>=0.20.0` dans `requirements.txt`. Un test d'intégration dédié `test_daily_counter_exclusivity` doit vérifier que deux appels concurrents n'incrémentent pas deux fois le compteur (voir TESTING.md).

Sans verrou, JOB-3 et JOB-7 pourraient tous deux lire `count=24`, incrémenter indépendamment et publier 2 posts alors qu'un seul slot restait.

**[SC-1] Engine utilisé :** l'argument `engine` reçoit l'`AsyncEngine` de `ancnouv.db` (le même que celui passé à `init_context`) — **pas** le `scheduler.db` qui est l'engine synchrone d'APScheduler (`SQLAlchemyJobStore`). Ces deux engines coexistent dans l'application mais `check_and_increment_daily_count` opère exclusivement sur `ancnouv.db`.

**[SC-2] Ordre des vérifications dans le bloc `BEGIN EXCLUSIVE` :** la vérification de `publications_suspended` est effectuée **à l'intérieur** du bloc `BEGIN EXCLUSIVE` — avant la lecture du compteur journalier. Ainsi, si JOB-5 écrit `publications_suspended="true"` simultanément, le verrou exclusif garantit que la publication en cours sera bloquée ou autorisée correctement.

**Vérifications dans l'ordre (à l'intérieur du `BEGIN EXCLUSIVE`) :**
1. Lire `publications_suspended` ; si `"true"` → retourner `False` immédiatement
2. Lit `scheduler_state.daily_post_count` (JSON `{"date": "YYYY-MM-DD", "count": N}`)
3. Si date = aujourd'hui et `count >= max_daily_posts` → retourne `False`
4. Si date ≠ aujourd'hui (nouveau jour) → réinitialise `count = 1`
5. Sinon → incrémente `count`, écrit via `INSERT ... ON CONFLICT DO UPDATE`

**Réinitialisation :** se fait automatiquement au premier appel du jour. La date stockée dans le JSON est calculée avec `datetime.now(timezone.utc).date().isoformat()` — le reset se produit à **minuit UTC** (= 1h ou 2h Paris selon l'heure d'été). Il n'est pas nécessaire de caler `TZ=Europe/Paris` dans le conteneur — le code utilise toujours `timezone.utc` explicitement, ce qui rend le processus indépendant du `TZ` système.

> **[SC-13] Reset à minuit UTC vs limite Meta :** le reset journalier se produit à minuit UTC. La limite Meta est une fenêtre glissante de 24h, pas un compteur calendaire UTC. En pratique, l'application est plus conservative que la limite Meta réelle — ce qui est acceptable.

> **Règle absolue — tous les jobs :** toujours utiliser `datetime.now(timezone.utc)` (ou `datetime.utcnow()` pour les comparaisons avec les colonnes `CURRENT_TIMESTAMP` SQLite). `datetime.now()` sans timezone retourne l'heure locale du process — si `TZ=Europe/Paris`, les comparaisons dérivent de ±1h/2h selon l'heure d'été. Voir DATABASE.md — section ORM.

**`OperationalError: database is locked` :** `handle_approve` doit capturer cette exception et présenter un message à l'utilisateur ("⚠️ Base de données temporairement indisponible. Réessayer dans quelques secondes.") — ne pas laisser remonter une traceback silencieuse.

---

## Récupération APScheduler après arrêt

- Jobs dont l'heure passée est < `misfire_grace_time` (300s) : exécutés immédiatement au redémarrage
- Jobs dont le délai dépasse `misfire_grace_time` : skippés silencieusement (log `Execution of job [...] missed by X`)
- `coalesce=True` : si plusieurs exécutions tombent dans la fenêtre de grâce, une seule est exécutée

> **[SC-5 / SC-12] Comportement asymétrique `cron` vs `interval` :** pour les jobs `cron`, `coalesce=True` fusionne toutes les exécutions manquées en une seule (si délai < `misfire_grace_time`). Pour les jobs `interval`, si le délai dépasse `misfire_grace_time`, l'exécution est **skippée** (pas fusionnée) et le job reprend son rythme normal. La note SC-C1 sur JOB-2 (déclenchement immédiat au redémarrage) ne s'applique que si le délai < `misfire_grace_time` (300s) — si le délai > 300s, JOB-2 est skipé et déclenché normalement au prochain intervalle de 6 heures.

> **[SC-M9] `misfire_grace_time=300` global — conséquences :** un arrêt de 6 minutes (> 300s) fait manquer la collecte Wikipedia de 2h (JOB-1) jusqu'au lendemain. Ce comportement est **documenté comme acceptable** : un arrêt de 6 min est rare en production, et la collecte Wikipedia alimente un stock de 30 jours — un jour manqué n'épuise pas le stock. Si ce comportement est problématique, augmenter `misfire_grace_time` pour JOB-1 uniquement via `scheduler.add_job(..., misfire_grace_time=3600)` (APScheduler supporte un `misfire_grace_time` par job en 3.x).

---

## Commande `/force` (`cmd_force`)

> Voir aussi TELEGRAM_BOT.md pour la documentation complète de la commande.

**[SC-8]** `/force` appelle `generate_post(session)` directement dans le handler Telegram, **bypasse `max_pending_posts`**. Comportement :
- L'état `paused` n'est **pas** vérifié par `/force` — intentionnel : l'utilisateur force explicitement une génération.
- La limite `max_pending_posts` n'est **pas** vérifiée — intentionnel : même si le seuil de posts en attente est atteint, `/force` génère un post supplémentaire.
- La limite journalière Instagram (`check_and_increment_daily_count`) est vérifiée lors de l'approbation (`handle_approve`), **pas** au moment du `/force`.
