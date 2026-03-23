# Sources de données

> Référence : [SPEC-3.1]

---

## Vue d'ensemble

| Source | Mode | Profondeur | Coût | Langue |
|--------|------|-----------|------|--------|
| Wikipedia "On This Day" | A (principal) | Illimitée | Gratuit | FR (fallback EN) |
| Flux RSS | B (optionnel) | Durée de collecte | Gratuit | FR |

---

## [DS-1] Wikipedia "On This Day" API

### [DS-1.1] Description

L'API Wikimedia "Featured Feed" renvoie les événements notables survenus un jour donné (MM/JJ) à travers toutes les années de l'Histoire. C'est la source principale de l'application.

### [DS-1.2] Endpoint

```
GET https://api.wikimedia.org/feed/v1/wikipedia/{lang}/onthisday/{type}/{month}/{day}
```

| Paramètre | Valeurs | Description |
|-----------|---------|-------------|
| `lang` | `fr`, `en`, ... | Édition Wikipedia |
| `type` | `all`, `selected`, `events`, `births`, `deaths`, `holidays` | Type d'événements |
| `month` | `01`–`12` | Mois (zéro-paddé) |
| `day` | `01`–`31` | Jour (zéro-paddé) |

### [DS-1.3] Format de réponse

```json
{
  "events": [
    {
      "text": "Fondation de la ville de Vienne en Autriche.",
      "year": 1137,
      "pages": [
        {
          "title": "Vienne (Autriche)",
          "thumbnail": {
            "source": "https://upload.wikimedia.org/.../thumbnail.jpg",
            "width": 320,
            "height": 213
          },
          "content_urls": {
            "desktop": {"page": "https://fr.wikipedia.org/wiki/Vienne_(Autriche)"}
          }
        }
      ]
    }
  ]
}
```

### [DS-1.4] Stratégie de collecte

**Type utilisé : `events`** (par défaut). Optionnellement activables : `births`, `deaths`.

**Langue (niveau d'escalade 0 — comportement normal) :** `fr` en priorité. Si la réponse contient < `config.content.wikipedia_min_events` événements après filtrage [DS-1.6] (défaut : 3), fallback sur `en` (contenu stocké en anglais avec `source_lang='en'`). Le seuil s'applique **après** filtrage — seuls les événements de qualité comptent, pas le nombre brut retourné par l'API.

**Si l'API EN est aussi insuffisante (< `wikipedia_min_events`) :** les événements collectés (même insuffisants) sont stockés tels quels. Aucune escalade supplémentaire n'est déclenchée par ce cas spécifique — l'escalade globale via `low_stock_threshold` gère ce scénario. Un simple WARNING est loggé : `"API Wikipedia EN insuffisante pour {date}: {N} événements après filtrage"`.

> **Niveau d'escalade ≥ 2 :** le seuil `wikipedia_min_events` n'est plus le déclencheur du fallback EN — l'API EN est appelée **inconditionnellement** pour chaque date, en parallèle de FR. Les deux résultats sont fusionnés avant stockage. La logique "si FR insuffisant → fallback EN" ne s'applique qu'au niveau 0.
>
> **Avertissement doublons inter-langues :** le même événement historique peut apparaître deux fois (version FR + version EN) avec des `content_hash` différents (textes différents). La contrainte UNIQUE sur `(source, source_lang, month, day, year, content_hash)` n'empêche pas les doublons inter-langues (`source_lang` diffère). En v1, ce comportement est accepté : l'application peut proposer le même événement en deux langues. L'opérateur peut rejeter la version EN via ❌ pour la marquer `blocked`.

### [DS-1.4b] Stratégie d'épuisement du pool

Quand les événements disponibles pour les 7 prochains jours tombent sous `content.low_stock_threshold` (défaut : 3), l'application applique l'escalade automatique :

| `escalation_level` | Action effective |
|-------------------|-----------------|
| 1 | Activer `births` et `deaths` en plus de `events` |
| 2 | Activer le fallback EN systématiquement : l'API EN est appelée **en plus** de l'API FR pour chaque date, indépendamment du seuil `wikipedia_min_events` (qui n'est plus le déclencheur — EN est inconditionnel). Les résultats FR et EN sont fusionnés avant stockage. |
| 3 | Déduplication : `window: 365 jours` — permet de reproposer des événements publiés il y a plus d'un an |
| 4 | Déduplication : `window: 180 jours` — assouplissement supplémentaire (180j < 365j = fenêtre plus courte = plus d'événements éligibles). **Aparté :** une fenêtre *plus longue* filtre *davantage* d'événements récents ; une fenêtre plus courte est donc plus permissive. Les niveaux 3→4 assouplissent progressivement en réduisant la fenêtre. |
| 5 | Pool vide même avec window 180j → notification bloquante |

**L'application ne modifie jamais `config.yml`.** L'état d'escalade est stocké dans `scheduler_state.escalation_level` (valeur `"0"` à `"5"`).

**Fenêtre de comptage :** les 7 prochains jours (de `date.today()` à `date.today() + 6`). La vérification se fait après chaque collecte `job_fetch_wiki`. C'est l'implémentation de RF-3.1.5 (fonctionnement hors-ligne pendant 7 jours). Une date pour laquelle `SELECT COUNT(*) = 0` (aucun événement stocké) entraîne `generate_post()` → `None` ce jour-là, avec notification Telegram "aucun événement disponible". Le stock bas déclenche l'escalade progressivement mais ne bloque pas immédiatement.

```sql
-- Requête de comptage du stock sur 7 jours (pour chaque date i dans range(7))
SELECT COUNT(*)
FROM events
WHERE (month = :month AND day = :day)
  AND status = 'available'
  AND published_count = 0
```

> **Note :** cette requête utilise `published_count = 0` de façon fixe, ce qui sous-estime le stock réel si la politique active est `window` ou `always`. Pour une évaluation précise avec ces politiques, remplacer la condition par celle correspondant à la politique effective. En pratique, l'escalade est une mesure conservatrice : sous-estimer le stock ne fait que déclencher l'escalade plus tôt, ce qui est acceptable.

### [DS-1.4c] Déclencheur d'escalade

> **Sémantique de `low_stock_threshold` :** le seuil est évalué **par date individuelle** (pas sur le total de 7 jours). L'escalade est déclenchée si **au moins une** des 7 dates futures a un stock < `low_stock_threshold`. Une date avec 0 événement stockés (absente de la DB) et une date avec 2 événements (< seuil=3) déclenchent toutes deux l'escalade. Il n'existe pas de mode "stock insuffisant pour une date précise" sans escalade — l'escalade globale est le seul mécanisme de réponse.

Déclenché dans `job_fetch_wiki` (JOB-1). Appelle `increment_escalation_level(session)` si le stock passe sous le seuil.

```python
async def increment_escalation_level(session: AsyncSession) -> int: ...
async def get_effective_query_params(session: AsyncSession, config: Config) -> EffectiveQueryParams: ...
```

> **`RawContentItem`** : dataclass de transport définie dans `ancnouv/fetchers/base.py`. Voir ARCHITECTURE.md — section "Fetchers → DB" pour les champs complets.

`increment_escalation_level` : incrémente `scheduler_state.escalation_level` (max 5, `min(current + 1, 5)`). Retourne le nouveau niveau.

`get_effective_query_params` : lit `escalation_level` depuis DB, calcule les paramètres effectifs en surchargeant la config. Les paramètres config sont la base — l'escalade les surcharge si elle est plus permissive. Aux niveaux 3 et 4 (déduplication `window`), si `config.content.deduplication_policy` est déjà `"window"` ou `"always"`, la valeur config est conservée si elle est plus permissive (ex: `"always"` n'est pas rétrogradé vers `"window"`). L'escalade ne peut qu'assouplir, jamais durcir.

`EffectiveQueryParams` (dataclass dans **`fetchers/base.py`** — pas dans `generator/selector.py` pour éviter la dépendance circulaire : `generator/__init__.py` importe `WikipediaFetcher` depuis `fetchers/`, et `fetchers/wikipedia.py` aurait besoin d'importer `EffectiveQueryParams` depuis `generator/` si elle y était définie. Le placement dans `fetchers/base.py` crée une dépendance à sens unique : `generator/selector.py` importe depuis `fetchers/base.py`, jamais l'inverse) :
- `event_types: list[str]` — ex: `["events", "births", "deaths"]`
- `use_fallback_en: bool`
- `dedup_policy: str` — `"never"` | `"window"` | `"always"`
- `dedup_window: int` — jours, ignoré si `dedup_policy != "window"`

`generator/selector.py` importe `EffectiveQueryParams` depuis `fetchers/base.py` (sens unique — pas de dépendance circulaire).

**Réinitialisation :** l'escalade ne redescend **jamais automatiquement** — même si le stock remonte. Seule la commande manuelle la remet à 0 :
```bash
python -m ancnouv escalation reset
# Remet escalation_level = 0 et notifie sur Telegram
```
Ce cliquet à sens unique est intentionnel : un stock bas est un signal d'alarme que l'opérateur doit traiter explicitement avant de retour à un mode normal.

Voir [RF-3.6.3] et CLI.md pour la séquence d'initialisation complète.

> **Procédure opérationnelle au niveau 5 :** `increment_escalation_level` est appelé à chaque exécution de JOB-1 tant que le stock reste bas (bloqué à 5 depuis le `min(current + 1, 5)`). Au niveau 5, l'application envoie une notification Telegram "Pool épuisé" et ne peut plus poster. Résolution :
> 1. Attendre que de nouvelles dates entrent dans la fenêtre (au fil des jours, de nouveaux MM/JJ deviennent disponibles)
> 2. Une fois le stock reconstitué, exécuter `python -m ancnouv escalation reset`
> 3. L'application reprend normalement au niveau 0

### [DS-1.5] Rate limits et authentification

- **Sans authentification** : 200 requêtes/jour par IP (suffisant pour usage normal)
- **Header obligatoire** : `User-Agent: AnciennesNouvelles/1.0 (contact@exemple.com)`
- Pas d'authentification Wikimedia en v1

### [DS-1.6] Filtrage et qualité

| Règle | Critère | Action |
|-------|---------|--------|
| Texte trop court | `len(text) < 20` | Ignorer |
| Texte trop long | `len(text) > 500` | Tronquer à 500 caractères |
| Année future | `year > current_year` | Ignorer |
| Année hors plage | `year < -9999` | Ignorer (valeur aberrante) |
| Doublon | Même `source + source_lang + month + day + year + content_hash` | UNIQUE constraint |

> **Années négatives** : les années négatives sont valides (événements avant J.-C.). Seules les valeurs `< -9999` sont aberrantes.

### [DS-1.7] Mapping vers le modèle DB

Champs mappés pour chaque événement Wikipedia → `Event` :
- `source="wikipedia"`, `source_lang="fr"` (ou `"en"` si fallback)
- `event_type` : `"event"` | `"birth"` | `"death"` | `"holiday"`
- `month`, `day`, `year` : depuis le champ `year` et la date de la requête
- `description` : champ `text` de l'entrée
- `wikipedia_url` : `pages[0].content_urls.desktop.page` si présent
- `image_url` : `pages[0].thumbnail.source` si présent (peut être `NULL`)
- `content_hash` : `compute_content_hash(description)` — voir [DS-1.7b]
- `title` : `None` — l'endpoint "On This Day" ne fournit pas de champ `title` au niveau de l'entrée. `pages[0].title` est disponible dans la réponse mais n'est pas mappé dans `RawContentItem.title` en v1 (toujours `None`). Réservé pour les sources futures avec titre explicite.
- `fetched_at` : `datetime.now(timezone.utc)`

### [DS-1.7b] Normalisation du `content_hash`

```python
def compute_content_hash(text: str) -> str: ...
```

Implémentation canonique dans `ancnouv/db/utils.py` — voir DATABASE.md section "Politique de déduplication". Étapes dans l'ordre : NFKC → strip → lowercase → SHA-256 hexdigest.

Vecteur de test canonique :
```python
# Doit toujours retourner la même valeur — espaces et casse normalisés
assert compute_content_hash("  Fondation de Vienne.  ") == compute_content_hash("Fondation de Vienne.")
# Étapes : NFKC → strip → lowercase → SHA-256
# "Fondation de Vienne." → "fondation de vienne." → sha256 → valeur fixe
```
La même fonction doit être utilisée dans les fetchers Wikipedia ET dans les tests — une divergence d'implémentation produirait des doublons en DB sans erreur visible.

### [DS-1.8] Mapping type API → type DB

L'endpoint `/onthisday/{type}/` renvoie une clé JSON dont le nom varie selon le type :

| Paramètre `type` | Clé JSON réponse |
|-----------------|------------------|
| `events` | `"events"` |
| `births` | `"births"` |
| `deaths` | `"deaths"` |
| `holidays` | `"holidays"` |
| `selected` | `"selected"` |

La DB stocke `"holiday"` (singulier), l'API attend `"holidays"` (pluriel). `TYPE_TO_KEY` dict dans `fetchers/wikipedia.py` — clé = type DB, valeur = `(api_param, json_response_key)` :

```python
TYPE_TO_KEY = {
    "event":   ("events",   "events"),
    "birth":   ("births",   "births"),
    "death":   ("deaths",   "deaths"),
    "holiday": ("holidays", "holidays"),
    "selected": ("selected", "selected"),
}
```

Le fetcher effectue **un appel API séparé par type** et parse la clé JSON correspondante.

> **Note DDL :** `TYPE_TO_KEY` contient `"selected"` mais le CHECK DDL sur `event_type` dans DATABASE.md ne liste que `('event', 'birth', 'death', 'holiday')`. Décision retenue : ajouter `'selected'` au CHECK DDL de `event_type` dans DATABASE.md (l'API Wikipedia l'expose, il peut être utile). Voir DATABASE.md pour la migration correspondante.

### [DS-1.9] Interface `WikipediaFetcher` (`fetchers/wikipedia.py`)

> **`BaseFetcher` et `RawContentItem` :** définis dans `ancnouv/fetchers/base.py`. Voir ARCHITECTURE.md — section "Fetchers → DB" pour les contrats complets.

```python
class WikipediaFetcher(BaseFetcher):
    def __init__(self, config: Config, effective_params: EffectiveQueryParams | None = None) -> None: ...
    async def fetch(self, target_date: date) -> list[RawContentItem]: ...
    async def store(self, items: list[RawContentItem], session: AsyncSession) -> int: ...
    async def _call_api(self, lang: str, event_type: str, target_date: date) -> dict: ...
```

`_call_api` : GET `https://api.wikimedia.org/feed/v1/wikipedia/{lang}/onthisday/{type}/{MM}/{DD}`. Header `User-Agent` obligatoire. Timeout HTTP : 10 secondes (une requête sans timeout peut bloquer le job indéfiniment). Retourne `{}` si HTTP 404 (date sans événements — normal). Stratégie de retry : HTTP 429 ou 503 → lire l'en-tête `Retry-After`, attendre, réessayer jusqu'à 3 fois ; tout autre code non-200 → lève `FetcherError` immédiatement sans retry.

L'en-tête `Retry-After` peut être soit un entier (secondes), soit une date HTTP RFC 7231 (ex : `Wed, 21 Oct 2015 07:28:00 GMT`). Gestion correcte :
```python
retry_after_raw = response.headers.get("Retry-After", "5")
try:
    wait = int(retry_after_raw)
except ValueError:
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(retry_after_raw)
        wait = max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        wait = 5
wait = min(wait, 60)
```

`fetch()` traite le résultat de `_call_api` avec `.get(json_key, [])` — jamais d'accès direct `result[json_key]` (protège contre le `{}` retourné sur 404 et les clés absentes dans une réponse mal formée).

`fetch` : conforme au contrat `BaseFetcher` — pas de session requise. Itère sur `effective_params.event_types`, parse les entrées. Accède aux champs avec `.get()` pour les champs optionnels (`title`, `thumbnail`, etc.) — `AttributeError` sur flux mal formé sinon.

`store` : INSERT OR IGNORE via la contrainte UNIQUE. Retourne le nombre de nouveaux événements insérés. **Comportement transactionnel :** toutes les insertions de `store()` partagent la session du job — en cas d'exception non catchée, SQLAlchemy rollback la session entière (rollback total, pas partiel). Les items déjà insérés lors d'un appel précédent (INSERT OR IGNORE) ne sont pas affectés par le rollback d'un appel ultérieur.

**Usage dans `job_fetch_wiki` :** calculer `get_effective_query_params(session, config)` **avant** d'instancier `WikipediaFetcher` — le job injecte les params via le constructeur. En `prefetch_wikipedia()` et dans les tests, instancier sans `effective_params` (niveau 0, défauts de config).

### [DS-1.10] Cas limites identifiés

| Problème | Solution |
|---------|----------|
| Peu d'événements en FR pour certaines dates | Fallback EN + niveaux d'escalade |
| `year` négatif (avant J.-C.) | Valide — stocker normalement, calculer "Il y a N ans" |
| Pas de thumbnail | `image_url = NULL` — image générée sans photo |
| API change sans préavis | Health check au démarrage, alertes si réponse inattendue |

---

## [DS-2] Flux RSS (Mode B — optionnel)

### [DS-2.1] Description

Des flux RSS de médias francophones sont récupérés toutes les **6 heures** (RF-3.1.3) via JOB-2. Les articles sont stockés en base puis publiés avec un délai configuré (défaut : 90 jours).

### [DS-2.2] Sources recommandées (gratuites)

Sources avec licences permissives :

| Média | URL du flux | Licence |
|-------|------------|---------|
| Wikimedia News | `https://en.wikinews.org/w/index.php?title=Special:NewsFeed&feed=atom` | CC BY-SA |

Sources à vérifier avant activation (CGU à confirmer) :

| Média | URL du flux | Statut |
|-------|------------|--------|
| Le Figaro | `https://www.lefigaro.fr/rss/figaro_actualites.xml` | ⚠️ vérifier les CGU avant activation |

Exemples de format URL uniquement (CGU interdisent la republication — **ne pas activer sans accord préalable**) :

| Média | URL du flux |
|-------|------------|
| Le Monde | `https://www.lemonde.fr/rss/une.xml` |
| France Info | `https://www.francetvinfo.fr/rss/` |
| RFI | `https://www.rfi.fr/fr/rss` |

> **Avertissement légal :** Les CGU de Le Monde, France Info et RFI **interdisent la republication** de leurs contenus sur des réseaux sociaux sans accord préalable. Ces sources sont listées uniquement à titre d'exemple de format URL. Utiliser en priorité des sources avec licences permissives (CC BY-SA). En cas de doute, désactiver le Mode B (`rss.enabled: false`).

### [DS-2.3] Parsing RSS

Librairie : `feedparser` (RSS 1.0/2.0 et Atom).

`feedparser.parse()` est **synchrone** — wrappé obligatoirement avec `asyncio.to_thread()` pour ne pas bloquer la boucle événementielle.

> **Ordre de validation obligatoire :** la validation `published_parsed is None` doit être effectuée **avant** toute construction de `RssFeedItem`. Si cette vérification est faite après le mapping ORM, un `IntegrityError: NOT NULL constraint failed` peut survenir sur la colonne `published_at`. Ordre correct : filtrer l'entrée feedparser → construire `RssFeedItem` → appeler `store()`.

Champs extraits de chaque entrée :
- `title` : `entry.get("title", "").strip()` — si vide, ignorer l'entrée
- `article_url` : `entry.get("link", "")` — si vide, ignorer
- `summary` : `entry.get("summary", "")`
- `published_at` : depuis `entry.published_parsed` — si `None`, ignorer (feedparser ne peut pas parser la date)
- `image_url` : extraction en deux passes avec fallback (version minimale feedparser : `feedparser>=6.0`) :
  ```python
  image_url = None
  thumbnails = entry.get("media_thumbnail", [])
  if thumbnails:
      image_url = thumbnails[0].get("url") or thumbnails[0].get("href")
  if not image_url:
      enclosures = entry.get("enclosures", [])
      if enclosures and enclosures[0].get("type", "").startswith("image/"):
          image_url = enclosures[0].get("href") or enclosures[0].get("url")
  ```
  Si aucune image trouvée : `image_url = None` (acceptable)
- `source_url` (= `feed_url` en DB) : `config.content.rss.feeds[n].url` (structure `RssFeedConfig` — voir CONFIGURATION.md section "Mode B RSS") — URL du flux RSS source, **pas** l'URL de l'article. Champ `NOT NULL` en DB, doit être renseigné explicitement à chaque appel de `store()` avant insertion.

> **Distinction `RssFeedItem` vs `RssArticle` (ORM) :** la dataclass de transport retournée par `RssFetcher.fetch_all` est nommée `RssFeedItem` (module `ancnouv.fetchers.base`). Le modèle ORM stocké en DB est `RssArticle` (module `ancnouv.db.models`). Les deux types sont distincts — ne pas les confondre. Le mapping lors du `store()` : `orm_obj.feed_url = dataclass.source_url` (champs de noms différents).

Définition complète de `RssFeedItem` :
```python
@dataclass
class RssFeedItem:
    source_url: str   # URL du flux RSS (feed_url en DB)
    title: str
    summary: str
    article_url: str
    published_at: datetime
    fetched_at: datetime
    image_url: str | None = None
    feed_name: str = ""  # fourni par config.content.rss.feeds[n].name — NOT NULL en DB
```
`feed_name` est obligatoirement renseigné par `fetch_all()` avant d'appeler `store()`.

**Gestion d'erreur réseau :** `feed.bozo=True` est fréquent sur les flux RSS de production (encodage incorrect, DTD manquante, balises mal formées) — beaucoup de flux populaires déclenchent `bozo=True` mais retournent des données exploitables. Ignorer uniquement si `feed.bozo_exception` est une erreur critique de connexion (`URLError`, `ConnectionError`) ou si `feed.entries` est vide. Ne **pas** ignorer systématiquement sur `bozo=True`. Logique : `if feed.get("bozo") and isinstance(feed.get("bozo_exception"), (URLError, ConnectionError)):` → logger WARNING et passer au flux suivant. Sinon, continuer le parsing normalement.

> **Import obligatoire :** `from urllib.error import URLError` — sans cet import, `isinstance(feed.bozo_exception, URLError)` lève `NameError` à l'exécution.

### [DS-2.4] Filtrage

| Règle | Critère | Action |
|-------|---------|--------|
| Doublon | Même `article_url` (y compris si deux flux différents publient le même article) | UNIQUE constraint — INSERT OR IGNORE. Choix intentionnel : l'URL canonique identifie univoquement un contenu, quelle que soit la source. Le premier flux qui insère l'article "gagne" — le `feed_url` du second est ignoré silencieusement. |
| Trop vieux | `published_at < now - max_age_days` (défaut : 180j) | Ignorer à la collecte. La marge de 90j entre `max_age_days` (180j) et `min_delay_days` (90j) est intentionnelle : un article collecté le premier jour où il passe sous le seuil `max_age_days` reste éligible pendant encore 90j avant d'être trop vieux pour être collecté. **Cas limite :** un article collecté exactement à `max_age_days - 1` jours d'ancienneté (179j) devient éligible dans `min_delay_days` jours (90j), soit potentiellement 269j après sa publication originale. Ce comportement est documenté comme acceptable : ce cas limite ne justifie pas de contrainte supplémentaire. |
| Titre vide | `len(title) < 5` | Ignorer |
| Date absente | `published_parsed is None` | Ignorer |

### [DS-2.5] Contrainte temporelle du Mode B

Les articles sont publiables uniquement après `min_delay_days` jours depuis leur publication originale (défaut : 90 jours). Si le stock d'articles éligibles est vide, le scheduler bascule automatiquement sur Mode A.

**Requête SQL de sélection :**
```sql
SELECT id
FROM rss_articles
WHERE status = 'available'
  AND published_count = 0
  AND fetched_at <= :cutoff_date   -- cutoff = today - min_delay_days (équivalent à : fetched_at + min_delay_days <= today)
ORDER BY RANDOM()
LIMIT 1
```

> **`fetched_at` et non `published_at` :** le délai `min_delay_days` est calculé depuis la date de **collecte** par l'app (`fetched_at`), pas la date de publication originale (`published_at`). Avec `published_at`, un article de 2020 collecté aujourd'hui serait immédiatement éligible (sa `published_at` est largement < cutoff). La contrainte deviendrait sans effet sur les articles anciens. Ce comportement est documenté dans CONFIGURATION.md (commentaire `rss.min_delay_days`).

**Interface `RssFetcher` (`fetchers/rss.py`) :**

> **Note d'héritage :** `RssFetcher` n'hérite **pas** de `BaseFetcher`. `BaseFetcher` définit l'interface pour les fetchers basés sur une date (`fetch(target_date)`). `RssFetcher` collecte tous les articles en une fois (`fetch_all(config)`) — l'ABC ne s'applique pas. En v2, tout nouveau fetcher date-based devra hériter de `BaseFetcher`.

> **Règle générale async :** tout appel bloquant dans un contexte `async` doit être wrappé avec `asyncio.to_thread()`. Exemples de fonctions bloquantes connues : `feedparser.parse()`, accès réseau synchrone, opérations fichier lourdes. Un appel bloquant non wrappé dans un handler async bloque la boucle événementielle et peut faire manquer des déclenchements APScheduler.

```python
class RssFetcher:
    async def fetch_all(self, config: Config) -> list[RssFeedItem]: ...
    async def store(self, articles: list[RssFeedItem], session: AsyncSession) -> int: ...
```

`fetch_all` : itère sur `config.content.rss.feeds`, wrappé avec `asyncio.to_thread()`. Remplit `feed_name` depuis `config.content.rss.feeds[n].name` — obligatoire car `feed_name` est `NOT NULL` en DB. Si un flux sur N lève une erreur réseau générique (non `bozo`) : logger WARNING avec l'URL du flux et l'exception, passer au flux suivant, continuer la collecte des autres flux. Ne jamais laisser l'erreur d'un flux unique interrompre la collecte des autres. Le `store()` est appelé uniquement avec les articles des flux ayant réussi.

`store` : INSERT OR IGNORE via la contrainte `UNIQUE(article_url)`. Retourne le count.

---

## [DS-3] Stratégie de sélection hybride A+B

```python
async def generate_post(session: AsyncSession) -> Post | None: ...
```

Voir ARCHITECTURE.md — "Génération hybride Mode A+B".

Algorithme :
1. Si `rss.enabled = false` : Mode A uniquement
2. Si `rss.enabled = true` : tirage probabiliste — `random.random() < mix_ratio` → Mode B (RSS) ; sinon Mode A. `mix_ratio = config.content.mix_ratio` (défaut : 0.2). **Sémantique :** `0.0` = 100% Wikipedia, `1.0` = 100% RSS. Défini dans `ContentConfig` (chemin `config.content.mix_ratio`), voir CONFIGURATION.md.
3. Si la source tirée retourne `None` : fallback sur l'autre source
4. Si les deux retournent `None` : retourner `None` (notification Telegram depuis le job)

**`select_event(session, target_date, effective_params)` — mécanisme :** filtre `month = target_date.month AND day = target_date.day` dans la table `events` (toutes les années historiques pour ce jour du calendrier). Applique la politique de déduplication (`published_count = 0` en mode `"never"`, ou filtre `last_used_at` en mode `"window"`). Sélection aléatoire parmi les candidats. Voir DATABASE.md — section "Requête de sélection des candidats".

Signature complète :
```python
async def select_event(session: AsyncSession, target_date: date, effective_params: EffectiveQueryParams) -> Event | None: ...
```
`EffectiveQueryParams` est défini dans `ancnouv/fetchers/base.py`.

---

## [DS-4] Stratégie de cache et fraîcheur

### Durée de vie des données

| Donnée | Conservation |
|--------|-------------|
| Événements Wikipedia | Permanente (données historiques immuables) |
| Articles RSS | Permanente (nécessaire pour délai min) |
| Images générées | `content.image_retention_days` (défaut : 7j, défini dans `ContentConfig`) |

### Pré-collecte au démarrage

```python
# ancnouv/fetchers/__init__.py
async def prefetch_wikipedia(config: Config, session: AsyncSession) -> None: ...
```

Collecte les événements Wikipedia pour les `prefetch_days` prochains jours (défaut : 30). Appelé au premier démarrage ou via `python -m ancnouv fetch --prefetch` (dans `cli/fetch.py`, via `run_fetch(config, prefetch=True)`). **Note :** `prefetch_wikipedia` utilise les paramètres de config de base (niveau d'escalade 0) — le niveau d'escalade courant est ignoré. Pour un prefetch respectant l'escalade active, utiliser JOB-1 directement ou déclencher `/force` depuis le bot. Cette limitation est documentée intentionnellement : le prefetch est une opération de bootstrap, pas un contournement de l'escalade.

### Health check des sources

```python
# ancnouv/fetchers/__init__.py
async def check_sources_health(config: Config) -> dict[str, bool]: ...
```

Vérifie la connectivité Wikipedia et des flux RSS configurés au démarrage. **"Connectivité" = HEAD HTTP avec timeout 5s** (pas de téléchargement du contenu). Wikipedia : `HEAD https://api.wikimedia.org/feed/v1/wikipedia/fr/onthisday/events/01/01`. Flux RSS : HEAD sur chaque URL de flux. Retourne `{"wikipedia_fr": bool, "rss_0": bool, "rss_1": bool, ...}` (index du flux dans `config.content.rss.feeds`). Si Wikipedia est KO mais que le cache DB couvre les 7 prochains jours, la valeur retournée est `False` mais l'application peut continuer — le comportement "KO avec cache suffisant" est loggé en WARNING, pas bloquant. Appelé par `cli/health.py`.

> **Avertissement HEAD 405 :** HEAD sur l'endpoint Wikimedia peut retourner `405 Method Not Allowed` sur certaines configurations. En cas de `405`, retenter avec GET pour confirmer si l'API est réellement KO. Fallback : `HEAD` suivi de `GET /feed/v1/wikipedia/fr/onthisday/events/01/01` avec timeout 5s.
