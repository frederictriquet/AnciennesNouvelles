# Anciennes Nouvelles — Spécification Fonctionnelle

> Document de référence. Toute implémentation doit référencer les blocs [SPEC-X.Y] correspondants.

> **Structure du document :** SPEC-1 à SPEC-5 couvrent le périmètre v1. SPEC-6 (Glossaire) est placé en dernier par convention pour faciliter la référence rapide. SPEC-7, SPEC-7bis et SPEC-7ter couvrent les fonctionnalités v2 (Stories, templates par époque, file d'attente). SPEC-8 et SPEC-9 sont des études de faisabilité pour des fonctionnalités v3+ (Reels, Threads, Carrousel, BnF/Gallica), sans date d'implémentation planifiée.

---

## [SPEC-1] Vision

**Anciennes Nouvelles** est une application Python autonome qui alimente simultanément un compte Instagram et une Page Facebook avec des publications de type "retour dans le temps". Chaque post présente un événement historique ou une actualité passée avec la formule narrative : *"Il y a X ans/mois, le [date], il s'est passé..."*.

L'application fonctionne en mode semi-automatique : génération automatique des posts, validation manuelle via Telegram, puis publication automatique sur Instagram et Facebook.

---

## [SPEC-2] Concept éditorial

### [SPEC-2.1] Deux modes de contenu

**Mode A — Anniversaires historiques** (mode principal)

- Un événement survenu un jour identique (même MM/JJ) dans une année passée.
- Exemple : aujourd'hui est le 21 mars 2026 → post sur un événement du 21 mars 2016, ou 1871, ou 1453.
- Source : Wikipedia "On This Day" API (gratuit, profondeur illimitée).
- Formule : *"Il y a 10 ans, le 21 mars 2016 : [événement]"*

**Mode B — Actualités retardées** (mode optionnel, activable en config)

- Une actualité collectée via RSS et republiée avec un délai configuré.
- Exemple : article RSS collecté le 21 décembre 2025 → publié le 21 mars 2026.
- Source : flux RSS de médias francophones.
- Formule : *"Il y a 3 mois, le 21 décembre 2025 : [titre de l'article]"*
- Contrainte : ce mode ne produit du contenu qu'après X mois de collecte continue. L'app doit tourner longtemps avant d'avoir du stock pour ce mode.
- Délai minimum configurable (par défaut : 3 mois).
- **Limitation fondamentale** : si l'application est arrêtée pendant 2 semaines, les articles de cette période sont définitivement perdus. Le Mode B ne publie que "ce qui a été collecté il y a 3 mois", pas "ce qui s'est passé il y a 3 mois". En cas d'arrêt prolongé, le stock Mode B comporte des lacunes temporelles. Ce n'est pas un bug — c'est une contrainte inhérente à l'approche. En cas de stock insuffisant, l'application bascule automatiquement sur le Mode A.

> **Décision de priorité** : Le Mode A est le mode par défaut et suffit à faire tourner l'application dès le premier jour. Le Mode B est un enrichissement progressif.

### [SPEC-2.2] Formule temporelle

Règles de formulation selon l'écart :

| Écart | Formulation |
|-------|-------------|
| < 1 mois | "Il y a moins d'un mois" |
| 1–11 mois | "Il y a N mois" |
| 1 an exactement (même MM/JJ) | "Il y a 1 an" |
| 2 ans et plus | "Il y a N ans" |

> **Règle de calcul :** l'écart en mois est calculé comme `(today.year - event.year) * 12 + (today.month - event.month)`. Si `months == 0` → "moins d'un mois". Si `months < 12` → "N mois". Sinon → `years = today.year - event.year` → "N an(s)".
>
> **Cas particulier — Mode A (Wikipedia) :** les événements ont le même MM/JJ que la date courante, donc l'écart en mois est toujours un multiple de 12. La règle "1 an exactement (même MM/JJ)" s'applique uniquement au Mode A — en Mode B (RSS), l'article n'est pas nécessairement du même MM/JJ. En Mode B, la formule de calcul générale s'applique : l'écart minimum est `config.content.rss.min_delay_days` (défaut : 90 jours ≈ 3 mois), le cas "< 1 mois" ne se produit donc jamais non plus.

Accord grammatical géré par l'application (1 an / 2 ans, 1 mois / 2 mois).

### [SPEC-2.3] Structure d'un post

Chaque post comprend :

1. **Image** (1080×1350 px, ratio 4:5) générée localement avec Pillow
   - Style visuel : journal vintage / gazette ancienne
   - Contenu de l'image : formule temporelle + date + texte de l'événement
2. **Légende Instagram** (caption)
   - Formule temporelle
   - Date complète de l'événement (ex : "21 mars 2016")
   - Texte de l'événement (tronqué si > 300 caractères avec "...")
   - Mention de la source (ex : "Source : Wikipédia")
   - Hashtags configurables (ex : #histoire #actualité #onthisday)

---

## [SPEC-3] Fonctionnalités

### [SPEC-3.1] Collecte de données

**RF-3.1.1** L'application récupère quotidiennement les événements Wikipedia pour la date du jour (MM/JJ) via l'API "On This Day".

**RF-3.1.2** Les événements récupérés sont stockés en base de données. Un événement déjà présent n'est pas réinséré (déduplication par source + identifiant).

**RF-3.1.3** Si le Mode B est activé, les flux RSS configurés sont récupérés toutes les 6 heures. Les articles sont dédupliqués par URL.

**RF-3.1.4** Tout contenu doit être en français. Pour Wikipedia, l'API `fr.wikipedia.org` est utilisée en priorité. Si elle ne renvoie pas assez de résultats (< `config.content.wikipedia_min_events` événements, défaut : 3), l'API `en.wikipedia.org` est utilisée en fallback (le texte anglais est conservé tel quel en v1, traduction automatique hors périmètre). Le seuil est évalué **après** filtrage de qualité [DS-1.6] — seuls les événements retenus après filtre sont comptés, pas le nombre brut retourné par l'API.

**RF-3.1.5** L'application doit fonctionner sans connexion internet pendant une durée définie (les événements en cache doivent couvrir les X prochains jours, configurable, défaut : 7 jours).

> **Note DDL — `event_type` CHECK :** Le CHECK sur `event_type` inclut `'selected'` pour couvrir les événements de type 'selected' renvoyés par l'API Wikipedia (voir DATA_SOURCES.md [DS-1.8]). Les valeurs autorisées sont donc : `('event', 'birth', 'death', 'holiday', 'selected')`.

### [SPEC-3.2] Sélection et génération

**RF-3.2.1** Lors de chaque cycle de génération, l'application sélectionne une source de contenu selon le mode actif :

**Mode A (défaut)** : sélection depuis `events`
- Statut `available` (jamais publié, ni rejeté définitivement)
- Correspondant à la date du jour (même MM/JJ, n'importe quelle année)
- Non publié dans la fenêtre de déduplication (configurable, défaut : jamais republier — `published_count = 0`)
- Sélection aléatoire parmi les candidats éligibles

**Mode B (RSS, si `content.rss.enabled: true`)** : sélection depuis `rss_articles`
- Statut `available`
- `fetched_at` antérieur à `today - min_delay_days` (délai minimum calculé depuis la **date de collecte**, pas la date de publication originale — voir DATA_SOURCES.md [DS-2.5] pour la justification)
- `published_count = 0`
- Sélection aléatoire parmi les candidats éligibles

**Sélection hybride A+B** : si les deux modes sont actifs, `generate_post()` tire la source selon `config.content.mix_ratio` (proportion Mode B, défaut : `0.2`). Si la source tirée n'a pas de candidat disponible, l'application bascule sur l'autre source (fallback). Si aucune source n'a de candidat, `generate_post()` retourne `None` et l'utilisateur est notifié. La logique hybride est implémentée dans `generator/__init__.py`.

> **Référence détaillée :** la requête SQL de sélection RSS, les paramètres `mix_ratio`, et le mécanisme de fallback sont définis dans `docs/DATA_SOURCES.md`.

> **Traduction SQL de la déduplication :** la table `posts` lie chaque post à un `event_id`. Le sélecteur filtre les événements selon la politique configurée :
> - Politique `never` (défaut) : seuls les événements avec `published_count = 0` sont éligibles. Cette condition est équivalente à "jamais publié, approuvé ou en attente" car `published_count` est incrémenté lors de la publication réussie — les posts `approved` et `pending_approval` actifs ont eux-mêmes `published_count = 0` sur leur événement tant qu'aucune publication n'a abouti. Si un post `pending_approval` existe déjà pour un événement, cet événement sera de toute façon filtré par `max_pending_posts` en amont. La contrainte canonique est donc `published_count = 0`.
> - Politique `window` : les événements sans post publié dans les `deduplication_window_days` derniers jours sont éligibles (`published_count = 0` OU `last_used_at < now - deduplication_window_days`).
> - Politique `always` : aucun filtre de déduplication n'est appliqué.
>
> Le schéma SQL complet de cette requête est défini dans `docs/DATABASE.md`.

**RF-3.2.2** Si aucun événement n'est disponible pour la date du jour, l'application notifie l'utilisateur via Telegram et ne génère pas de post (pas d'erreur silencieuse).

**RF-3.2.3** L'image est générée en 1080×1350 px, sauvegardée dans `data/images/{uuid}.jpg`.

**RF-3.2.4** Si l'événement dispose d'une `image_url` (thumbnail Wikipedia), cette image est téléchargée et intégrée dans le template Pillow. Si le téléchargement échoue ou si `image_url` est NULL, le template est généré en mode purement typographique sans erreur.

**RF-3.2.5** La légende est formatée selon [SPEC-2.3].

**RF-3.2.6** Le post est sauvegardé en base avec le statut `pending_approval`.

> **Machine à états complète :** le statut `pending_approval` est le statut initial d'un post. La machine à états complète, définie dans `docs/DATABASE.md`, liste tous les statuts possibles : `pending_approval`, `approved`, `queued`, `publishing`, `published`, `rejected`, `error`, `expired`. Le statut `skipped` est utilisé pour les posts ignorés via le bouton "Autre événement". Les statuts `approved` et `queued` (v2 — [SPEC-7ter]) sont introduits respectivement lors de la validation Telegram et de la mise en file d'attente.
>
> **Statut `queued` en v1 :** le statut `queued` est atteint quand la limite journalière est atteinte lors de l'approbation. JOB-7 (publication des posts en file) est désactivé en v1 — les posts `queued` nécessitent un déblocage manuel. Voir SCHEDULER.md [JOB-7] pour la procédure.

**RF-3.2.7** Si un post est déjà `pending_approval` (non encore validé), aucun nouveau post n'est généré (comportement configurable : `max_pending_posts`, défaut : 1).

> **Référence schéma DB :** le schéma SQL complet (tables `events`, `posts`, `meta_tokens`, `rss_articles`, `scheduler_state`), les modèles ORM SQLAlchemy, la politique de déduplication au niveau SQL et les procédures de migration sont définis dans `docs/DATABASE.md`.

**RF-3.2.8** Si le mode auto-publish est activé (`scheduler.auto_publish: true`), le post est immédiatement publié sans passer par la validation Telegram. La vérification de la limite journalière de 25 posts Instagram s'applique identiquement à ce flux — si la limite est atteinte, la publication est reportée et l'utilisateur notifié, même en mode auto. Une notification de confirmation est envoyée après chaque publication réussie. Ce mode est désactivé par défaut.

### [SPEC-3.3] Workflow d'approbation Telegram

**RF-3.3.1** Chaque post généré est immédiatement envoyé sur Telegram avec :
- L'image en prévisualisation (envoi du fichier image)
- La légende en texte
- Un clavier inline avec les boutons d'action

> **Précision :** ce comportement s'applique en mode par défaut (`auto_publish: false`). En mode `auto_publish: true` (RF-3.2.8), le post est publié directement sans approbation Telegram ; une notification de confirmation est envoyée après publication.

**RF-3.3.2** Boutons d'action :
- ✅ **Publier** : approuve et déclenche la publication immédiate
- ❌ **Rejeter** : marque le post comme rejeté (l'événement ne sera plus proposé)
- ⏭ **Autre événement** : rejette ce post et génère immédiatement une nouvelle proposition. L'événement sous-jacent n'est PAS bloqué définitivement (contrairement à ❌ Rejeter qui marque l'événement comme `blocked`) — il reste disponible pour une future proposition. Seul le post est marqué `skipped`. La vérification `pending_count >= max_pending_posts` ne s'applique pas ici — le skip remplace le post en attente par un nouveau ; le nombre total de posts `pending_approval` ne change pas (+1 nouveau, mais le post skipé est retiré). **Séquençage :** (1) le post existant est d'abord écrit `skipped` et commité, (2) un nouveau cycle de génération est déclenché. Si `generate_post` retourne `None`, le post original reste `skipped` (pas de restauration) et l'utilisateur est notifié "Aucun autre événement disponible". L'implémentation de `handle_skip` est dans `docs/TELEGRAM_BOT.md`.
- ✏️ **Modifier la légende** : ouvre un échange conversationnel pour saisir une nouvelle légende, puis re-propose le post modifié

**RF-3.3.3** Un post non validé dans les 48h passe automatiquement en statut `expiré`. L'utilisateur est notifié. L'événement sous-jacent reste disponible pour une future proposition.

**RF-3.3.4** Le bot ne traite que les messages/callbacks des utilisateurs dont l'ID Telegram figure dans la liste blanche de la configuration.

**RF-3.3.5** Au démarrage, le bot envoie un message de confirmation d'opérationnalité avec le statut du scheduler (actif/en pause, prochaine exécution prévue).

**RF-3.3.6** Commandes disponibles :
- `/start` — message de bienvenue + état actuel du système
- `/status` — état détaillé : scheduler (actif/pause), dernier post publié, posts en attente
- `/pause` — met le scheduler en pause (plus de génération automatique)
- `/resume` — reprend le scheduler
- `/stats` — statistiques : total publié, rejeté, taux d'approbation, posts/semaine
- `/force` — génère et soumet immédiatement un post (hors cycle)
- `/pending` — liste les posts en attente d'approbation
- `/retry` — retente la publication du dernier post en statut `error` (les deux plateformes)
- `/retry_ig` — retente Instagram uniquement sur le dernier post `published` avec `instagram_error` non NULL
- `/retry_fb` — retente Facebook uniquement sur le dernier post `published` avec `facebook_error` non NULL
- `/help` — liste des commandes

### [SPEC-3.4] Publication Meta (Instagram + Facebook)

**RF-3.4.1** Avant toute publication, l'image est uploadée vers un service d'hébergement public (configurable) pour obtenir une URL HTTPS accessible publiquement, requise par l'API Meta.

> **Note d'hébergement :** l'application héberge elle-même les images via un serveur HTTP embarqué (aiohttp), satisfaisant ainsi la contrainte C-4.1.1 (zéro service payant). Deux architectures sont supportées selon `config.image_hosting.backend` :
> - `backend: "local"` : le serveur aiohttp est embarqué dans le processus principal et expose les images via l'URL `public_base_url`. Requiert que la machine soit accessible publiquement (VPS avec IP fixe). C'est l'architecture de référence simple.
> - `backend: "remote"` : les images sont uploadées vers un serveur distant séparé (`ancnouv-images`). Dans le déploiement Docker de référence, ce serveur tourne dans un conteneur dédié derrière un reverse-proxy nginx avec TLS. Docker est **optionnel** — conforme à C-4.1.6.
>
> **Contrainte C-4.1.5 (derrière NAT) :** l'hébergement d'images (`public_base_url`) nécessite une URL HTTPS publiquement accessible — incompatible avec un déploiement purement privé derrière NAT. Pour concilier C-4.1.5 et RF-3.4.1 sans Docker : déployer sur un VPS avec IP publique (backend `local`), ou utiliser un tunnel SSH permanent vers le VPS pour le serveur d'images. C-4.1.5 s'applique au bot Telegram (polling sortant, aucun port entrant requis) — pas au serveur d'images. Voir `docs/DEPLOYMENT.md`.

**RF-3.4.2** La publication Instagram utilise l'API officielle Meta Instagram Graph API (endpoint media container + media publish).

**RF-3.4.3** La publication Facebook utilise l'API Graph pour poster une photo sur la Page Facebook liée (`/{page-id}/photos`), en parallèle de la publication Instagram.

**RF-3.4.4** Les deux publications (Instagram et Facebook) sont déclenchées simultanément (asyncio). Un échec partiel (l'une réussit, l'autre échoue) est géré indépendamment : les IDs de publication sont stockés séparément en DB.

**RF-3.4.5** L'application gère automatiquement le renouvellement du token d'accès utilisateur long durée (expiration tous les 60 jours). Le Page Access Token, dérivé du token utilisateur, n'expire pas tant que le token utilisateur est valide. Des alertes progressives sont envoyées via Telegram à 30, 14, 7, 3 et 1 jour(s) avant expiration ; le refresh automatique est tenté à partir de J-7. Les publications sont suspendues à J-1 si le refresh a échoué.

**RF-3.4.6** En cas d'échec de publication sur les deux plateformes, le post passe en statut `error` (colonne `error_message` renseignée) et l'utilisateur est notifié via Telegram avec le détail de l'erreur. La commande `/retry` remet le post en statut `approved` pour relancer la publication. En cas d'échec partiel (une plateforme réussit, l'autre échoue), le post passe en statut `published` avec la colonne d'erreur correspondante renseignée (`instagram_error` ou `facebook_error`) ; les commandes `/retry_ig` et `/retry_fb` permettent de retenter uniquement la plateforme échouée.

**RF-3.4.7** L'application respecte une limite configurable de publications par 24h pour Instagram (`max_daily_posts`, défaut : 25). La limite technique réelle de l'API Meta Graph est de 50 publications par utilisateur par 24h, mais la valeur par défaut de 25 est volontairement conservative. La configuration CONFIGURATION.md permet d'ajuster cette valeur jusqu'à 50 maximum. Facebook n'impose pas de limite stricte équivalente pour les Pages.

**RF-3.4.8** Dès qu'au moins une plateforme publie avec succès, le post est marqué `publié`. Les IDs de publication (`instagram_post_id`, `facebook_post_id`) sont stockés séparément — `NULL` si la plateforme a échoué ou est désactivée. En cas d'échec partiel, la commande `/retry_ig` ou `/retry_fb` permet de retenter la plateforme échouée. L'image locale est conservée X jours (configurable, défaut : 7 jours). Clé de config : `content.image_retention_days` (défaut : 7 jours, dans `ContentConfig`).

**RF-3.4.9** La publication Facebook peut être désactivée indépendamment dans la configuration (`facebook.enabled: false`) sans affecter la publication Instagram.

### [SPEC-3.5] Planification

**RF-3.5.1** La fréquence de génération de posts est configurable via une expression cron (ex : `0 */4 * * *` pour toutes les 4h, soit jusqu'à 6 posts/jour).

**RF-3.5.2** L'application tourne en mode daemon continu.

**RF-3.5.3** L'état du scheduler est persisté en base de données (APScheduler SQLAlchemy jobstore) pour survivre aux redémarrages.

**RF-3.5.4** L'utilisateur peut mettre en pause et reprendre le scheduler via Telegram sans redémarrer l'application.

**RF-3.5.5** Le timezone est configurable (défaut : `Europe/Paris`).

### [SPEC-3.6] Configuration et démarrage

**RF-3.6.1** Configuration non-sensible dans `config.yml`, secrets dans `.env`.

**RF-3.6.2** Au démarrage, l'application valide exhaustivement la configuration. Toute valeur manquante ou invalide provoque un arrêt immédiat avec un message d'erreur explicite.

**RF-3.6.3** La séquence d'initialisation complète est : (1) `python -m ancnouv db init` — crée la base de données et applique les migrations ; (2) `python -m ancnouv setup fonts` — télécharge les polices requises pour la génération d'images ; (3) éditer `config.yml` avec les paramètres et `.env` avec les secrets (token Telegram, identifiants Meta) ; (4) `python -m ancnouv auth meta` — flux OAuth interactif pour authentifier l'application auprès de Meta (Instagram + Facebook) ; (5) `python -m ancnouv fetch --prefetch` — pré-collecte les événements Wikipedia pour les 30 prochains jours. Voir `docs/CLI.md` pour le détail de chaque commande.

---

## [SPEC-4] Contraintes

### [SPEC-4.1] Contraintes techniques

| ID | Contrainte |
|----|------------|
| C-4.1.1 | Aucun service payant. Toutes les API et librairies doivent être gratuites ou open-source. |
| C-4.1.2 | Portabilité : Python 3.12+ sur tout environnement Linux/macOS (VPS, Raspberry Pi, NAS). |
| C-4.1.3 | Base de données SQLite uniquement (fichier local, zéro infrastructure externe). |
| C-4.1.4 | Génération d'images uniquement avec Pillow (pas d'IA, pas de service externe). |
| C-4.1.5 | Le bot Telegram doit fonctionner derrière NAT (polling sortant — aucun port entrant requis). Le serveur d'hébergement d'images (`image_hosting.public_base_url`) nécessite une URL HTTPS publiquement accessible — incompatible avec un déploiement purement privé derrière NAT sans VPS. Voir DEPLOYMENT.md pour les options d'architecture. |
| C-4.1.6 | Zéro dépendance à Docker ou à des services cloud (optionnel seulement). |

### [SPEC-4.2] Contraintes légales et éthiques

| ID | Contrainte |
|----|------------|
| C-4.2.1 | Contenu Wikipedia sous licence CC BY-SA 4.0 : la source doit être mentionnée dans chaque post. |
| C-4.2.2 | Flux RSS : reproduction de titres et résumés uniquement, pas du contenu intégral. |
| C-4.2.3 | Respect des conditions d'utilisation de Meta (limite de posts, usage non-spam). |
| C-4.2.4 | Pas de contenu trompeur : la date historique doit toujours être clairement indiquée. |

---

## [SPEC-5] Hors périmètre (v1)

- Génération d'images par IA (Stable Diffusion, DALL-E, Flux, etc.)
- Interface web d'administration
- Publication sur d'autres réseaux (Twitter/X, Threads, etc.)
- Reels Instagram/Facebook (contenu vidéo — voir [SPEC-8] pour étude de faisabilité v3)
- Multi-compte Instagram
- Traduction automatique du contenu anglais en français
- Analytics de performance des posts
- Modération ou filtrage automatique du contenu par IA
- Gestion multi-utilisateurs Telegram (un seul validateur)
- Threads, Carrousel Instagram, Archives BnF/Gallica (voir [SPEC-9])

---

## [SPEC-7] Périmètre v2 — Stories Instagram et Facebook

### [SPEC-7.1] Concept

Une Story est publiée **en complément** du post feed, immédiatement après approbation. Elle reprend le même contenu mais dans un format vertical 9:16, avec tout le texte incrusté dans l'image (pas de légende possible via l'API).

### [SPEC-7.2] Différences techniques avec le post feed

| Aspect | Post feed | Story |
|--------|-----------|-------|
| Dimensions | 1080×1350 px (4:5) | 1080×1920 px (9:16) |
| Légende API | Oui (jusqu'à 2200 car.) | **Non — champ ignoré** |
| Hashtags API | Oui | **Non** |
| Durée de vie | Permanente | **24 heures** |
| `media_type` API | `IMAGE` (défaut) | `STORIES` |
| Découvrabilité | Feed + Explore | Abonnés uniquement |

### [SPEC-7.3] Exigences fonctionnelles v2

**RF-7.3.1** Un second template Pillow 1080×1920 est généré pour chaque post approuvé.

**RF-7.3.2** Le template Story incruste obligatoirement dans l'image : formule temporelle, date, texte de l'événement (plus condensé que le feed), et la source. Les hashtags ne sont pas affichés (inutiles sans légende).

**RF-7.3.3** La Story est publiée immédiatement après le post feed (même cycle d'approbation — une seule validation Telegram déclenche les deux).

**RF-7.3.4** La Story est publiée sur Instagram Stories **et** Facebook Stories (même mécanique que pour le feed).

**RF-7.3.5** La publication de Stories est activable/désactivable indépendamment dans la configuration (`stories.enabled`).

**RF-7.3.6** Un échec de publication de la Story n'affecte pas le statut du post feed (`published`). L'erreur est notifiée via Telegram mais ne bloque pas.

**RF-7.3.7** L'image de la Story est stockée séparément (`data/images/{uuid}_story.jpg`) et soumise à la même politique de rétention que l'image feed.

### [SPEC-7.4] Zones à concevoir en v2

- **Design du template Story** : contraintes d'espace différentes (beaucoup plus de hauteur, zones de sécurité en haut/bas car l'interface Stories masque ~250px en haut et ~400px en bas)
- **Texte condensé** : l'événement affiché dans la Story peut être plus court que dans le feed — définir une longueur max spécifique
- **`story_post_id`** : colonne à ajouter dans la table `posts` en DB (migration Alembic)

---

## [SPEC-7bis] Périmètre v2 — Templates par époque

### Concept

Le style visuel de l'image s'adapte automatiquement à la période de l'événement. L'identité visuelle reste cohérente mais signale visuellement l'ancienneté du contenu.

### Époques et styles

| Époque | Années | Style | Palette |
|--------|--------|-------|---------|
| Antiquité / Moyen Âge | < 1500 | Parchemin, enluminure | Ocre chaud, brun foncé |
| Époque moderne | 1500–1799 | Gazette imprimée, caractères d'époque | Sépia, crème cassé |
| XIXe siècle | 1800–1899 | Journal noir & blanc, gravures | Gris foncé sur blanc cassé |
| Première moitié XXe | 1900–1959 | Presse illustrée, style Art Déco | Noir & blanc légèrement jauni |
| Deuxième moitié XXe | 1960–1999 | Journal moderne, couleurs froides | Blanc, bleu nuit, rouge |
| XXIe siècle | 2000+ | Épuré, typographie contemporaine | Blanc, noir, accent coloré |

### Exigences fonctionnelles

**RF-7bis.1** L'époque est déterminée automatiquement :
- Pour les événements Wikipedia (Mode A) : depuis le champ `year` de l'événement.
- Pour les articles RSS (Mode B) : toujours style "XXIe siècle" (les articles RSS sont des actualités récentes, `published_at` est par définition dans les dernières années). Le champ `year` n'est pas utilisé pour les articles RSS.

**RF-7bis.2** Chaque époque dispose de son propre ensemble de paramètres Pillow (palette, polices, disposition).

**RF-7bis.3** Le même système s'applique au template feed et au template Story (9:16).

**RF-7bis.4** Un paramètre `image.force_template` dans `config.yml` permet de forcer un style unique (pour les utilisateurs préférant une identité visuelle homogène).

---

## [SPEC-7ter] Périmètre v2 — File d'attente et publication planifiée

### Concept

L'utilisateur peut pré-approuver plusieurs posts à l'avance. Le scheduler les publie dans l'ordre selon le rythme configuré, sans nouvelle interaction Telegram.

### Exigences fonctionnelles

**RF-7ter.1** Le statut `queued` est ajouté à la machine à états des posts, entre `approved` et `publishing`.

**RF-7ter.2** Telegram propose les boutons : **Publier maintenant** / **Ajouter à la file** / **Planifier à [heure]** / **Rejeter**.

**RF-7ter.3** Les posts `queued` sont publiés dans l'ordre d'approbation, au rythme de `scheduler.generation_cron`.

**RF-7ter.4** La commande `/queue` affiche la file d'attente avec le nombre de posts en attente et l'heure estimée de chaque publication.

**RF-7ter.5** La taille maximale de la file est configurable (`scheduler.max_queue_size`, défaut : 10). Au-delà, Telegram avertit que la file est pleine.

**RF-7ter.6** Un post planifié à heure fixe (`scheduled_for`) est publié à cette heure précise, indépendamment du cron général. **Priorité :** les posts avec `scheduled_for` non NULL sont traités avant les posts `queued` sans `scheduled_for` (ordre : `scheduled_for ASC NULLS LAST, approved_at ASC`).

### Impact DB

- Colonne `scheduled_for DATETIME` à ajouter dans `posts`
- Statut `queued` ajouté à l'enum de statut

---

## [SPEC-8] Étude de faisabilité v3+ — Reels Instagram et Facebook

> Statut : **non planifié**. Consigné pour référence future. Aucune implémentation prévue avant que v1 et v2 soient stables.

### [SPEC-8.1] Différence fondamentale avec les Stories

Les Reels sont du **contenu vidéo obligatoire**. L'API Meta rejette toute tentative de publier une image statique en tant que Reel. Une vidéo réelle doit être soumise, même si elle ne dure que quelques secondes.

### [SPEC-8.2] Avantage éditorial

Les Reels bénéficient de la **plus forte découvrabilité** de l'écosystème Meta : ils sont poussés algorithmiquement à des non-abonnés, apparaissent dans l'onglet Explore et dans le feed Reels dédié. C'est le format le plus favorisé par Meta actuellement.

### [SPEC-8.3] Ce que cela implique techniquement

**Génération vidéo**
- Pillow seul ne suffit plus. Il faudra ajouter `ffmpeg` (via `subprocess`) ou `moviepy`.
- Approche minimale envisageable : **effet Ken Burns** (zoom/pan progressif) sur l'image générée, avec le texte en overlay animé ou statique.
- Durée cible : 15–30 secondes (acceptable pour un Reel factuel).
- Format : MP4, codec H.264, 1080×1920 px, 30 fps.

**Flux d'upload vidéo** (différent des images)
- L'API Meta impose un upload vidéo en deux temps : upload du fichier binaire vers un endpoint dédié, puis création du container Reel.
- Le traitement côté Meta est asynchrone et peut prendre 1–5 minutes.
- Le polling du statut de traitement est obligatoire avant de publier.

**Audio**
- Les Reels sans son sont techniquement publiables mais moins performants algorithmiquement.
- Ajouter de la musique pose un problème de **droits d'auteur** : Meta supprime les Reels avec musique non licenciée. Les options sont :
  - Sons libres de droits (bibliothèques Creative Commons)
  - Silence (sous-optimal)
  - Sons Meta natifs (non accessible via API)
- Cette question doit être tranchée avant toute implémentation.

**Stockage**
- Un fichier MP4 de 30s ≈ 5–20 Mo, contre ~200 Ko pour un JPEG.
- La politique de rétention doit être revue (réduction probable de la durée).

**Performance sur petits appareils**
- Encoder une vidéo avec ffmpeg sur Raspberry Pi 3 peut prendre 30–120 secondes.
- Le RPi 4 avec accélération matérielle (h264_v4l2m2m) réduit ce temps à ~5–15 secondes.
- À évaluer selon la cible de déploiement au moment de l'implémentation.

### [SPEC-8.4] Dépendances supplémentaires estimées

```
ffmpeg          # encodage vidéo (binaire système, pas pip)
moviepy==2.*    # optionnel, wrapper Python pour ffmpeg
# ou
imageio[ffmpeg] # alternative légère
```

### [SPEC-8.5] Prérequis avant d'implémenter

- v1 et v2 (Stories) stables en production
- Décision sur la stratégie audio (silence / sons libres)
- Validation de la performance d'encodage sur la cible de déploiement
- Décision sur la durée et le style d'animation (Ken Burns ou autre)

---

## [SPEC-9] Étude de faisabilité v3+ — Threads, Carrousel, BnF/Gallica

> Statut : **non planifié**. Consigné pour référence future.

### [SPEC-9.1] Threads (Meta)

**Faisabilité : élevée.** Threads utilise la même infrastructure OAuth Meta. L'API Threads (disponible depuis 2024) permet de publier du texte et des images. La publication serait déclenchée en parallèle d'Instagram et Facebook.

Implications :
- Nouvelle permission OAuth : `threads_basic`, `threads_content_publish`
- Nouveau module `publisher/threads.py`
- Colonne `threads_post_id` dans la table `posts`
- Les Threads sont limités à 500 caractères — la légende devra être tronquée différemment

### [SPEC-9.2] Carrousel Instagram

**Faisabilité : moyenne.** L'API Instagram Graph supporte les carrousels (`media_type=CAROUSEL`). Chaque slide est un container individuel qui sont ensuite regroupés en un container parent.

Implications :
- Générer plusieurs images par post (slide 1 : événement principal, slide 2 : contexte, slide 3 : carte/timeline...)
- Définir une stratégie éditoriale pour le contenu de chaque slide
- Flux API plus complexe : N containers individuels → 1 container carrousel → publication
- Colonne `carousel_item_count` dans la table `posts`
- Pas applicable aux Stories (les Stories multi-slide ont une API différente)

### [SPEC-9.3] Archives BnF / Gallica

**Faisabilité : moyenne.** La BnF expose une API SRU (Search/Retrieve via URL) gratuite pour interroger les archives numérisées de Gallica. Elle permet de trouver des articles de presse historiques par date et mots-clés.

Implications :
- Nouveau fetcher `fetchers/gallica.py`
- API endpoint : `https://gallica.bnf.fr/SRU?operation=searchRetrieve&query=...`
- Le contenu est en OCR (qualité variable) — filtrage de qualité nécessaire
- Richesse éditoriale : vrais articles de presse de l'époque, pas seulement des résumés Wikipedia
- Complémentaire du Mode A (Wikipedia) pour les événements français

---

## [SPEC-6] Glossaire

| Terme | Définition |
|-------|-----------|
| Événement | Fait historique issu de Wikipedia "On This Day" |
| Article | Actualité issue d'un flux RSS |
| Post | Entité générée (image feed + légende) publiable sur Instagram et Facebook Page. En v2, un post peut aussi déclencher la publication d'une Story associée. |
| Story | Contenu éphémère (24h) en format 9:16 publié sur Instagram Stories et Facebook Stories en complément du post feed (v2 — SPEC-7) |
| Approbation | Processus de validation via Telegram avant publication |
| Container | Objet intermédiaire de l'API Instagram avant publication (media container) |
| Token utilisateur court | Token Meta valable 1 heure (issu du flux OAuth) |
| Token utilisateur long | Token Meta valable 60 jours (échangé depuis le token court) |
| Page Access Token | Token associé à la Page Facebook, dérivé du token utilisateur long, sans expiration propre |
| Fenêtre de déduplication | Durée minimale entre deux publications du même événement (uniquement pour la politique `window`). La politique `never` (défaut) est équivalente à une fenêtre infinie — une fois publié, un événement n'est plus jamais reproposé. La politique `always` supprime toute contrainte de déduplication. |
| Mode A | Mode Anniversaires historiques (Wikipedia) |
| Mode B | Mode Actualités retardées (RSS) |
| Escalade | Mécanisme automatique d'assouplissement progressif des critères de sélection quand le stock d'événements disponibles est bas (voir [DS-1.4b] dans `docs/DATA_SOURCES.md`) |
| File d'attente | Liste de posts approuvés avec une heure de publication planifiée (`scheduled_for`) — statut `queued` (v2 — SPEC-7ter) |
