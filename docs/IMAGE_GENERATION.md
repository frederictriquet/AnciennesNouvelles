# Génération d'Images

> Référence : [SPEC-2.3], [SPEC-3.2], C-4.1.4

---

## Contraintes techniques

- **Librairie** : Pillow (PIL Fork) — gratuit, aucun service externe
- **Format de sortie** : JPEG, qualité 95 (configurable : `config.image.jpeg_quality`)
- **Prérequis système : `libjpeg-dev`** — Pillow nécessite `libjpeg-dev` (ou `libjpeg62-turbo-dev`) pour encoder en JPEG. Sans cette dépendance système, `pip install pillow` réussit mais `img.save(..., "JPEG")` lève `KeyError: encoder jpeg not available`. Installer **avant** `pip install pillow` :
  - Debian/Ubuntu : `apt-get install libjpeg-dev`
  - Dockerfile : `RUN apt-get install -y libjpeg-dev` (voir DEPLOYMENT.md)
  - macOS : `brew install libjpeg` (si Pillow n'inclut pas les codecs précompilés)
- **Dimensions** : 1080 × 1350 px (ratio 4:5 — optimal pour le feed Instagram). Lues depuis `config.image.width` et `config.image.height`. ⚠️ Le ratio 4:5 = 0.8 est exactement à la **limite basse** acceptée par Meta (0.8–1.91). Ne pas modifier `height` sans vérifier que le ratio reste dans cette plage.
- **Espace disque** : ~100–300 Ko par image selon la complexité
- **Durée de génération** : < 1 seconde sur Raspberry Pi 4
- **Dépendance numpy :** `numpy==1.*` est requis (voir `requirements.txt`). numpy 2.x a modifié l'API de `np.random.randint` — `_draw_paper_texture` peut produire des résultats incorrects silencieusement avec numpy 2.x. Épingler `numpy>=1.24,<2` dans `requirements.txt`.
- **Rétention des fichiers image** : les images sont conservées `config.content.image_retention_days` jours (défaut : 7) après la publication/rejection/expiration. Nettoyage assuré par `job_cleanup` (JOB-6, SCHEDULER.md). `generate_image` ne gère pas la rétention — les fichiers s'accumulent dans `data/images/` jusqu'au prochain passage de `job_cleanup`.

---

## Format Instagram — Justification du ratio 4:5

| Format | Ratio | Dimensions | Avantage |
|--------|-------|-----------|----------|
| Carré | 1:1 | 1080×1080 | Classique |
| Portrait | **4:5** | **1080×1350** | **Plus de surface dans le feed, meilleure visibilité** |
| Portrait max | 9:16 | 1080×1920 | Stories/Reels uniquement |

---

## Concept visuel : Style Gazette Vintage

L'esthétique vise un journal ancien du XIXe siècle. Ce style renforce le concept "Anciennes Nouvelles".

### Palette de couleurs

| Clé | Valeur | Usage |
|-----|--------|-------|
| `background` | `#F2E8D5` | Fond papier jauni |
| `background_dark` | `#E8D9BC` | Zones d'ombre |
| `text_primary` | `#1A1008` | Encre noire (texte principal) |
| `text_secondary` | `#4A3728` | Encre brune (texte secondaire) |
| `accent` | `#8B2020` | Rouge bordeaux (masthead, filets encadrant) |
| `border` | `#2C1810` | Brun foncé (bordure décorative) |
| `divider` | `#6B4C3B` | Filets intermédiaires |

### Typographie

Les polices sont **bundlées avec le projet** dans `assets/fonts/` (Google Fonts, licence OFL).

| Usage | Police | Fichier |
|-------|--------|---------|
| Masthead | **Playfair Display Bold** | `PlayfairDisplay-Bold.ttf` |
| Texte événement | **Libre Baskerville Regular** | `LibreBaskerville-Regular.ttf` |
| Texte événement italique | Libre Baskerville Italic | `LibreBaskerville-Italic.ttf` |
| Date/formule | **IM Fell English Regular** | `IMFellEnglish-Regular.ttf` |

`FONTS_DIR = Path(__file__).parent.parent / "assets" / "fonts"` — chemin absolu basé sur la localisation du fichier, insensible au répertoire d'exécution.

La commande `python -m ancnouv setup fonts` télécharge les polices depuis Google Fonts. Idempotente (ne re-télécharge pas si déjà présent). Les URLs exactes et l'avertissement sur les variantes (polices statiques `.ttf` — ne pas télécharger les variable fonts) sont documentés dans CLI.md (`setup fonts`).

> **[IMG-m6] Comportement si réseau indisponible :** `setup fonts` tente le téléchargement via `httpx`. En cas d'échec réseau (`httpx.HTTPError`, `ConnectionError`, timeout), l'erreur est loguée en ERROR et la commande se termine avec un code de sortie non nul. Le fichier police cible n'est pas créé (ou reste inchangé s'il existait déjà). Aucun fichier partiel n'est laissé sur disque (écriture atomique via fichier temporaire avant déplacement final). Résolution : vérifier la connexion réseau et relancer `setup fonts` (idempotent, reprend depuis zéro sans risque).

---

## Zones de l'image

**Sans thumbnail** (mode typographique pur) :
```
┌─────────────────────────────────────────────────────────┐  y=0
│  ┌───────────────────────────────────────────────────┐  │
│  │  ════════  MASTHEAD  ════════════════════════════  │  │  y=80
│  │  ─────────────────────────────────────────────────│  │
│  │  IL Y A 10 ANS — LE 21 MARS 2016                  │  │  y=220
│  │  ─────────────────────────────────────────────────│  │
│  │                                                   │  │  y=260
│  │  [TEXTE DE L'ÉVÉNEMENT — jusqu'à 18 lignes]       │  │
│  │                                                   │  │
│  │  ─────────────────────────────────────────────────│  │
│  │  Source : Wikipédia                               │  │  y=1290
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘  y=1350
```

**Avec thumbnail** (quand `event.image_url` est disponible) :
```
┌─────────────────────────────────────────────────────────┐  y=0
│  ┌───────────────────────────────────────────────────┐  │
│  │  ════════  MASTHEAD  ════════════════════════════  │  │  y=80
│  │  ─────────────────────────────────────────────────│  │
│  │  IL Y A 10 ANS — LE 21 MARS 2016                  │  │  y=220
│  │  ─────────────────────────────────────────────────│  │
│  │                                                   │  │  y=260
│  │  ┌─────────────────────────────────────────────┐  │  │
│  │  │         THUMBNAIL WIKIPEDIA                 │  │  │
│  │  │         (1000×300 px, centré)               │  │  │
│  │  └─────────────────────────────────────────────┘  │  │  y=560
│  │                                                   │  │
│  │  [TEXTE DE L'ÉVÉNEMENT — jusqu'à 12 lignes]       │  │  y=600
│  │                                                   │  │
│  │  ─────────────────────────────────────────────────│  │
│  │  Source : Wikipédia                               │  │  y=1290
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘  y=1350
```

### Règles de traitement du thumbnail

| Cas | Comportement |
|-----|-------------|
| Ratio paysage (> 1.5:1) | Letterbox : redimensionner en `width=W_INNER`, centrer verticalement, bandes fond papier |
| Ratio portrait (< 0.7:1) | Crop centré : **fit en largeur** (`new_w=W_INNER`), crop hauteur au centre |
| Ratio carré (0.7–1.5:1) | Resize direct vers `(W_INNER, 300)` — **distorsion intentionnelle** : ratio légèrement modifié (max ~1.05× pour les images dans la plage 0.7–1.5), documentée comme acceptable pour les thumbnails encyclopédiques de taille proche du carré |
| Téléchargement échoue | Mode typographique pur |
| `image_url` est NULL | Mode typographique pur |

**Crop portrait — logique correcte :** fitter en largeur (`new_w = TARGET_W`, `new_h = int(TARGET_W / ratio)`), puis `y_crop = (new_h - TARGET_H) // 2`, `zone = resized.crop((0, y_crop, TARGET_W, y_crop + TARGET_H))`. Fitter en hauteur serait incorrect : `new_w = int(TARGET_H * ratio) < TARGET_W` → `x_crop` négatif → zone vide.

> **Invariant `new_h >= TARGET_H` :** garanti si le ratio source est `< TARGET_W / TARGET_H` (c.-à-d. `< 1080/300 = 3.6`). Pour les images portrait (`ratio < 0.7`), `new_h = TARGET_W / ratio > TARGET_W / 0.7 ≈ 1543 >> TARGET_H = 300` — l'invariant est toujours satisfait. Une image de résolution trop basse (ex: 10×20 px) sera upscalée par Pillow ; le crop reste valide mais la qualité est dégradée.

Zone thumbnail : `W_INNER × 300 px` (`W_INNER = 1080 - 2*PADDING = 1000 px`), `x_offset = PADDING = 40 px`.

### Dimensions des zones

| Zone | Mode typo (sans thumbnail) | Mode photo (avec thumbnail) |
|------|---------------------------|----------------------------|
| Masthead | y: 60–170, 110px | y: 60–170, 110px |
| Date/formule | y: 180–250, 70px | y: 180–250, 70px |
| Thumbnail | — | y: 260–560, 300px |
| Texte principal | y: 260–1200 (~18 lignes) | y: 600–1200 (~12 lignes) |
| Footer | y: 1250–1330, 80px | y: 1250–1330, 80px |

---

## Constantes de mise en page

```python
# Constantes de mise en page (module-level)
MARGIN = 20          # marge extérieure (bordure décorative)
PADDING = 40         # padding intérieur (texte à l'intérieur de la bordure)
W_INNER = 1000       # = 1080 - 2 * PADDING

# Import numpy au niveau module (pas dans _draw_paper_texture) — fail-fast si absent
import numpy as np   # [IMG-C7] intentionnellement au niveau module, voir note ci-dessous
```

> **[IMG-C7] `import numpy` au niveau module :** placer `import numpy as np` dans le corps de `_draw_paper_texture` (import local) cacherait l'erreur jusqu'au premier appel. Placé au niveau module, `ImportError` est levée dès `from ancnouv.generator import image` — détectable au démarrage via `health`. Ce n'est pas une constante mais un import de module ; il apparaît dans cette section car il conditionne la disponibilité de `_draw_paper_texture`.

> **Version numpy :** `numpy==1.*` requis (voir `requirements.txt`). La v2.x de numpy a modifié l'API de `np.random.randint` (type de retour et broadcasting). Sans contrainte de version, `pip install numpy` installe la v2 et `_draw_paper_texture` peut produire des résultats incorrects silencieusement.

> **[IMG-m5 / IMG-17] Relation entre MARGIN, PADDING et les repères y :** les y-repères dans les diagrammes et tableaux ci-dessus sont des coordonnées **absolues** depuis le haut de l'image (y=0). La bordure décorative est tracée à `MARGIN=20` px des bords — rectangle de `(20, 20)` à `(1060, 1330)`. `MARGIN` est l'espace entre le bord de l'image et le rectangle de bordure décoratif. `PADDING=40` est l'espace entre le rectangle de bordure et le début du contenu textuel. Les coordonnées y du contenu commencent donc à `MARGIN + PADDING = 60` depuis le haut de l'image, et à `PADDING = 40` depuis l'intérieur du rectangle de bordure. `MARGIN` est un paramètre du tracé de la bordure uniquement ; il n'est **pas** un offset supplémentaire à ajouter aux y-repères du contenu — les deux systèmes sont indépendants.

---

## Signatures des fonctions de rendu (`generator/image.py`)

```python
async def fetch_thumbnail(image_url: str | None) -> Image.Image | None: ...
def generate_image(source: Event | RssArticle, config: Config, output_path: Path, thumbnail: Image.Image | None = None) -> Path: ...
def _generate_image_inner(source: Event | RssArticle, config: Config, output_path: Path, thumbnail: Image.Image | None = None) -> Path: ...
def _load_fonts() -> dict: ...
# Retourne {"masthead": font_72, "date_large": font_40, "date_small": font_32,
#            "body": font_28, "body_italic": font_28_italic, "footer": font_24}
def _draw_decorative_border(draw: ImageDraw, W: int, H: int) -> None: ...
def _draw_masthead(draw: ImageDraw, W: int, fonts: dict) -> None: ...
def _draw_date_banner(draw: ImageDraw, W: int, time_ago: str, date_str: str, fonts: dict) -> None: ...
def _draw_divider(draw: ImageDraw, W: int, y: int) -> None: ...
def _draw_footer(draw: ImageDraw, W: int, H: int, source_text: str, fonts: dict) -> None: ...
def _draw_thumbnail(img: Image.Image, thumbnail: Image.Image, y: int, W: int) -> None: ...
def _draw_event_text(draw: ImageDraw, W: int, text: str, text_y: int, max_height: int, fonts: dict) -> None: ...
def _draw_paper_texture(img: Image.Image, intensity: int = 8) -> Image.Image: ...
def load_font(path: Path, size: int) -> ImageFont: ...
```

> **`RssArticle` ici est `ancnouv.db.models.RssArticle`** (le modèle ORM retourné par `select_article`), pas la dataclass de transport `RssFeedItem` de `fetchers/base.py`. Le champ `image_url` est présent dans les deux types : `Event.image_url` (Wikipedia thumbnail URL) et `RssArticle.image_url` (image extraite du flux RSS). Voir DATABASE.md — colonnes `events.image_url` et `rss_articles.image_url`.

**`generate_image`** : wrapper qui appelle `_generate_image_inner` dans un try-except. Toutes les exceptions Pillow sont capturées et réemballées dans `GeneratorError(f"Échec génération image pour event.id={event.id}: {exc}")`. **[IMG-18]** `GeneratorError` est importée via `from ancnouv.exceptions import GeneratorError` — définie dans `ancnouv/exceptions.py`, hérite de `AncNouvError`.

**`_generate_image_inner`** : séquence d'appel **[IMG-8]** :
1. Créer `Image.new("RGB", (config.image.width, config.image.height), color=COLORS["background"])`
2. Si `config.image.paper_texture` : `img = _draw_paper_texture(img, intensity=config.image.paper_texture_intensity)` — `_draw_paper_texture` retourne une **nouvelle image** (ne modifie pas `img` en place), réaffecter obligatoire
3. `_draw_decorative_border`, `_draw_masthead`
4. **Dessin du banner date — extraction des paramètres selon le type de source** :
   - Si `isinstance(source, Event)` : **Guard `source.year is None`** : si `None`, `time_ago = "Date inconnue"` et `date_str = "Date inconnue"` ; sinon `compute_time_ago(source.year, source.month, source.day)` et `format_historical_date(source.year, source.month, source.day)`. `text = source.description`. `source_text = "Source : Wikipédia"` (ou `"Source : Wikipedia (EN)"` si `source.source_lang == "en"`).
   - Si `isinstance(source, RssArticle)` : `time_ago = "ACTUALITÉ RSS"`. `date_str = format_historical_date_from_datetime(source.published_at)` (helper local retournant `published_at.strftime("%d %B %Y")` en français). `text = f"{source.title}\n\n{source.summary}"` (tronqué par `truncate_for_image`). `source_text = f"Source : {source.feed_name}"`.
   - `_draw_date_banner(draw, W, time_ago, date_str, fonts)`
5. Si thumbnail disponible : télécharger et dessiner la thumbnail via `_draw_thumbnail`. **[IMG-6]** `_draw_thumbnail` garantit que tout le contenu est clipé à `y_max=560` (fin de la zone thumbnail). Le gap de 40px entre y=560 et `text_y=600` évite le chevauchement même si le thumbnail déborde légèrement.
6. Dessiner le texte de l'événement : `_draw_event_text(text_y=600, max_height=600)` (avec thumbnail) ou `_draw_event_text(text_y=260, max_height=940)` (sans)
7. Dessiner le footer — **trois appels `_draw_divider`** :
   - `_draw_divider(draw, W, y=175)` — après le masthead
   - `_draw_divider(draw, W, y=255)` — après la date banner
   - `_draw_divider(draw, W, y=1230)` — avant le footer
   - Puis `_draw_footer(draw, W, H=config.image.height, source_text, fonts)`
8. Sauvegarder : `img.save(output_path, "JPEG", quality=config.image.jpeg_quality, optimize=True)`

> La génération de légende (`format_caption`) appartient à `generate_post()` (appelé avant `generate_image`), pas à `_generate_image_inner`.
   > **[IMG-m8] `optimize=True` et performance RPi4 :** `optimize=True` déclenche un second passage Pillow pour calculer des tables de Huffman optimales (+20–50ms sur RPi4 vs sans optimize). Bilan mesuré : rendu Pillow ~80ms + texture numpy ~15ms + sauvegarde JPEG avec optimize ~30ms ≈ **125ms total < 1 seconde** — la contrainte de performance (section Contraintes) reste respectée. L'économie de taille de fichier (~5–10%) justifie le surcoût.

**Tailles de police `_load_fonts()` :**

| Clé retournée | Police | Taille | Zone d'utilisation |
|--------------|--------|--------|-------------------|
| `"masthead"` | PlayfairDisplay-Bold | **72px** | Masthead (zone 110px) |
| `"date_large"` | IMFellEnglish-Regular | **40px** | `time_ago` dans date banner |
| `"date_small"` | IMFellEnglish-Regular | **32px** | `date_str` dans date banner |
| `"body"` | LibreBaskerville-Regular | **28px** | Texte événement principal |
| `"body_italic"` | LibreBaskerville-Italic | **28px** | Réservé v1 (non utilisé activement) |
| `"footer"` | LibreBaskerville-Regular | **24px** | Footer (source, zone 80px) |

**`_draw_decorative_border`** : rectangle simple, **épaisseur 4px**, couleur `COLORS["border"]` (`#2C1810` — brun foncé). Dessiné à partir de `(MARGIN, MARGIN)` jusqu'à `(W - MARGIN, H - MARGIN)` via `draw.rectangle(...)`. Style : filet simple (pas de double filet ni coins décorés en v1).

**`_draw_masthead`** — texte et centrage :

Affiche `config.image.masthead_text` (défaut : `"ANCIENNES NOUVELLES"`) — configurable dans `config.yml`. **[IMG-4]** Le champ `image.masthead_text` est identique pour Mode A et Mode B en v1. Police : `fonts["masthead"]` (PlayfairDisplay-Bold 72px). Couleur : `COLORS["accent"]` (`#8B2020` — rouge bordeaux).

**[IMG-10] Centrage horizontal — méthode retenue :** `draw.textbbox((0, 0), text, font=font)` pour obtenir la largeur, puis `x = (W - text_width) // 2`. Cette méthode est plus précise que `anchor="mt"` qui peut décaler selon les descenders.

**`_draw_masthead`** — centrage vertical :
- Calculer `bbox = draw.textbbox((0, 0), text, font=font)`
- Hauteur du texte : `text_h = bbox[3] - bbox[1]`
- Position y pour centrer dans la zone masthead (y=60–170, centre y=115) : `y = 115 - text_h // 2`
- **Ne pas utiliser** `(bbox[1] + bbox[3]) // 2` : cette expression est l'offset du centre de la bbox depuis l'origine (pas la hauteur), ce qui donne un résultat incorrect lorsque `bbox[1] != 0` (descenders, polices avec métrique non nulle à l'origine).

**`_draw_date_banner`** — deux lignes centrées dans la zone y=180–260 :
1. `time_ago` (ex : "Il y a 10 ans") — police `fonts["date_large"]` (IMFellEnglish 40px), couleur `COLORS["text_secondary"]` (`#4A3728`), centré horizontalement, position **y=185** (s'étend jusqu'à environ y=225)
2. `date_str` (ex : "Le 21 mars 2016") — police `fonts["date_small"]` (IMFellEnglish 32px), couleur `COLORS["text_secondary"]`, centré horizontalement, position **y=230** (5px de marge après `time_ago`, s'étend jusqu'à environ y=262)

**[IMG-11]** Zone totale : y=185 à y=262, dans la zone disponible y=180–260. Les positions y=185 et y=230 garantissent l'absence de chevauchement entre les deux lignes.

Centrage horizontal : `x = (W - text_width) // 2` (via `draw.textbbox`). Les deux textes sont dessinés avec `draw.text(...)`.

**`_draw_event_text`** — algorithme de rendu :
1. Sélectionner la police : `fonts["body"]` (LibreBaskerville-Regular 28px)
2. Calculer l'interlignage : `line_height = 28 * 1.5 = 42px`
3. Appeler `wrap_text(draw, text, font, max_width=W_INNER)` → liste de lignes
4. Dessiner ligne par ligne depuis `y = text_y` : `draw.text((PADDING, y), line, fill=COLORS["text_primary"], font=font)`; incrémenter `y += line_height`
5. **Dépassement — limite opérationnelle : 18 lignes maximum [IMG-2].** La hauteur disponible divisée par `line_height` donne ~22 lignes théoriques, mais la limite effective est **18 lignes** compte tenu des marges, du padding et des lignes partielles en bas. Le rendu s'arrête à 18 lignes et tronque avec `"…"` si le texte est plus long. Ne **jamais** déborder dans la zone footer. L'ellipse indique visuellement que le texte est tronqué.

**`_draw_footer`** — disposition :
Police : `fonts["footer"]` (LibreBaskerville-Regular 24px). Couleur : `COLORS["text_secondary"]`.

**[IMG-3]** Géométrie du footer : le filet séparateur est à y=1230, la zone footer s'étend de y=1250 à y=1330 (hauteur 80px). `_draw_footer` positionne le texte à **y=1290** (centre vertical approximatif de la zone 1250–1330). `source_text` centré horizontalement. S'adapte automatiquement si les dimensions de l'image changent (position calculée depuis `H`).

**`_draw_paper_texture`** : bruit aléatoire numpy sur les pixels. `np.random.randint(-intensity, intensity+1, arr.shape, dtype=np.int16)`, clipé dans [0, 255]. Retourne une **nouvelle `Image.Image`** (ne modifie pas l'image d'entrée en place). ~15ms sur RPi4 pour 1080×1350.

> **[IMG-16]** La valeur effective de `intensity` vient de `config.image.paper_texture_intensity` (défaut : 8, défini dans `ImageConfig`). La valeur par défaut dans la signature Python (`intensity=8`) n'est utilisée que si la fonction est appelée directement sans config (ex: tests unitaires isolés).

> **[IMG-m7] ⚠️ `_draw_paper_texture` retourne une nouvelle image — réaffectation obligatoire.** La fonction ne modifie pas `img` en place. Oublier la réaffectation produit une image sans texture, sans message d'erreur ni exception :
> ```python
> # ✗ Incorrect — img reste inchangé
> _draw_paper_texture(img)
> # ✓ Correct
> img = _draw_paper_texture(img, intensity=config.image.paper_texture_intensity)
> ```

**`fetch_thumbnail`** : télécharge via `httpx.AsyncClient(timeout=5)`. `import httpx` est au **niveau du module** (`generator/image.py`) — si `httpx` n'est pas installé, l'import échoue au démarrage avec `ImportError` (comportement fail-fast). **[IMG-15]** `RssArticle.image_url` est nullable — `fetch_thumbnail(None)` et `fetch_thumbnail("")` retournent `None` sans erreur (guard : `if not image_url: return None`). Retourne aussi `None` si le statut HTTP n'est pas 200 ou si toute exception est levée. Version : `httpx==0.*` (voir `requirements.txt`). **Impact du timeout 5s sur la latence :** `fetch_thumbnail` est appelée avant `generate_image` dans `generate_post`. Dans le pire cas (thumbnail indisponible, timeout atteint), le cycle complet de `generate_post` dure ~5s au lieu de ~0.1s. Sur RPi4 à 10h30 (déclenchement JOB-3), cela bloque la boucle asyncio pendant 5s — acceptable car les timeouts réseau sont rares. Si un feed de thumbnails est systématiquement lent (> 3s), passer `timeout=3` dans `fetch_thumbnail`.

**`load_font`** : si `path` n'existe pas, log WARNING et retourne `ImageFont.load_default()`.

**[IMG-13] `_load_fonts` — comportement selon la police manquante :**
- `LibreBaskerville-Italic.ttf` absent : log **WARNING** (pas ERROR) — la police italique n'est pas utilisée activement en v1.
- `LibreBaskerville-Regular.ttf`, `PlayfairDisplay-Bold.ttf`, `IMFellEnglish-Regular.ttf` absentes : log **ERROR** — ces polices sont requises pour le rendu correct de l'image (masthead, texte corps, date).

---

## Orchestration dans `generator/__init__.py`

```python
async def generate_post(session: AsyncSession) -> Post | None: ...
```

> **Config via `get_config()` :** `generate_post` obtient la configuration via `get_config()` (singleton du contexte partagé dans `scheduler/context.py`) — **pas** via un paramètre `config`. Ne pas ajouter `config` en paramètre à cette fonction. `get_image_path`, `generate_image`, `format_caption` reçoivent `config` extrait de `get_config()` en interne.

`get_image_path` est défini dans `generator/__init__.py` (ou `generator/image.py`) :

```python
def get_image_path(config: Config) -> Path:
    images_dir = Path(config.data_dir) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    return images_dir / f"{uuid4()}.jpg"
```

Séquence :
1. `select_event(session, date.today())` (Mode A) ou `select_article(session, config)` (Mode B) selon le ratio (voir DATA_SOURCES.md [DS-3]). Résultat : `source` est soit un `Event` soit un `RssArticle` (ORM).
2. `thumbnail = await fetch_thumbnail(source.image_url)` — async (HTTP httpx), retourne `Image.Image | None`. `source.image_url` est disponible sur les deux types (`Event.image_url` et `RssArticle.image_url`).
3. `output_path = get_image_path(config)` — construit `Path(config.data_dir) / "images" / f"{uuid4()}.jpg"`, crée le dossier si absent.
4. `generate_image(source, config, output_path, thumbnail=thumbnail)` — **synchrone** (CPU-bound, Pillow). Patron d'appel depuis `generate_post` (async) : appel direct sans `await` ni `asyncio.to_thread`. Acceptable car ~100ms sur RPi4 — la boucle asyncio est bloquée ≤ 100ms, ce qui est dans les limites de PTB v20+ (timeout de polling 30s). Si la plateforme cible est plus lente (> 500ms observé), envelopper dans `await asyncio.to_thread(generate_image, source, config, output_path, thumbnail=thumbnail)`.
4. Mode A : `format_caption(source, config)` — Mode B : `format_caption_rss(source, config)` (voir `generator/caption.py`)
5. Insérer `Post(event_id=source.id if isinstance(source, Event) else None, article_id=source.id if isinstance(source, RssArticle) else None, caption=caption, image_path=str(output_path), status="pending_approval")`
6. Mettre à jour `source.last_used_at = now()` — **à la génération, pas à la publication** (voir DATABASE.md TC-5). **Conséquence :** un post rejeté ou expiré maintient `last_used_at` mis à jour, ce qui bloque la source dans la fenêtre de déduplication (`deduplication_window_days`) même si aucune publication n'a eu lieu. Ce comportement est intentionnel — éviter de proposer la même source en succession rapide même si la première tentative n'a pas abouti.
7. `session.add(post)`, `session.commit()`

Fichier image nommé `{uuid4()}.jpg` dans `config.data_dir / "images" /`. `config.data_dir` défaut : `"data"` (voir CONFIGURATION.md — champ racine). Le dossier `data/images/` est créé si absent (`parents=True, exist_ok=True`). Identique à la valeur dans la section "Nommage des fichiers".

---

## Utilitaires (`utils/date_helpers.py`)

```python
def compute_time_ago(year: int, month: int | None, day: int | None) -> str: ...
def format_historical_date(year: int, month: int | None, day: int | None) -> str: ...
```

**`compute_time_ago`** : retourne la formule temporelle narrative selon [SPEC-2.2].
- Si `month` ou `day` est `None` : retourne l'année seule
- `< 1 mois` → "Il y a moins d'un mois"
- `1–11 mois` → "Il y a N mois"
- `1 an` → "Il y a 1 an"
- `N ans` → "Il y a N ans"
- Années négatives (av. J.-C.) : calculer `delta = abs(year) + today.year` (ex : année -44, today.year = 2026 → delta = 44 + 2026 = 2070 ans). Note : `year = 0` n'existe pas dans le calendrier grégorien/julien (après l'an 1 av. J.-C. vient l'an 1 apr. J.-C.) — les événements Wikipedia utilisent `year = -1` pour l'an 1 av. J.-C.

**`format_historical_date`** : retourne la date formatée en français.
- `format_historical_date(2016, 3, 21)` → `"21 mars 2016"`
- `format_historical_date(-44, 3, 15)` → `"15 mars 44 av. J.-C."`

---

## Utilitaires (`utils/text_helpers.py`)

```python
def wrap_text(draw: ImageDraw, text: str, font: ImageFont, max_width: int) -> list[str]: ...
def truncate_for_image(text: str, max_chars: int = 500) -> str: ...
```

**`wrap_text`** : découpe le texte en lignes ne dépassant pas `max_width` pixels (via `draw.textlength`). Word-wrap : un mot trop long est placé seul sur sa ligne. Les `\n` explicites dans le texte sont traités comme des sauts de ligne forcés (split préalable sur `\n` avant le wrapping word-by-word).

> **[IMG-12] Mot unique dépassant `max_width` :** un mot seul dépassant `max_width` pixels est affiché tel quel sur sa propre ligne (Pillow ne supporte pas la coupure de mot nativement). Si cela provoque un débordement visuel, le texte peut être tronqué côté droit. Recommandation : appliquer une troncature avec `"..."` si un mot seul dépasse `max_width * 1.2`.

**`truncate_for_image`** : pré-filtre à 500 caractères (borne conservative). Si `len(text) > max_chars`, tronquer au dernier mot entier (`rsplit(" ", 1)[0]`) et ajouter `"…"`. La limite visuelle réelle (lignes) est gérée par `_draw_event_text(max_height)`.

**`_draw_event_text`** — taille de police et interlignage : taille de police 28px (Libre Baskerville), interlignage = `font.size * 1.5` (≈ 42px). Pour `max_height=940`, le calcul brut donne ~22 lignes théoriques. La limite opérationnelle est **18 lignes maximum** — les marges, padding et lignes partielles en bas réduisent la capacité effective. Le wrapping s'arrête à 18 lignes et tronque avec `"..."` si le texte est plus long. **[IMG-2]**

### Fonctions de formatage de légende (`generator/caption.py`)

```python
def format_caption(event: Event, config: Config) -> str: ...
def format_caption_rss(article: RssArticle, config: Config) -> str: ...
def truncate_caption(text: str, max_chars: int = 300) -> str: ...
```

**`format_caption`** (Mode A) : formule temporelle + description tronquée à 300 chars + attribution source (`config.caption.source_template_fr` ou `_en`) + hashtags (voir [SPEC-2.3]).

**`format_caption_rss`** (Mode B) : **[IMG-7]** La formule temporelle est **obligatoire** (SPEC-2.3) dans la légende Mode B. Format :
```
Il y a 3 mois, le 21 décembre 2025 :

{article.title}

{truncate_caption(article.summary)}

Source : {article.feed_name}
{hashtags}
```
Éléments obligatoires : formule temporelle, titre de l'article, extrait du résumé (tronqué si > 300 chars), nom du flux source, hashtags configurables.

`truncate_caption` : si `len(text) > max_chars`, tronquer au dernier mot entier (`rsplit(" ", 1)[0]`) et ajouter `"..."`. Limite Telegram : 1024 chars pour `send_photo` — `format_caption` / `format_caption_rss` doivent respecter cette limite.

### Tableau de troncature

| Contexte | Mécanisme | Limite |
|----------|-----------|--------|
| Image — pré-filtre | `truncate_for_image` (`utils/text_helpers.py`) | 500 caractères |
| Image — visuel (sans thumbnail) | `_draw_event_text(max_height=940)` | ~18 lignes |
| Image — visuel (avec thumbnail) | `_draw_event_text(max_height=600)` | ~12 lignes |
| Légende Instagram (Mode A) | `truncate_caption` (`generator/caption.py`) : tronquer à 300 chars | Voir [SPEC-2.3] |
| Légende Instagram (Mode B) | `truncate_caption` sur `article.summary` : tronquer à 300 chars | — |
| Légende Instagram — total | Vérification finale recommandée (SPEC-2.3) | 2200 caractères (limite API Instagram) |

> **Distinction `truncate_for_image` vs `truncate_caption` :** `truncate_for_image` (500 chars, `utils/text_helpers.py`) prépare le texte pour la génération d'image. `truncate_caption` (300 chars, `generator/caption.py`) formate la description pour la légende Instagram. Les deux opèrent sur des données distinctes — ne pas les confondre.

> **[IMG-14] Vérification globale de longueur de la légende :** vérifier que la légende complète (formule temporelle + texte tronqué à 300 chars + source + hashtags) reste sous **2200 chars** (limite API Instagram). La troncature à 300 chars du texte devrait suffire en pratique, mais la vérification finale est recommandée pour les hashtags longs.

---

## Nommage des fichiers

Images stockées dans `{config.data_dir}/images/{uuid4()}.jpg`. L'UUID garantit l'unicité. Le dossier est créé si absent (`parents=True, exist_ok=True`).

---

## Gestion des polices manquantes

Si une police est absente, `load_font` retourne `ImageFont.load_default()` (Pillow built-in) avec un log WARNING. L'image est générée avec une typographie dégradée mais sans crash.

> **Comportement dégradé :** `ImageFont.load_default()` a une taille fixe de 10px. L'interlignage calculé à partir de `font.size` sera ~15px au lieu de ~42px pour une police normale, causant un chevauchement extrême des lignes de texte sur l'image. L'image reste techniquement valide (publiable) mais visuellement inutilisable. Résolution : exécuter `python -m ancnouv setup fonts` (voir CLI.md).

---

## Templates par époque (v2 — SPEC-7bis)

> Non implémenté en v1. Cette section définit l'architecture pour v2.

| Époque | Années | Style | Palette | Polices |
|--------|--------|-------|---------|---------|
| Antiquité/Moyen Âge | < 1500 | Parchemin | Ocre, brun foncé | Serif historique |
| Époque moderne | 1500–1799 | Gazette | Sépia, crème | Serif classique |
| XIXe siècle | 1800–1899 | Journal N&B | Gris/blanc cassé | Baskerville |
| 1re moitié XXe | 1900–1959 | Presse illustrée | N&B jauni | Condensed |
| 2e moitié XXe | 1960–1999 | Journal moderne | Blanc, bleu, rouge | Sans-serif |
| XXIe siècle | 2000+ | Épuré | Blanc, noir, accent | Contemporaine |

Sélection du template :
- Mode A (Wikipedia) : déterminé par `event.year`
- Mode B (RSS) : toujours style "XXIe siècle" (SPEC-7bis.1)
- Override : `config.image.force_template` (SPEC-7bis.4)

En v2, ajouter : `_get_template_config(year: int | None, force: str | None) -> TemplateConfig`
`TemplateConfig` est un dataclass avec : `palette`, `fonts`, `layout` params.
