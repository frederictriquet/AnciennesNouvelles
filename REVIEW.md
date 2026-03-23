# Audit des specs — Anciennes Nouvelles

> Audit réalisé le 2026-03-22. Périmètre : SPEC.md + 11 documents docs/. Méthode : 5 sous-agents en parallèle, chacun auditant 2–3 documents avec relecture croisée de SPEC.md. Aucun code examiné — uniquement les specs.
>
> **163 findings** au total. Légende : `[CRITIQUE]` bloquant l'implémentation ou introduisant un bug de production garanti · `[MAJEUR]` ambiguïté sérieuse sans laquelle l'implémentation correcte est impossible ou incertaine · `[MINEUR]` incohérence, manque, ou risque de confusion résoluble par inférence · `[INFO]` observation ou suggestion d'amélioration.

---

## Résumé exécutif

| Document | CRITIQUE | MAJEUR | MINEUR | INFO | Total |
|----------|:--------:|:------:|:------:|:----:|:-----:|
| DATABASE.md | 2 | 8 | 5 | 3 | 18 |
| DATA_SOURCES.md | 2 | 6 | 7 | 4 | 19 |
| INSTAGRAM_API.md | 3 | 7 | 3 | 1 | 14 |
| TELEGRAM_BOT.md | 5 | 7 | 4 | 2 | 18 |
| SCHEDULER.md | 2 | 8 | 4 | 2 | 16 |
| IMAGE_GENERATION.md | 3 | 8 | 5 | 2 | 18 |
| CONFIGURATION.md | 0 | 2 | 10 | 4 | 16 |
| ARCHITECTURE.md | 2 | 8 | 7 | 2 | 19 |
| DEPLOYMENT.md | 2 | 5 | 3 | 0 | 10 |
| CLI.md | 1 | 5 | 3 | 1 | 10 |
| TESTING.md | 4 | 7 | 4 | 1 | 16 |
| **TOTAL** | **26** | **71** | **55** | **22** | **163** |

### Problèmes transversaux critiques (impactent plusieurs docs)

- [x] **[TRANSVERSAL-1]** `select_event` et `EffectiveQueryParams` : la signature de `select_event` dans DATABASE.md ne prend pas `EffectiveQueryParams` en paramètre, mais DATA_SOURCES.md [DS-1.4b] et ARCHITECTURE.md présupposent qu'elle le reçoit. Sans résolution, les niveaux d'escalade 3 et 4 (déduplication window) sont silencieusement inopérants. Impacte : DATABASE.md, DATA_SOURCES.md, ARCHITECTURE.md.

- [x] **[TRANSVERSAL-2]** Statut `queued` en v1 = état terminal sans sortie : DB DDL inclut `'queued'` dans le CHECK, `handle_approve` y envoie les posts quand la limite journalière est atteinte, mais JOB-7 (qui consommerait la file) est commenté en v1. La commande `/retry` ne traite que les posts `error`. Résultat : tout post approuvé quand `max_daily_posts` est atteint est bloqué indéfiniment. Impacte : DATABASE.md, ARCHITECTURE.md, SCHEDULER.md, TELEGRAM_BOT.md, TESTING.md.

- [x] **[TRANSVERSAL-3]** `recover_pending_posts` : mentionnée dans 6 documents (ARCHITECTURE.md, DATABASE.md, SCHEDULER.md, TELEGRAM_BOT.md, DEPLOYMENT.md, TESTING.md) mais jamais entièrement définie dans aucun d'eux. Ni la signature, ni le module d'appartenance, ni le contexte d'appel (job APScheduler ? appel direct dans `main_async` ?) ne sont précisés.

- [x] **[TRANSVERSAL-4]** `last_alert_days_threshold` (champ `meta_tokens`) vs `token_alert_level` (champ `scheduler_state`) : deux champs distincts dans deux tables différentes semblent couvrir le même mécanisme anti-doublon d'alertes d'expiration token. INSTAGRAM_API.md utilise l'un, TELEGRAM_BOT.md l'autre. Impossible d'implémenter correctement sans choisir une source de vérité.

- [x] **[TRANSVERSAL-5]** Clé de config `image_retention_days` : SCHEDULER.md utilise `scheduler.image_retention_days` (défaut 30j), IMAGE_GENERATION.md utilise `content.image_retention_days` (défaut 7j), SPEC.md [RF-3.4.8] dit "défaut 7 jours". Deux valeurs par défaut différentes, deux sections de config différentes. La clé canonique n'est pas définie.

- [x] **[TRANSVERSAL-6]** Pattern de session SQLAlchemy dans les handlers PTB : non défini dans TELEGRAM_BOT.md ni dans ARCHITECTURE.md. Comment chaque handler obtient-il une `AsyncSession` ? Injection via `context.bot_data` ? Ouverture locale dans chaque handler ? Factory globale ? Ce choix architectural structure toute l'implémentation du bot.

- [x] **[TRANSVERSAL-7]** Aucune liste de dépendances Python avec versions dans l'ensemble de la documentation. Les bibliothèques sont citées au fil du texte mais sans `requirements.txt` ni `pyproject.toml` esquissé. APScheduler 3.x vs 4.x, PTB 13.x vs 20.x, SQLAlchemy 1.x vs 2.x sont incompatibles entre elles. Un développeur qui installe les dernières versions peut tout casser.

---

## DATABASE.md

- [x] **[DB-1] [MAJEUR]** Requête `select_event` politique `"window"` : clause `last_used_at IS NULL` redondante et potentiellement masque un état incohérent (`published_count > 0` + `last_used_at IS NULL` après crash). Clarifier si cet état est possible, sinon supprimer la clause.

- [x] **[DB-2] [MAJEUR]** Atomicité non documentée : l'UPDATE de `published_count` et le UPDATE de `post.status = 'published'` doivent être dans le même `commit()`, sinon un crash entre les deux laisse un événement republibale malgré une publication réussie. Non précisé dans le doc.

- [x] **[DB-3] [MAJEUR]** Statut `queued` présent dans le CHECK en v1 mais colonnes associées (`scheduled_for`, `queued_at`) commentées. Un post peut atteindre `queued` sans mécanisme de sortie. Voir TRANSVERSAL-2.

- [x] **[DB-4] [MAJEUR]** `recover_pending_posts` citée mais non spécifiée : module, requête SQL, critères de sélection, limite d'âge pour les posts `publishing` bloqués. Voir TRANSVERSAL-3.

- [x] **[DB-5] [MAJEUR]** `daily_post_count` dans `scheduler_state` : aucune description de qui incrémente, qui remet à zéro, à quelle heure, et en quel timezone (UTC vs `Europe/Paris`).

- [x] **[DB-6] [MAJEUR]** Transition `approved → queued` dans `handle_approve` : la séquence exacte (vérification du compteur avant ou après passage en `approved` ? un ou deux commits ?) n'est pas documentée.

- [x] **[DB-7] [MAJEUR]** `onupdate=func.now()` ne fonctionne pas avec les UPDATEs directs SQL (plusieurs dans le code). La doc le reconnaît mais ne liste pas exhaustivement tous les endroits nécessitant un UPDATE explicite de `updated_at`.

- [x] **[DB-8] [MINEUR]** `job_cleanup` utilise `created_at` pour la rétention d'images plutôt que `published_at`. Un post créé lundi, publié vendredi peut voir son image nettoyée dès lundi+7j, soit 3 jours après publication — rendant `/retry_ig` impossible si l'image locale est nécessaire.

- [x] **[DB-9] [MINEUR]** Procédure Alembic pour la migration initiale non documentée step-by-step. Sans DB vide au moment du `alembic revision --autogenerate`, la migration peut être vide ou partielle.

- [x] **[DB-10] [MINEUR]** `PRAGMA foreign_keys = ON` avec `render_as_batch` : l'ordre d'activation du PRAGMA par rapport à `context.begin_transaction()` n'est pas précisé. Les migrations Alembic batch avec FK activées peuvent échouer si la séquence est incorrecte.

- [x] **[DB-11] [MINEUR]** Index manquant : pas d'index composite `(month, day, status, published_count)` sur `events`, qui est exactement le pattern de la requête critique exécutée à chaque cycle.

- [x] **[DB-12] [MINEUR]** Durée de vie container Instagram "< 24h côté Meta" non sourcée. Si Meta change cette politique, la logique de retry est silencieusement cassée.

- [x] **[DB-13] [INFO]** `server_default=text("'{}'")` pour `telegram_message_ids` : syntaxe à double niveau de guillemets fragile selon les versions SQLAlchemy. Ajouter un test unitaire de validation.

- [x] **[DB-14] [INFO]** `db backup` : non documenté si la commande utilise `sqlite3.Connection.backup()` ou `shutil.copy`. Un `shutil.copy` sur DB SQLite en mode WAL peut produire un fichier corrompu.

- [x] **[DB-15] [CRITIQUE]** Escalade niveaux 3 et 4 : `select_event` dans DATABASE.md ne prend pas `EffectiveQueryParams` en paramètre, mais c'est la structure qui porte les paramètres de déduplication window. Sans ce paramètre, les niveaux 3 et 4 sont inopérants. Voir TRANSVERSAL-1.

- [x] **[DB-16] [CRITIQUE]** `select_article` : requête SQL complète absente de DATABASE.md malgré l'affirmation que "le schéma SQL complet" y est défini. La requête RSS est renvoyée à DATA_SOURCES.md [DS-2.5] mais le doc se contredit lui-même.

- [x] **[DB-17] [MAJEUR]** `select_article` avec politique `"window"` : non documentée. Combinaison de `fetched_at <= :cutoff_delay` (contrainte RSS) et `last_used_at < :cutoff` (déduplication) jamais définie.

- [x] **[DB-18] [MINEUR]** Comportement de `published_count` sur `rss_articles` avec politique `"window"` ou `"always"` : non documenté. Peut-on republier un article RSS avec ces politiques ? L'intention n'est pas spécifiée.

---

## DATA_SOURCES.md

- [x] **[DS-1] [CRITIQUE]** Ordre de validation des champs RSS non spécifié : si la vérification de `published_parsed is None` est faite après le mapping ORM, un `IntegrityError: NOT NULL constraint failed` peut survenir. Documenter que la validation précède toute construction de `RssFeedItem`.

- [x] **[DS-2] [MAJEUR]** `Retry-After` peut être une date HTTP (RFC 7231) en plus d'un entier. Le parser qui attend uniquement un entier lève `ValueError` sur un 429 Wikimedia avec date HTTP.

- [x] **[DS-3] [MAJEUR]** Fusion FR+EN au niveau ≥ 2 : le même événement historique peut être inséré deux fois (une version FR, une version EN) avec des `content_hash` différents. L'application proposera le même événement dans deux langues sans filtre de déduplication inter-langues.

- [x] **[DS-4] [MAJEUR]** `URLError` non importée dans le snippet de gestion d'erreur RSS. `from urllib.error import URLError` est requis mais non spécifié.

- [x] **[DS-5] [MAJEUR]** Extraction `image_url` RSS (`"url"` vs `"href"` selon les versions feedparser) : le snippet Python exact n'est pas fourni. La version feedparser minimale n'est pas précisée. Ordre de test non défini.

- [x] **[DS-6] [MAJEUR]** Interface `RssFetcher` non conforme à `BaseFetcher` : `fetch_all(config)` prend `config` alors que `fetch(target_date)` de `WikipediaFetcher` n'en a pas besoin. Non documenté si `RssFetcher` hérite de `BaseFetcher`.

- [x] **[DS-7] [MAJEUR]** Marge 90j entre `max_age_days` et `min_delay_days` : un article collecté exactement à `max_age_days - 1` devient éligible dans `min_delay_days` jours, soit potentiellement 270 jours après sa publication. Ce cas limite n'est pas documenté comme acceptable ou aberrant.

- [x] **[DS-8] [MINEUR]** `feed_name` dans `RssFeedItem` : champ NOT NULL en DB, mentionné dans `fetch_all` mais la dataclass `RssFeedItem` n'est pas définie dans DATA_SOURCES.md. Renvoi vers ARCHITECTURE.md sans nom de section.

- [x] **[DS-9] [MINEUR]** Requête de comptage de stock [DS-1.4b] filtre `published_count = 0` de façon fixe, sous-estimant le stock réel si la politique active est `"window"` ou `"always"`.

- [x] **[DS-10] [MINEUR]** Comportement non documenté quand l'API EN (fallback) est aussi insuffisante (< `wikipedia_min_events`) : escalade déclenchée ? Warning ? Rien ?

- [x] **[DS-11] [MINEUR]** `prefetch_wikipedia` ignore le niveau d'escalade courant (toujours niveau 0). Un `--prefetch` ne résout pas un stock bas dû au sous-dimensionnement en FR. Non mentionné dans le CLI.

- [x] **[DS-12] [MINEUR]** Health check via `HEAD` sur l'endpoint Wikipedia "onthisday" : peut retourner 405 Method Not Allowed sur certaines configurations Wikimedia, faisant croire que l'API est KO alors qu'elle est fonctionnelle.

- [x] **[DS-13] [INFO]** `compute_content_hash` définie dans deux documents (DB et DS) sans vecteur de test canonique. Une divergence d'implémentation (ex: normalisation avant/après strip) produirait des doublons en DB sans erreur visible.

- [x] **[DS-14] [INFO]** `TYPE_TO_KEY` contient `"selected"` mais le CHECK du DDL sur `event_type` ne liste que `('event', 'birth', 'death', 'holiday')`. Soit `'selected'` est absent du CHECK (données incohérentes), soit il doit y être ajouté.

- [x] **[DS-15] [CRITIQUE]** Interface de `select_event` contradictoire avec DATA_SOURCES.md : le paramètre `EffectiveQueryParams` censé porter la politique de déduplication d'escalade est absent de la signature documentée dans DATABASE.md. Voir TRANSVERSAL-1.

- [x] **[DS-16] [MINEUR]** Les sources RSS "recommandées" (Le Monde, France Info, RFI) sont listées immédiatement avant un avertissement sur les CGU restrictives de ces mêmes médias. Contradiction éditoriale avec risque légal.

- [x] **[DS-17] [MINEUR]** `escalation reset` absente de l'index de commandes CLI et de la séquence d'initialisation SPEC.md [RF-3.6.3]. Un opérateur cherchant cette commande lors d'un incident ne la trouvera pas.

- [x] **[DS-18] [MINEUR]** Règle générale sur `asyncio.to_thread()` absente : seul `feedparser.parse()` est explicitement wrappé. Tout appel bloquant ajouté par un développeur sans connaître cette règle bloquera la boucle événementielle.

- [x] **[DS-19] [INFO]** `EffectiveQueryParams` dans `fetchers/base.py` : placement architecturalement surprenant (le module `fetchers` expose une abstraction de sélection/déduplication utilisée par `generator`). Compromis non justifié explicitement dans le doc.

- [x] **[DS-20] [MINEUR]** Comportement de `fetch_all` si un flux RSS sur N lève une erreur réseau générique (non `bozo`) : skip du flux avec continuation ? Exception propagée ? Non documenté.

---

## INSTAGRAM_API.md

- [x] **[IG-F1] [CRITIQUE]** Architecture `TokenManager` / sessions concurrentes : `InstagramPublisher` et `FacebookPublisher` s'exécutent en `asyncio.gather`. Si chacun instancie son propre `TokenManager`, les deux peuvent déclencher un refresh simultané. Si un seul `TokenManager` est partagé, les appels concurrents à `_save_token` créent une race condition. Non spécifié.

- [x] **[IG-F2] [CRITIQUE]** Estimation de 26 appels/heure inclut `/debug_token` qui n'est jamais défini dans le document ni dans `TokenManager`. Le comptage est incohérent avec le flux implémenté.

- [x] **[IG-F3] [CRITIQUE]** Guard `image_path NULL` absent de `_publish_approved_post` : un post `approved` avec `image_path` nettoyé par `job_cleanup` déclenche une exception non gérée lors de `recover_pending_posts`.

- [x] **[IG-F4] [MAJEUR]** Ratio 4:5 = 0.8 est la borne inférieure stricte du range API Meta (0.8–1.91). Si Meta traite cette borne comme exclusive, l'image est rejetée. Aucune marge de sécurité documentée.

- [x] **[IG-F5] [MAJEUR]** Publication de Stories (SPEC-7, v2) totalement absente de INSTAGRAM_API.md. Un développeur implémentant v2 n'a aucune référence technique pour `media_type=STORIES`.

- [x] **[IG-F6] [MAJEUR]** Deux champs pour le même mécanisme : `MetaToken.last_alert_days_threshold` (INSTAGRAM_API.md) vs `scheduler_state.token_alert_level` (TELEGRAM_BOT.md). Voir TRANSVERSAL-4.

- [x] **[IG-F7] [MAJEUR]** `cmd_auth_meta` non idempotente : si le crash survient après l'échange OAuth mais avant le commit DB, l'UPSERT peut laisser `meta_tokens` dans un état indéfini. Comportement non précisé.

- [x] **[IG-F8] [MAJEUR]** Seuil de renouvellement anticipé : Meta peut refuser le refresh si `days_remaining > 30`. Un échec à J-7 est "absorbé silencieusement" sans vérification que `expires_at` a progressé. L'utilisateur est notifié trop tard (J-3).

- [x] **[IG-F9] [MAJEUR]** Container `IN_PROGRESS` depuis > 2 minutes : `_wait_for_container_ready` lève `PublisherError("Container Meta bloqué")`. Le container expirant après 24h, le post reste bloqué en `error` pendant 24h avant qu'un retry réussisse. `_get_or_create_container` ne force pas la recréation si le container existant est bloqué.

- [x] **[IG-F10] [MAJEUR]** Si `instagram.enabled: false` et `facebook.enabled: true`, `check_and_increment_daily_count` ne s'applique pas. Comportement non documenté.

- [x] **[IG-F11] [MINEUR]** Version API `v21.0` hardcodée dans tous les exemples curl sans note indiquant que la valeur réelle est `config.instagram.api_version`.

- [x] **[IG-F12] [MINEUR]** Scope `instagram_creator_manage_content` présent dans `SCOPES` de [IG-6] mais absent de l'URL d'autorisation de [IG-2.1]. Comptes Créateur échouent si le développeur construit l'URL depuis [IG-2.1].

- [x] **[IG-F13] [MINEUR]** `auth meta` ne précise pas si le Page Access Token est **toujours** régénéré ou seulement si absent. Un développeur pourrait optimiser pour ne pas le régénérer, laissant un token invalidé.

- [x] **[IG-F14] [INFO]** Comportement si les deux plateformes sont désactivées simultanément (`instagram.enabled: false` ET `facebook.enabled: false`) : le post passe-t-il en `published` ou `error` ? Non documenté.

---

## TELEGRAM_BOT.md

- [x] **[TG-F1] [CRITIQUE]** État `queued` en v1 sans sortie : la notification Telegram indique à l'utilisateur d'utiliser `/retry` pour un post `queued`, mais `/retry` n'opère que sur les posts `status='error'`. L'utilisateur est désorienté et le post reste bloqué. Voir TRANSVERSAL-2.

- [x] **[TG-F2] [CRITIQUE]** `recover_pending_posts` : signature complète absente de TELEGRAM_BOT.md. Comment la fonction obtient-elle `AsyncSession`, `Bot`, et `Config` ? Voir TRANSVERSAL-3.

- [x] **[TG-F3] [CRITIQUE]** Transition `queued → publishing` non documentée pour le cas où l'utilisateur utilise `/retry` sur un post `queued` : `/retry` ne traite que `status='error'`. Doublon avec TG-F1 mais côté handler. Voir TRANSVERSAL-2.

- [x] **[TG-F4] [CRITIQUE]** `handle_edit_timeout` : pour appeler `bot.edit_message_caption` vs `bot.edit_message_text`, le handler doit connaître le type du message original (photo vs texte). Ce type n'est pas précisé comme stocké dans `context.chat_data`. Implémentation impossible sans cette information.

- [x] **[TG-F5] [CRITIQUE]** `cmd_force` bypass `max_pending_posts` + race condition sur `check_and_increment_daily_count` : si deux approbations arrivent simultanément (double-clic, multi-admins), les deux lectures du compteur peuvent précéder les deux écritures, dépassant `max_daily_posts`. Non spécifié si `check_and_increment_daily_count` est protégé par `asyncio.Lock` ou par une mise à jour SQL conditionnelle.

- [x] **[TG-F6] [MAJEUR]** Race condition sur `/retry` : si le retry a uploadé `image_public_url` mais crashé avant le commit, l'état DB est inconsistant. Non documenté si `upload_image` est idempotent (même fichier → même URL sans erreur).

- [x] **[TG-F7] [MAJEUR]** `/pending` n'inclut pas de lien `t.me/c/{chat_id}/{message_id}` vers le message d'approbation original. UX dégradée avec `max_pending_posts > 1`.

- [x] **[TG-F8] [MAJEUR]** Pattern de gestion des sessions SQLAlchemy dans les handlers PTB non défini : injection via `context.bot_data` ? Ouverture locale ? Factory globale ? Voir TRANSVERSAL-6.

- [x] **[TG-F9] [MAJEUR]** `send_approval_request` avec `authorized_user_ids` vide : boucle sans itération, `telegram_message_ids = {}`, post bloqué `pending_approval` indéfiniment. Validation au démarrage non confirmée dans TELEGRAM_BOT.md.

- [x] **[TG-F10] [MAJEUR]** Multi-admins : après approbation par l'admin A, les boutons "Publier / Rejeter" restent actifs dans les messages des admins B, C, etc. `handle_approve` n'édite que le message de l'admin ayant cliqué. Non documenté comme limitation connue.

- [x] **[TG-F11] [MAJEUR]** Troncature légende à 1024 chars pour Telegram `send_photo` : si `handle_new_caption` écrit la version tronquée en DB, la légende Instagram publiée est aussi limitée à 1024 chars alors que l'API Instagram supporte 2200.

- [x] **[TG-F12] [MAJEUR]** `job_check_expired` : si l'utilisateur a supprimé le message Telegram, la notification "Post expiré" ne contient pas l'extrait de légende permettant d'identifier le post.

- [x] **[TG-F13] [MINEUR]** `cmd_stats` : division par zéro si `published = 0` et `rejected = 0` (instance fraîche). Pas de garde documentée.

- [x] **[TG-F14] [MINEUR]** Comportement du `ConversationHandler` après timeout/annulation puis re-clic sur "Modifier" : le re-clic devrait relancer la conversation via l'entry_point mais ce comportement PTB n'est pas confirmé dans le document.

- [x] **[TG-F15] [MINEUR]** `notify_all` avec admin inaccessible (bot bloqué) : `send_with_retry` retente 5 fois avec backoff exponentiel (~160 secondes). Peut bloquer le scheduler si `notify_all` est appelé dans un contexte APScheduler avec un timeout.

- [x] **[TG-F16] [MINEUR]** `/retry_ig` et `/retry_fb` : comportement si aucun post éligible n'est trouvé (requête retourne `None`) non documenté. Message d'absence à spécifier.

- [x] **[TG-F17] [INFO]** Ordre exact de `bot_app.initialize()`, `bot_app.start()`, `recover_pending_posts()`, `bot_app.run_polling()` non précisé. Si `recover_pending_posts` envoie des messages avant `bot_app.start()`, les envois échouent.

- [x] **[TG-F18] [INFO]** `ConversationHandler` avec `per_user=True, per_chat=False` : comportement pour un admin sur deux appareils (deux chats, même `user_id`) non documenté comme comportement intentionnel.

---

## SCHEDULER.md

- [x] **[SC-1] [CRITIQUE]** `check_and_increment_daily_count(engine: AsyncEngine)` : ambiguïté sur quel engine est passé (`ancnouv.db` async vs `scheduler.db` sync). Les deux engines coexistent et la distinction n'est jamais nommée explicitement dans SCHEDULER.md.

- [x] **[SC-2] [CRITIQUE]** Vérification de `publications_suspended` hors verrou exclusif : si JOB-5 écrit `publications_suspended` simultanément, la race condition peut laisser passer une publication malgré le token expiré.

- [x] **[SC-3] [MAJEUR]** Clé de config `image_retention_days` : `scheduler.image_retention_days` (défaut 30j) dans SCHEDULER.md vs `content.image_retention_days` (défaut 7j) dans IMAGE_GENERATION.md vs SPEC.md [RF-3.4.8] (défaut 7j). Voir TRANSVERSAL-5.

- [x] **[SC-4] [MAJEUR]** `recover_pending_posts` pour posts `approved` avec `image_public_url` non-NULL : aucune vérification de `instagram_post_id IS NULL` / `facebook_post_id IS NULL` avant retry. Double publication Instagram possible si l'app a publié mais n'a pas commité.

- [x] **[SC-5] [MAJEUR]** Comportement APScheduler pour les jobs `interval` avec `misfire_grace_time=300` et `coalesce=True` contradictoire avec la note SC-C1 (déclenchement immédiat si délai > 6h). Les deux comportements ne sont pas réconciliés.

- [x] **[SC-6] [MAJEUR]** Timezone absente pour JOB-2 (interval) : contrairement à JOB-1 et JOB-3 qui précisent `config.scheduler.timezone`, JOB-2 ne mentionne pas de timezone. `AsyncIOScheduler` doit être configuré avec la timezone au niveau de l'instance.

- [x] **[SC-7] [MAJEUR]** `job_check_token` : `MetaToken.last_alert_days_threshold` de type non précisé, comparaison sur `0` (falsy en Python) risquée, et redondance avec `scheduler_state.token_alert_level`. Voir TRANSVERSAL-4.

- [x] **[SC-8] [MAJEUR]** `/force` interagit avec APScheduler mais la séquence n'est pas documentée dans SCHEDULER.md : appel direct de `job_generate()` ? `scheduler.modify_job` ? `/force` est-il bloqué si `paused=True` ? Respecte-t-il `max_pending_posts` ?

- [x] **[SC-9] [MAJEUR]** Posts `pending_approval` renvoyés au redémarrage par `recover_pending_posts` peuvent être chronologiquement expirés (> 48h) mais pas encore marqués `expired` (JOB-4 ne tourne pas avant H+1). Un clic "Publier" sur un tel post produit un comportement indéfini.

- [x] **[SC-10] [MAJEUR]** `recover_pending_posts` re-envoie uniquement les posts dont `telegram_message_ids == {}`, mais pas les posts dont `telegram_message_ids` est partiellement rempli (certains admins ont reçu le message, d'autres non après un crash partiel).

- [x] **[SC-11] [MINEUR]** JOB-1 utilise `get_effective_query_params` sans renvoi vers DATA_SOURCES.md. La correspondance `escalation_level → paramètres` n'est pas définie dans SCHEDULER.md.

- [x] **[SC-12] [MINEUR]** `coalesce=True` a des comportements différents pour les jobs `cron` et `interval`. Ce comportement asymétrique n'est pas explicité dans la note SC-C1.

- [x] **[SC-13] [MINEUR]** Reset `daily_post_count` à minuit UTC (1h ou 2h Paris) : la limite Meta est une fenêtre glissante 24h, pas un compteur calendaire. Utiliser UTC pour le reset peut introduire un dépassement si Meta utilise une autre référence.

- [x] **[SC-14] [MINEUR]** Comportement de `handle_approve` pendant une pause scheduler non précisé : les approbations manuelles Telegram sont-elles bloquées ou toujours opérationnelles ? SPEC.md ne clarifie pas l'impact de la pause sur les approbations.

- [x] **[SC-15] [INFO]** JOB-7 commenté mais des posts peuvent atteindre `queued` en v1. La procédure de récupération documentée (UPDATE SQL direct) ne peut pas être effectuée en production sans accès direct à SQLite. Aucune commande CLI de secours.

- [x] **[SC-16] [INFO]** Séquence `main_async` : `recover_pending_posts` peut tenter de re-publier des posts avec `image_public_url` en mode `backend=remote`. La contrainte "URL d'images accessibles avant de re-envoyer les posts" varie selon le backend mais n'est pas documentée comme telle.

---

## IMAGE_GENERATION.md

- [x] **[IMG-1] [CRITIQUE]** Templates par époque [SPEC-7bis] totalement absents : aucun mapping époque → palette, aucune police alternative, aucune disposition spécifique, aucun mécanisme de sélection du template, aucune signature de fonction. SPEC-7bis est non-implémentable à partir de IMAGE_GENERATION.md.

- [x] **[IMG-2] [CRITIQUE]** Incohérence `_draw_event_text(max_height)` : le tableau des dimensions dit "~18 lignes" comme limite, la description dit "~22 lignes théoriques, ~18 effectives". Un développeur peut sur-contraindre le wrapping en croyant que 18 est une limite hard.

- [x] **[IMG-3] [CRITIQUE]** Zone footer : `_draw_footer` positionne le texte à `y = H - 60 = 1290`, la zone est documentée y=1250–1330, mais le diviser est à y=1230. Le centrage vertical dans la zone footer n'est pas cohérent entre les sections.

- [x] **[IMG-4] [MAJEUR]** Masthead Mode B : le document ne précise pas si le masthead "ANCIENNES NOUVELLES" est toujours identique pour Mode A et Mode B, ni si le champ `image.masthead_text` est configurable.

- [x] **[IMG-5] [MAJEUR]** Traitement thumbnail "ratio carré" : la note d'exemple cite une image "proches de 3.3:1" qui est hors de la plage carré (0.7–1.5). L'exemple est incohérent avec les seuils définis.

- [x] **[IMG-6] [MAJEUR]** `_draw_event_text` avec thumbnail : `text_y=600` est codé en dur indépendamment de la hauteur réelle du thumbnail rendu. Si `_draw_thumbnail` ne garantit pas un clip strict à y=560, le texte peut chevaucher l'image.

- [x] **[IMG-7] [MAJEUR]** `format_caption_rss` ne contient pas la formule temporelle (ex: "Il y a 3 mois, le 21 décembre 2025"). SPEC.md [SPEC-2.3] l'exige. La légende Mode B ne respecte pas la spec telle que documentée.

- [x] **[IMG-8] [MAJEUR]** Numérotation en doublon à l'étape 4 de `_generate_image_inner` : deux étapes portent le numéro 4. La logique de légende (`format_caption`) appartient à `generate_post` mais est insérée dans la séquence de `_generate_image_inner`, créant une confusion de frontières entre fonctions.

- [x] **[IMG-9] [MAJEUR]** Dépendance `httpx` : le document ne précise pas si l'import est au niveau module (fail-fast au démarrage si absent) ou dans le corps de `fetch_thumbnail` (fail silencieux → toujours mode typographique).

- [x] **[IMG-10] [MAJEUR]** `_draw_masthead` : deux méthodes de centrage documentées (`anchor="mt"` OU calcul manuel `(W - textwidth) // 2`) sans que l'une soit désignée comme référence. Résultats légèrement différents en raison des descenders.

- [x] **[IMG-11] [MAJEUR]** `_draw_date_banner` : deux lignes (40px + 32px) dans une zone de 70px. `time_ago` à y=185 avec police 40px se termine à ~y=225–235, chevauchant `date_str` à y=225. Chevauchement de texte possible.

- [x] **[IMG-12] [MINEUR]** `wrap_text` : comportement pour un mot seul dépassant `max_width` pixels non spécifié. Débordement visuel ? Coupure ? Troncature ?

- [x] **[IMG-13] [MINEUR]** `LibreBaskerville-Italic.ttf` marquée "non utilisée activement en v1" mais incluse dans `_load_fonts`. Non précisé si l'absence du fichier génère un WARNING ou est silencieusement ignorée.

- [x] **[IMG-14] [MINEUR]** Pas de vérification globale que la légende complète (formule + texte 300 chars + hashtags configurables + source) reste sous les 1024 chars de `send_photo` Telegram.

- [x] **[IMG-15] [MINEUR]** `RssArticle.image_url` : le document affirme que le champ est présent sur les deux types mais ne confirme pas si le champ est nullable. `fetch_thumbnail("")` (chaîne vide) vs `fetch_thumbnail(None)` peuvent avoir des comportements différents.

- [x] **[IMG-16] [MINEUR]** `_draw_paper_texture(intensity=8)` : la valeur par défaut dans la signature n'est utilisée que si la fonction est appelée directement (tests). La valeur effective vient de `config.image.paper_texture_intensity` dont la valeur par défaut n'est pas rappelée dans IMAGE_GENERATION.md.

- [x] **[IMG-17] [INFO]** Diagramme ASCII : l'espace entre le bord image et le contenu représente `MARGIN=20` mais le texte commence à `PADDING=40`. Le diagramme peut induire en erreur sur les coordonnées réelles.

- [x] **[IMG-18] [INFO]** `GeneratorError` mentionnée comme exception levée en cas d'échec Pillow mais son module de définition n'est pas précisé dans IMAGE_GENERATION.md. Un développeur travaillant uniquement sur `generator/image.py` ne sait pas où l'importer.

---

## CONFIGURATION.md

- [x] **[CONF-01] [MINEUR]** `jpeg_quality : le=95` interdit la valeur 100 sans justification documentée. Un utilisateur saisissant `100` obtient une erreur Pydantic sans explication.

- [x] **[CONF-02] [MINEUR]** `deduplication_window_days` sans contrainte `ge=1` : une valeur `0` ou négative rend la politique `window` identiquement comportementale à `always` de façon silencieuse.

- [x] **[CONF-03] [MINEUR]** `prefetch_days` et `image_retention_days` sans contrainte `ge=1` : valeur `0` pour `prefetch_days` casse la pré-collecte ; valeur `0` pour `image_retention_days` purge les images immédiatement.

- [x] **[CONF-04] [MINEUR]** `low_stock_threshold` sans contrainte documentée : une valeur `0` désactiverait silencieusement l'escalade.

- [x] **[CONF-05] [MINEUR]** `backup_keep` sans contrainte `ge=1` : une valeur `0` supprimerait tous les backups immédiatement après création.

- [x] **[CONF-06] [MINEUR]** `notification_debounce` sans contrainte `ge=0` : une valeur négative serait silencieusement acceptée.

- [x] **[CONF-07] [MINEUR]** `approval_timeout_hours` : contrainte `ge=1` uniquement, sans borne haute. Une valeur `8760` (1 an) rendrait RF-3.3.3 inapplicable.

- [x] **[CONF-08] [MAJEUR]** `RssConfig` : aucun `@model_validator` vérifiant `min_delay_days < max_age_days`. Si `min_delay_days > max_age_days`, aucun article ne serait jamais éligible — Mode B silencieusement cassé.

- [x] **[CONF-09] [MAJEUR]** `validate_image_hosting` : la valeur recommandée pour le dev (`"https://dev.example.com:8765"`) contient `"example"`, qui est dans la liste de rejet. Contradiction directe : la valeur recommandée est rejetée par le validator.

- [x] **[CONF-10] [MINEUR]** `validate_image_hosting` : ambiguïté sur `localhost` — "dans la liste de rejet" mais "l'exception n'est pas implémentée en v1". Le développeur ne sait pas si `localhost` est ou non rejeté.

- [x] **[CONF-11] [INFO]** `api_version` absent de `FacebookConfig` mais `FacebookPublisher` l'utilise. Comment `FacebookPublisher` obtient-il sa version ? Depuis `config.instagram.api_version` ? Codée en dur ?

- [x] **[CONF-12] [INFO]** `RssFeedConfig.url` est de type `str` sans validation de format URL. Une URL mal formée n'est détectée qu'à la première collecte RSS.

- [x] **[CONF-13] [MINEUR]** `config.yml.example` décrit comme "référence canonique" mais son contenu n'est pas reproduit dans CONFIGURATION.md. Toute divergence entre les deux est indétectable depuis le doc.

- [x] **[CONF-14] [MAJEUR]** `image_hosting.public_base_url` avec `default=""` mais marqué "obligatoire" : le caractère obligatoire est enforced par un validator model-level, pas par `Field(...)`. Trompeur pour un développeur qui s'attend à une erreur Pydantic de champ manquant.

- [x] **[CONF-15] [INFO]** `authorized_user_ids` validé dans `validate_meta` (qui concerne Meta) plutôt que dans un `validate_telegram` dédié. Incohérence nominale.

- [x] **[CONF-16] [MINEUR]** `validate_cron` utilise `CronTrigger.from_crontab()` d'APScheduler dans `config.py`. Cette dépendance croisée (config → scheduler) n'est mentionnée ni dans ARCHITECTURE.md ni dans les deps.

---

## ARCHITECTURE.md

- [x] **[ARCH-01] [CRITIQUE]** Aucune liste de dépendances Python avec versions. APScheduler 3.x vs 4.x, PTB 13.x vs 20.x, SQLAlchemy 1.x vs 2.x ont des APIs incompatibles. Un développeur installant les dernières versions peut tout casser. Voir TRANSVERSAL-7.

- [x] **[ARCH-02] [CRITIQUE]** Version Python requise (3.12+ selon SPEC C-4.1.2) non confirmée dans ARCHITECTURE.md.

- [x] **[ARCH-03] [MAJEUR]** Terminologie `RssFeedItem` (dataclass transport) vs `RssArticle` (ORM) vs `rss_articles` (table SQL) : le document le mentionne mais n'ajoute pas de lexique centralisé. Risque de confusion lors de l'implémentation.

- [x] **[ARCH-04] [MAJEUR]** `job_fetch_wiki` heure fixe "2h" non configurable et non documentée comme telle. Pas de paramètre de config pour les horaires des jobs système. Non précisé si c'est intentionnel.

- [x] **[ARCH-05] [MAJEUR]** Fréquence `job_fetch_rss` "toutes les 6h" (SPEC RF-3.1.3) non configurable sans modification du code. Incohérence avec `generation_cron` qui est configurable.

- [x] **[ARCH-06] [MAJEUR]** `job_cleanup` présent dans le diagramme MAINTENANCE sans définition : que nettoie-t-il exactement ? À quelle fréquence ? Renvoi à SCHEDULER.md qui peut ne pas être encore rédigé.

- [x] **[ARCH-07] [MAJEUR]** `recover_pending_posts` : comportement pour les posts `publishing` au redémarrage (crash mid-publish) non spécifié. Voir TRANSVERSAL-3.

- [x] **[ARCH-08] [MAJEUR]** En v1, un post qui atteint `queued` (limite journalière) lors de `_publish_approved_post` reste bloqué car JOB-7 est commenté. La note le reconnaît mais ne documente ni le comportement utilisateur attendu ni la commande de déblocage. Voir TRANSVERSAL-2.

- [x] **[ARCH-09] [MINEUR]** Signature de `get_effective_query_params()` dans `generator/selector.py` non documentée (types de paramètres, type de retour).

- [x] **[ARCH-10] [MINEUR]** `format_caption_rss` mentionnée dans la génération hybride mais absente des interfaces définies dans la section "Interfaces entre composants".

- [x] **[ARCH-11] [MINEUR]** `generate_image(source, config, ...)` : la notation `...` dans les paramètres est insuffisante pour l'implémentation. Si IMAGE_GENERATION.md n'est pas encore complet, c'est un trou de spec.

- [x] **[ARCH-12] [MINEUR]** `get_scheduler_state` et `set_scheduler_state` dans `db/utils.py` définies sans signature (aucun type de retour, aucun paramètre).

- [x] **[ARCH-13] [MINEUR]** Mécanisme d'escalade mentionné dans `scheduler_state.escalation_level` sans résumé minimal. Un développeur travaillant sur `job_fetch_wiki` seul ne comprend pas `increment_escalation_level`.

- [x] **[ARCH-14] [INFO]** APScheduler sans version spécifiée. APScheduler 4.x a supprimé `SQLAlchemyJobStore` et `AsyncIOScheduler` de la même façon. Si un développeur installe APScheduler 4.x, `ImportError` garanti. Voir TRANSVERSAL-7.

- [x] **[ARCH-15] [INFO]** `data/scheduler.db` chemin fixe : non précisé si ce chemin est relatif à `config.data_dir` ou au CWD. Si `data_dir` est personnalisé, `scheduler.db` peut se retrouver ailleurs.

- [x] **[ARCH-16] [MINEUR]** `logs/` répertoire fixe non créé automatiquement au démarrage. Si le répertoire n'existe pas, `FileHandler` crash. Absent de la séquence d'initialisation `db init → setup fonts → ...`.

- [x] **[ARCH-17] [MINEUR]** Pattern `BEGIN EXCLUSIVE` + `AUTOCOMMIT` avec SQLAlchemy 2.x async + aiosqlite : présenté comme "Pattern exact requis" sans préciser les versions exactes sur lesquelles il a été validé. Peut générer `OperationalError` selon les versions.

- [x] **[ARCH-18] [INFO]** `asyncio.ensure_future` dans le handler SIGTERM : déprécié en faveur de `asyncio.create_task` depuis Python 3.7. Peut générer `DeprecationWarning` en Python 3.12.

- [x] **[ARCH-19] [MINEUR]** Schéma DB entièrement délégué à DATABASE.md sans résumé minimal des colonnes critiques dans ARCHITECTURE.md. Navigation constante entre deux docs nécessaire.

- [x] **[ARCH-20] [MAJEUR]** Aucune section "Setup de l'environnement de développement" : pas de `venv`, pas d'installation des dépendances, pas de commandes pour lancer les tests. Un développeur qui démarre doit chercher dans 3–4 documents différents.

- [x] **[ARCH-21] [MAJEUR]** Hiérarchie des exceptions manque `DatabaseError` : un `OperationalError` SQLAlchemy (DB inaccessible) remonterait sans être catchée proprement. Cas "DB inaccessible → Arrêt immédiat" non représenté.

- [x] **[ARCH-22] [INFO]** `run_image_server` : comportement en cas de port déjà utilisé (`EADDRINUSE`) non documenté. En Docker avec deux instances, exception aiohttp non catchée.

---

## DEPLOYMENT.md

- [x] **[D-01] [CRITIQUE]** Contradiction C-4.1.5 vs réalité : SPEC déclare "fonctionner derrière NAT (pas de port entrant requis par défaut)" mais DEPLOYMENT.md indique qu'un déploiement entièrement derrière NAT n'est pas supporté pour la publication Meta. SPEC.md doit être amendée pour clarifier que cette contrainte ne s'applique qu'au bot Telegram (polling sortant), pas au serveur d'images.

- [x] **[D-02] [CRITIQUE]** Contrainte architecturale cachée : `publisher/__init__.py` ne doit PAS contenir d'imports top-level de `InstagramPublisher` ou `FacebookPublisher`. Mentionnée uniquement dans un commentaire Dockerfile, absente d'ARCHITECTURE.md.

- [x] **[D-03] [MAJEUR]** Séquence d'initialisation systemd inverse `setup fonts` et `db init` par rapport à l'ordre canonique de CLI.md. Confusion documentaire.

- [x] **[D-04] [MAJEUR]** Polices dans Docker : `COPY assets/ ./assets/` + volume `./assets:/app/assets:rw` + `.gitignore` excluant `assets/fonts/` = scénario probable où les polices sont absentes à la fois dans l'image et sur l'hôte. `setup fonts` doit être lancé comme étape Docker run explicite mais n'apparaît pas dans la séquence numérotée.

- [x] **[D-05] [MAJEUR]** Architecture Raspberry Pi + VPS (tunnel SSH permanent) mentionnée dans SPEC.md comme option valide mais totalement absente de DEPLOYMENT.md.

- [x] **[D-06] [MAJEUR]** Dockerfile ne copie pas `alembic.ini` ni `alembic/versions/`. `db init` et `db migrate` échouent dans le conteneur.

- [x] **[D-07] [MAJEUR]** `ancnouv-notify@.service` : si `TELEGRAM_CHAT_ID` est absent du `.env`, le script curl envoie une requête avec `chat_id=` vide. L'erreur est silencieuse — l'opérateur croit que les notifications crash fonctionnent.

- [x] **[D-08] [MAJEUR]** Crontab de sauvegarde : `sqlite3 data/scheduler.db "VACUUM INTO ..."` suppose que `sqlite3` est installé sur l'hôte. Sur un VPS minimal (Debian slim), ce n'est pas le cas.

- [x] **[D-09] [MINEUR]** Libellé du composant `health` incohérent : "Token Meta" dans CLI.md vs "Token Instagram" dans DEPLOYMENT.md.

- [x] **[D-10] [MINEUR]** Section renouvellement token : ne précise pas que le refresh automatique est tenté à J-7 et que les publications sont suspendues à J-1 si tous les essais ont échoué.

- [x] **[D-11] [MINEUR]** Procédure de rollback Alembic uniquement pour Docker. Équivalent systemd absent.

---

## CLI.md

- [x] **[C-01] [CRITIQUE]** Commandes Telegram bot (`/retry`, `/retry_ig`, `/retry_fb`, `/force`, `/pending`, `/stats`, `/pause`, `/resume`, `/status`, `/help`, `/queue`) non documentées dans CLI.md. SPEC.md [RF-3.3.6] les liste mais sans référence vers TELEGRAM_BOT.md depuis CLI.md. `/force` en particulier : son interaction avec `max_pending_posts`, mode `auto_publish`, état `paused` n'est documentée nulle part.

- [x] **[C-02] [MAJEUR]** `fetch` sans `--prefetch` : comportement sur erreur réseau avec cache existant non spécifié. Données stale utilisées ? Notification ? RF-3.1.5 mentionne le mode offline mais sans lien avec la commande `fetch`.

- [x] **[C-03] [MAJEUR]** `generate-test-image` : cas "token absent alors que `instagram.enabled: true`" non documenté. Code de retour `1` avec message guidant vers `auth meta` ? Non précisé.

- [x] **[C-04] [MAJEUR]** `health` : composant "serveur images" absent de la liste des composants vérifiés. DEPLOYMENT.md l'inclut dans le mock de sortie. Comment est-il vérifié en mode `backend=local` sans démarrer le scheduler ?

- [x] **[C-05] [MAJEUR]** `start` en mode `backend=local` : comportement si le port est déjà occupé non documenté. Échec code `1` avant ou après démarrage du bot Telegram et d'APScheduler ?

- [x] **[C-06] [MAJEUR]** `auth meta` sur VPS Docker : les étapes (tunnel SSH en Terminal 1, docker run en Terminal 2) ne sont pas numérotées dans CLI.md contrairement à DEPLOYMENT.md. L'ordre d'exécution n'est pas rendu explicite.

- [x] **[C-07] [MINEUR]** `db backup` : non précisé dans la doc de la commande que `VACUUM INTO` est sûr pendant que `start` tourne. Information présente uniquement dans les commentaires cron de DEPLOYMENT.md.

- [x] **[C-08] [MINEUR]** `db status` : tableau principal dit "toujours `0` si la DB est accessible" mais note [CLI-M7] corrige immédiatement en disant que c'est incorrect pour le cas DB inaccessible. Le tableau lui-même est erroné.

- [x] **[C-09] [MINEUR]** `images-server` : comportement côté client (`ancnouv`) quand le serveur d'images retourne `HTTP 400` non spécifié. Retry ? Post en `error` ? Non documenté.

- [x] **[C-10] [INFO]** `escalation reset` : comment "enrichir manuellement la DB d'événements" (l'usage recommandé avant de reset) n'est documenté nulle part. Pas de commande CLI d'import, pas de format d'import.

---

## TESTING.md

- [x] **[T-01] [CRITIQUE]** Tests CLI exclus délibérément ("orchestrateurs minces") mais `_dispatch_inner` contient une logique non triviale (deux groupes de commandes, `asyncio.run()`, `SystemExit(2)`). Aucun smoke test documenté pour détecter une régression dans le dispatch.

- [x] **[T-02] [CRITIQUE]** Chemin de patch `notify_all` pour `test_recover_pending_posts.py` non documenté (trois chemins différents selon les fichiers de test, le troisième non précisé). Faux positifs si le chemin est incorrect.

- [x] **[T-03] [CRITIQUE]** Seuils d'alerte expiration token : seul J-7 est testé dans `test_job_check_token_sends_alert_at_threshold`. Les seuils J-30, J-14, J-3, J-1 définis dans RF-3.4.5 ne sont pas couverts.

- [x] **[T-04] [CRITIQUE]** RF-3.3.3 (post expiré → notification + event reste `available`) non testé. Aucun test ne vérifie que la notification Telegram est envoyée ni que `event.status` reste `available` après expiration.

- [x] **[T-05] [MAJEUR]** Fixture `db_article` hardcode `91j` (`min_delay_days + 1`). Si un test surcharge `min_delay_days > 90`, la fixture retourne un article non éligible et le test échoue de façon cryptique.

- [x] **[T-06] [MAJEUR]** Flux complet `handle_skip` → `generate_post` → `send_approval_request` non testé. `test_handle_skip_generates_next` vérifie que `generate_post` est appelé mais pas que `send_approval_request` est ensuite appelé avec le nouveau post.

- [x] **[T-07] [MAJEUR]** `SystemExit(2)` depuis argparse non testé (tests CLI exclus). Une régression `except Exception` au lieu de `except BaseException` dans `_dispatch` laisse des tracebacks bruts fuiter en production.

- [x] **[T-08] [MAJEUR]** RF-3.2.7 (`max_pending_posts`) non couvert : aucun test pour le cas où `pending_count >= max_pending_posts` → `generate_post` retourne `None`.

- [x] **[T-09] [MAJEUR]** Politiques de déduplication `window` et `always` non testées en end-to-end via `generate_post()`. Tests de `select_event` unitaires uniquement.

- [x] **[T-10] [MAJEUR]** Ambiguïté de localisation du test de récupération des posts `publishing` au démarrage : documenté dans `test_post_lifecycle.py` mais le module responsable (`recover_pending_posts` vs job séparé) n'est pas précisé.

- [x] **[T-11] [MAJEUR]** `test_handle_approve_daily_limit_queues` vérifie `post.status == "queued"` alors que `queued` est un statut v2 (SPEC-7ter). Périmètre ambigu : un développeur implémentant strictement v1 sans statut `queued` fera échouer ce test.

- [x] **[T-12] [MINEUR]** Seuil de couverture `--cov-fail-under=80` global. Un module critique (cible 90%) peut descendre à 60% sans déclencher d'échec si d'autres modules compensent.

- [x] **[T-13] [MINEUR]** `feedparser` dans `requirements-dev.txt` alors qu'il est déjà dans `requirements.txt` (dépendance prod). Redondance potentiellement source de conflits de versions.

- [x] **[T-14] [MINEUR]** Environnement de staging : procédure d'isolation incomplète. Comment séparer staging de production (deux `config.yml` ? variable d'environnement ?) non documenté.

- [x] **[T-15] [MINEUR]** `db_engine` et `db_engine_static` appellent toutes deux `set_engine` : si un test utilise les deux simultanément, la seconde écrase la `_session_factory` de la première. Contrainte d'exclusion mutuelle non documentée.

- [x] **[T-16] [INFO]** Aucune intégration CI documentée. La couverture n'est vérifiée que manuellement. Aucun exemple de workflow GitHub Actions ou GitLab CI.

---

*Fin de l'audit — 163 findings, dont 26 critiques et 71 majeurs.*
