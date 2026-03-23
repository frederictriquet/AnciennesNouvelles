# Déploiement

> Référence : [SPEC-4.1], C-4.1.2, C-4.1.6

---

## Prérequis — Avant tout code

Ces étapes sont **manuelles et obligatoires**. Sans elles, l'application ne peut pas fonctionner.

### 1. Créer le bot Telegram

1. Ouvrir Telegram → contacter **@BotFather**
2. Envoyer `/newbot`
3. Choisir un nom affiché (ex : `Anciennes Nouvelles`)
4. Choisir un @username (ex : `@ancnouv_bot`)
5. Récupérer le **token** au format `123456789:AABBccDDeeFFggHH...`
6. **Stocker dans `.env`** : `TELEGRAM_BOT_TOKEN=123456789:AABBccDDeeFFggHH...`

**Obtenir son propre Telegram user_id :**

- Contacter **@userinfobot** sur Telegram → il répond avec votre `id` numérique
- **Stocker dans `config.yml`** : `telegram.authorized_user_ids: [123456789]`

> Le bot ne répond qu'aux user_ids listés dans `authorized_user_ids`. Si la liste est vide, l'application refuse de démarrer.

---

### 2. Créer la Meta App (Instagram + Facebook)

L'API Meta exige une URL publique HTTPS pour accéder aux images avant publication. Suivre ces étapes dans l'ordre.

> **[DEP-C2] Déploiement derrière NAT (box domestique) :** la contrainte SPEC.md C-4.1.5 ("peut tourner derrière NAT") s'applique au **bot Telegram uniquement** (polling sortant, aucun port entrant requis). Elle ne s'applique **pas** au serveur d'images qui nécessite une URL HTTPS publiquement accessible (RF-3.4.1). En v1, un déploiement entièrement derrière NAT sans VPS n'est pas supporté pour la publication Meta. Options :
> - **A (recommandée) : VPS** — même un VPS à 3–5€/mois suffit. Rediriger les images vers le VPS (résidence → VPS via SSH reverse tunnel) — complexe.
> - **B : Tunnel ngrok/Cloudflare Tunnel** — exposer le port 8765 local via un tunnel. Convient pour les tests mais peu fiable pour la production (downtime réseau domestique).
> - **C : Héberger les images ailleurs** (S3, Cloudinary) — non supporté en v1.
>
> En v1, un déploiement entièrement derrière NAT sans VPS n'est **pas supporté** pour la publication Meta. Le bot Telegram et la génération d'images fonctionnent, mais les publications Instagram/Facebook échouent.

#### 2a. Compte Instagram Professional

Un compte **Personnel** ne fonctionne pas avec l'API Graph Meta.

1. Dans l'app Instagram → Profil → ☰ → Paramètres → Compte
2. Faire défiler jusqu'à **Passer à un compte professionnel**
3. Choisir **Créateur** (pour un compte individuel) ou **Entreprise**

#### 2b. Page Facebook

Une Page Facebook est **obligatoire**, même si vous ne publiez pas sur Facebook.

1. Aller sur [facebook.com/pages/create](https://www.facebook.com/pages/create)
2. Créer une Page (ex : "Anciennes Nouvelles")
3. Récupérer le **Page ID** : visible dans les paramètres de la Page → À propos → ID de page

#### 2c. Lier Instagram à la Page Facebook (Business Portfolio)

1. Aller sur [business.facebook.com](https://business.facebook.com)
2. Créer un Business Portfolio (ou utiliser un existant)
3. Dans le Portfolio → Paramètres → Comptes → Comptes Instagram
4. Cliquer **Ajouter** → saisir les identifiants du compte Instagram Professional
5. Dans le Portfolio → Paramètres → Comptes → Pages
6. Ajouter la Page Facebook créée à l'étape 2b

#### 2d. Créer la Meta App

1. Aller sur [developers.facebook.com](https://developers.facebook.com) → **Mes apps** → **Créer une app**
2. Type d'utilisation : **Autre**
3. Type d'application : **Business**
4. Renseigner le nom (ex : "AnciennesNouvelles") et l'email de contact
5. Dans le tableau de bord de l'app → **Ajouter un produit** :
   - **Instagram Graph API** → Configurer
   - **Facebook Login pour les entreprises** → Configurer

#### 2e. Configurer les Redirect URIs

Dans **Facebook Login pour les entreprises** → Paramètres :

- URIs de redirection OAuth valides : `http://localhost:8080/callback`

> La commande `auth meta` démarre un serveur HTTP temporaire sur `localhost:8080` pour capturer le code OAuth. Ce Redirect URI doit correspondre exactement.

#### 2f. Permissions (scopes) requis

Dans le tableau de bord de l'app → **Examen de l'app** → Permissions et fonctionnalités :

| Scope | Utilité |
|-------|---------|
| `instagram_basic` | Lire les infos du compte Instagram |
| `instagram_content_publish` | Publier des médias Instagram |
| `instagram_creator_manage_content` | Requis pour les comptes **Créateur** (ignoré pour Business) |
| `pages_show_list` | Lister les Pages gérées |
| `pages_read_engagement` | Lire les métriques des Pages |
| `pages_manage_posts` | Publier sur la Page Facebook |

> En mode **développement** (non soumis à Meta) : seul le compte propriétaire de l'app peut être autorisé. Pour un usage personnel, le mode développement est suffisant.

#### 2g. Récupérer App ID et App Secret

Dans **Paramètres de l'app** → **Paramètres de base** :
- **ID de l'application** → `META_APP_ID` dans `.env`
- **Secret de l'application** → `META_APP_SECRET` dans `.env`

---

## Déploiement Docker (méthode recommandée — VPS)

> **[DEP-C1/C3] Docker : recommandé, pas obligatoire.** La contrainte SPEC.md C-4.1.6 stipule "Zéro dépendance à Docker (optionnel seulement)". Docker est présenté ici comme la méthode **recommandée** pour les déploiements VPS avec IP publique, car il simplifie HTTPS (via nginx + Let's Encrypt), l'isolation des services, et la reproductibilité. Il n'est pas requis — le déploiement systemd sans Docker est entièrement supporté (voir section ci-dessous). RF-3.4.1 qui mentionne "un conteneur dédié `ancnouv-images`" décrit l'architecture à deux processus, pas une obligation Docker — le déploiement systemd implémente cette même architecture.

Docker simplifie HTTPS, l'isolation des services et la reproductibilité sur un VPS avec IP publique.

### Architecture : deux conteneurs sur un VPS

```
VPS (IP publique)
├── nginx (TLS, Let's Encrypt)          → port 443 public
│   └── proxy /images/ → ancnouv-images:8765
├── ancnouv (app principale)            → réseau interne Docker
│   ├── scheduler + bot Telegram
│   ├── DB SQLite (data/ancnouv.db)
│   └── upload images → http://ancnouv-images:8765/images/upload
└── ancnouv-images (serveur d'images)  → 127.0.0.1:8765 (loopback)
    └── GET /images/*   (accès public via nginx)
    └── POST /images/upload  (accès privé depuis ancnouv seulement)
```

**Flux d'une publication :**
1. `ancnouv` génère l'image → upload via `http://ancnouv-images:8765/images/upload` (réseau Docker)
2. `ancnouv-images` stocke dans `/app/data/images/`
3. `ancnouv` passe l'URL publique `https://images.votre-domaine.com/images/{uuid}.jpg` à l'API Meta
4. Meta télécharge l'image via nginx → HTTPS → 127.0.0.1:8765

---

### `docker-compose.yml`

```yaml
# docker-compose.yml
version: "3.9"

services:
  ancnouv:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    depends_on:
      - ancnouv-images
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./config.yml:/app/config.yml:ro
      - ./.env:/app/.env:ro
      # ⚠️ DEP-B4 : ne PAS monter assets/ en :ro — setup fonts écrit dans assets/fonts/
      # Le :ro bloque le téléchargement des polices avec "Permission denied".
      # [D-04] Solution A (recommandée) : télécharger les polices AVANT docker compose up :
      #   python -m ancnouv setup fonts  (sur la machine hôte, hors Docker, dans .venv)
      #   puis monter en :ro — les polices sont déjà présentes dans ./assets/fonts/
      #   Si les polices sont absentes de ./assets/fonts/, elles sont aussi absentes
      #   du volume hôte → les deux conteneurs démarrent sans polices.
      # Solution B : laisser le volume en lecture-écriture (moins secure)
      - ./assets:/app/assets  # rw — requis si setup fonts est lancé dans le conteneur
    environment:
      - TZ=Europe/Paris
    networks:
      - ancnouv-net

  ancnouv-images:
    build:
      context: .
      dockerfile: Dockerfile.images
    restart: unless-stopped
    ports:
      # Exposé uniquement sur le loopback — nginx proxie depuis l'extérieur
      - "127.0.0.1:8765:8765"
    volumes:
      - ./data/images:/app/data/images
      - ./.env:/app/.env:ro
    networks:
      - ancnouv-net

networks:
  ancnouv-net:
    driver: bridge
```

---

### `Dockerfile` (application principale)

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Dépendances système pour Pillow
# libjpeg62-turbo-dev : codec JPEG obligatoire — sans lui, pip install Pillow réussit
# mais img.save(..., "JPEG") lève KeyError: encoder jpeg not available au runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi-dev \
    libjpeg62-turbo-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ancnouv/ ./ancnouv/
COPY assets/ ./assets/
# [D-06] Fichiers Alembic obligatoires — sans eux, db init et db migrate échouent dans le conteneur
COPY alembic.ini .
COPY ancnouv/db/migrations/ ./ancnouv/db/migrations/

# Créer les répertoires runtime (montés en volume en production)
RUN mkdir -p data/images logs

CMD ["python", "-m", "ancnouv", "start"]
```

---

### `Dockerfile.images` (serveur d'images)

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Le serveur d'images n'utilise qu'aiohttp
# ⚠️ SPEC-B4 : copier UNIQUEMENT image_hosting.py et __main__.py est insuffisant.
# `python -m ancnouv` requiert ancnouv/__init__.py (package marker) et argparse
# charge tout le module — ancnouv/config.py est importé même pour images-server.
# Solution : copier le package complet ou extraire run_image_server dans un script dédié.
# Approche retenue : copier le package minimal nécessaire.
# [DEP-C7] Limitation : requirements.txt complet est utilisé ici (Pillow, APScheduler, numpy, etc.)
# alors que ce container n'utilise qu'aiohttp + pydantic-settings.
# Raison : ancnouv/__main__.py importe config.py qui importe pydantic-settings — forçant
# l'installation de pydantic-settings[yaml] pour que le module charge sans ImportError.
# Amélioration possible v2 : créer requirements-images.txt avec seulement aiohttp + pydantic-settings.
# Impact v1 : image Docker ~300Mo au lieu de ~80Mo — acceptable sur VPS moderne.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Package minimal : __init__, __main__, config, publisher (pour image_hosting)
COPY ancnouv/__init__.py        ./ancnouv/__init__.py
COPY ancnouv/__main__.py        ./ancnouv/__main__.py
COPY ancnouv/config.py          ./ancnouv/config.py
COPY ancnouv/publisher/__init__.py          ./ancnouv/publisher/__init__.py
COPY ancnouv/publisher/image_hosting.py     ./ancnouv/publisher/image_hosting.py
# ⚠️ SPEC-B5 / [D-02] : `publisher/__init__.py` NE DOIT PAS contenir d'imports top-level de
# `InstagramPublisher` ou `FacebookPublisher` — ces modules ne sont pas copiés ici.
# Les imports de `InstagramPublisher`/`FacebookPublisher` doivent être locaux
# (à l'intérieur de `publish_to_all_platforms`) pour éviter ImportError au démarrage.
# Sans cette contrainte, `python -m ancnouv images-server` échoue avec
# `ImportError: cannot import name 'InstagramPublisher'` dès le chargement du module.

RUN mkdir -p data/images

# images-server ne charge PAS la config complète — lit uniquement IMAGE_SERVER_TOKEN
# depuis l'environnement (voir CLI.md — _dispatch_inner)
CMD ["python", "-m", "ancnouv", "images-server", "--port", "8765"]
```

---

### `.dockerignore`

```dockerignore
# Données runtime
data/
logs/
scheduler.db

# Secrets
.env

# Dev
.venv/
__pycache__/
*.pyc
*.pyo
.pytest_cache/
tests/

# VCS
.git/
.gitignore

# Docs (non nécessaires dans le conteneur) [DEP-m2]
# *.md exclut intentionnellement TOUS les fichiers Markdown (README.md, CLAUDE.md, docs/*.md)
# — aucun d'eux n'est utilisé à l'exécution. Réduit la taille de l'image de build context.
docs/
*.md
```

---

### nginx + TLS

> **[DEP-M3] Installation nginx et certbot :** si nginx et certbot ne sont pas encore installés sur le VPS :
> ```bash
> sudo apt-get update
> sudo apt-get install -y nginx certbot python3-certbot-nginx
> # Ouvrir les ports nécessaires (si ufw est actif)
> sudo ufw allow 80/tcp    # ACME challenge Let's Encrypt
> sudo ufw allow 443/tcp   # [DEP-M10] HTTPS (souvent fermé par défaut)
> sudo ufw reload
> sudo systemctl enable nginx
> sudo systemctl start nginx
> ```
> Si nginx et certbot sont déjà gérés sur le VPS, il suffit d'ajouter un sous-domaine.

**1. Ajouter l'entrée DNS** : `images.votre-domaine.com` → IP du VPS (via votre gestionnaire DNS habituel).

**2. Ajouter le bloc suivant à votre configuration nginx globale** (ex: dans le répertoire `sites-available/` ou votre fichier d'includes) :

```nginx
# ── Bloc HTTP (port 80) — OBLIGATOIRE pour la validation ACME (certbot) ──────
# Sans ce bloc, certbot ne peut pas émettre le certificat Let's Encrypt.
# certbot challenge-01 : GET /.well-known/acme-challenge/<token> sur port 80.
server {
    listen 80;
    server_name images.votre-domaine.com;

    # Challenge ACME Let's Encrypt (renouvellement automatique)
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    # Rediriger tout le reste vers HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}

# ── Bloc HTTPS (port 443) — actif après émission du certificat ───────────────
# ⚠️ Ce bloc ne peut démarrer que si les fichiers .pem existent déjà.
# Séquence correcte :
#   1. Démarrer nginx avec uniquement le bloc port 80 (commenter le bloc 443)
#   2. Émettre le certificat : certbot certonly --webroot -w /var/www/certbot -d images.votre-domaine.com
#   3. Décommenter le bloc 443 et recharger nginx
server {
    listen 443 ssl;
    server_name images.votre-domaine.com;

    # Certificat géré par certbot
    ssl_certificate     /etc/letsencrypt/live/images.votre-domaine.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/images.votre-domaine.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    # [DEP-M2] ssl_dhparam : généré par certbot >= 2.x dans options-ssl-nginx.conf.
    # Si absent (certbot < 2.x ou installation ancienne), générer manuellement :
    #   sudo openssl dhparam -out /etc/letsencrypt/ssl-dhparams.pem 2048
    # puis décommenter : ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location /images/ {
        proxy_pass http://127.0.0.1:8765/images/;
        proxy_set_header Host $host;
        proxy_read_timeout 10s;    # [DEP-m5] timeout de lecture aiohttp
        proxy_connect_timeout 5s;  # [DEP-m5] timeout de connexion
        expires 7d;
        add_header Cache-Control "public, max-age=604800";
    }

    location / {
        return 403;
    }
}
```

**3. Émettre le certificat** (première fois — certbot doit pouvoir valider sur port 80) :

```bash
# Créer le répertoire webroot si absent
sudo mkdir -p /var/www/certbot

# Émettre le certificat
sudo certbot certonly --webroot -w /var/www/certbot -d images.votre-domaine.com

# Décommenter ensuite le bloc 443 dans nginx.conf et recharger
```

**4. Recharger nginx :**

```bash
sudo nginx -t && sudo systemctl reload nginx
```

**5. Renouvellement automatique Let's Encrypt [DEP-M1]** (le certificat expire au bout de 90 jours) :

```bash
# Tester le renouvellement automatique (dry-run)
sudo certbot renew --dry-run

# Vérifier le timer systemd de renouvellement automatique (installé par certbot)
sudo systemctl status certbot.timer

# Si le timer n'est pas actif, ajouter dans /etc/cron.d/certbot-renew :
0 3 * * * root certbot renew --quiet --post-hook "systemctl reload nginx"
```

> `certbot` installé via `apt` configure automatiquement un timer systemd ou une cron entrée pour le renouvellement. Vérifier avec `systemctl list-timers | grep certbot`. Sans renouvellement automatique, le certificat expire au bout de 90 jours et nginx refuse de démarrer.

**5. Renseigner dans `config.yml` :**

```yaml
image_hosting:
  public_base_url: "https://images.votre-domaine.com"
  # Sans port : nginx termine TLS sur le port 443 standard (transparent pour le client)
  # Contraste avec le mode local sans nginx : public_base_url inclut le port (ex: ":8765")
  # car aiohttp écoute directement sur un port non-standard. Voir INSTAGRAM_API.md [IG-5].
```

---

### Configuration pour le déploiement Docker

```yaml
# config.yml — version Docker VPS (tout sur le même serveur)
image_hosting:
  backend: "remote"
  # URL publique (via nginx HTTPS) — ce que Meta voit
  public_base_url: "https://images.votre-domaine.com"
  # URL d'upload interne (réseau Docker) — jamais exposée publiquement
  remote_upload_url: "http://ancnouv-images:8765/images/upload"
```

```bash
# .env
TELEGRAM_BOT_TOKEN=123456789:AABBccDDeeFF...
META_APP_ID=1234567890123456
META_APP_SECRET=abcdef1234567890abcdef1234567890
# Générer : python3 -c "import secrets; print(secrets.token_hex(32))"
IMAGE_SERVER_TOKEN=votre_token_secret_64_caracteres_hex
# ID numérique du chat Telegram pour les notifications systemd (voir section systemd)
# Généralement le même que authorized_user_ids[0] — obtenir via @userinfobot sur Telegram
TELEGRAM_CHAT_ID=123456789
```

> **[DEP-A2] Authentification du serveur d'images :** Le serveur d'images valide le token sur chaque requête `POST /images/upload` via le header HTTP : `Authorization: Bearer <IMAGE_SERVER_TOKEN>`. L'application principale envoie automatiquement ce header lors de l'upload (via `config.image_server_token` lu depuis `.env`).

---

### Séquence d'initialisation (premier déploiement)

```bash
# 1. Cloner le dépôt
git clone https://github.com/votre-user/AnciennesNouvelles.git
cd AnciennesNouvelles

# 2. Créer et remplir les fichiers de configuration
cp config.yml.example config.yml
# Éditer config.yml :
#   - image_hosting.public_base_url = "https://images.votre-domaine.com"
#   - image_hosting.remote_upload_url = "http://ancnouv-images:8765/images/upload"
#   - telegram.authorized_user_ids = [votre_user_id]

cp .env.example .env
# Remplir : TELEGRAM_BOT_TOKEN, META_APP_ID, META_APP_SECRET, IMAGE_SERVER_TOKEN
# [DEP-C8] IMAGE_SERVER_TOKEN : requis pour authentifier les uploads vers ancnouv-images
#   Générer : python3 -c "import secrets; print(secrets.token_hex(32))"
# [DEP-C5] Restreindre les permissions du fichier secrets
chmod 600 .env
```

> **[DEP-B3] Contenu des fichiers example** : `config.yml.example` et `.env.example` sont versionnés dans le dépôt. Leur contenu complet est documenté dans `docs/CONFIGURATION.md`.
>
> Contenu minimal de `.env.example` :
> ```bash
> TELEGRAM_BOT_TOKEN=
> META_APP_ID=
> META_APP_SECRET=
> IMAGE_SERVER_TOKEN=
> ```
> Pour `config.yml.example`, copier le contenu de `docs/CONFIGURATION.md` — section "Fichier config.yml (complet et annoté)" en remplaçant les valeurs par défaut.
>
> **⚠️ `config.yml.example` pour Docker :** changer `backend: "local"` → `backend: "remote"` avant de copier en `config.yml`. Le déploiement Docker utilise deux containers séparés — `backend: "local"` y est incorrect et ne produira pas d'erreur immédiate (la validation passe), mais les images ne seront pas uploadées correctement vers `ancnouv-images`.

```bash
# 3. Créer les répertoires de données
mkdir -p data/images logs

# 3b. [DEP-M11] Pour Docker : s'assurer que config.yml utilise backend: "remote"
#      Le défaut de config.yml.example est "local" — le changer AVANT build
#      backend: "local" passe la validation mais les uploads échouent silencieusement

# 4. Construire les conteneurs
docker compose build

# 4b. Démarrer ancnouv-images EN PREMIER pour qu'il soit prêt avant auth meta [DEP-M9]
docker compose up -d ancnouv-images
# Attendre ~5s et vérifier
sleep 5 && docker compose logs ancnouv-images --tail 5

# 5. Initialiser la base de données (AVANT auth meta)
docker compose run --rm ancnouv python -m ancnouv db init

# 6. Télécharger les polices
docker compose run --rm ancnouv python -m ancnouv setup fonts

# 7. Authentifier Meta (OAuth interactif)
#
#    ⚠️ ÉTAPE OBLIGATOIRE si déploiement sur VPS sans écran :
#    auth meta démarre un serveur HTTP temporaire sur localhost:8080.
#    Le callback OAuth doit pouvoir atteindre ce port depuis votre navigateur local.
#    Sur un VPS distant, ouvrir un tunnel SSH AVANT de lancer la commande :
#
#      Terminal 1 (sur votre machine locale) :
#        ssh -L 8080:localhost:8080 user@votre-vps
#
#      Terminal 2 — sur le VPS (pas en local) :
docker compose run --rm -p 8080:8080 ancnouv python -m ancnouv auth meta
#
#    → auth meta affiche une URL → l'ouvrir dans votre navigateur LOCAL
#    → Autoriser l'app Meta → le callback arrive sur localhost:8080 via le tunnel
#    → Les tokens sont stockés automatiquement dans data/ancnouv.db

# 8. Activer les plateformes dans config.yml APRÈS que les tokens soient en DB
#    ⚠️ Ne pas activer AVANT auth meta : validate_meta bloque si user_id est vide
#    Éditer config.yml :
#      instagram.enabled: true
#      instagram.user_id: "<valeur affichée par auth meta>"
#      facebook.enabled: true
#      facebook.page_id: "<valeur affichée par auth meta>"

# 8b. Vérifier les tokens après auth meta [DEP-m3]
docker compose run --rm ancnouv python -m ancnouv health

# 9. Pré-collecter les données Wikipedia
docker compose run --rm ancnouv python -m ancnouv fetch --prefetch

# 10. Vérification finale
docker compose run --rm ancnouv python -m ancnouv health

# 11. Démarrer
docker compose up -d

# Vérifier les logs
docker compose logs -f
```

---

## Renouvellement des tokens Meta

Les tokens utilisateur Meta (`user_long`) expirent tous les 60 jours. L'application envoie des alertes progressives via Telegram (à J-30, J-14, J-7, J-3, J-1) et tente un refresh automatique à partir de J-7.

> **[D-10] Séquence de refresh automatique :** le refresh est tenté à J-7 et J-3. Les publications sont suspendues à J-1 si tous les essais de refresh ont échoué (`publications_suspended="true"` dans `scheduler_state`). Pour lever la suspension : `python -m ancnouv auth meta` — renouvelle le token ET réinitialise `publications_suspended="false"`.

**Procédure de renouvellement manuel** (si le refresh automatique échoue) :

```bash
# Sur la machine d'exécution (ou via SSH)
docker compose run --rm -p 8080:8080 ancnouv python -m ancnouv auth meta
```

Cette commande exécute le même flux OAuth que lors de l'initialisation. Les nouveaux tokens écrasent les anciens dans la table `meta_tokens` de `ancnouv.db`. Aucune perte de données.

> ⚠️ Nécessite un navigateur (même procédure que lors du premier déploiement — voir "Authentifier Meta" dans la séquence d'initialisation).

---

## Mises à jour

### Mise à jour systemd [DEP-M5]

```bash
PROJ=/home/ancnouv/AnciennesNouvelles

# Arrêter les services
sudo systemctl stop ancnouv ancnouv-images

# Récupérer les changements
sudo -u ancnouv git -C $PROJ pull

# Sauvegarder et migrer si nécessaire
sudo -u ancnouv $PROJ/.venv/bin/python -m ancnouv db status
sudo -u ancnouv $PROJ/.venv/bin/python -m ancnouv db backup
sudo -u ancnouv $PROJ/.venv/bin/python -m ancnouv db migrate

# Mettre à jour les dépendances Python si requirements.txt a changé
sudo -u ancnouv $PROJ/.venv/bin/pip install -r $PROJ/requirements.txt

# Redémarrer
sudo systemctl start ancnouv-images
sudo systemctl start ancnouv
sudo systemctl status ancnouv
```

### Mise à jour Docker

> **Downtime :** `docker compose down` entraîne un arrêt complet. Sur VPS bas de gamme (1 vCPU), `docker compose build` peut prendre 5–10 minutes. Pour les mises à jour sans migration DB, préférer `docker compose up -d --build` (reconstruction à chaud, downtime < 30s).

```bash
cd AnciennesNouvelles

# Récupérer les changements
git pull

# Vérifier l'état des migrations AVANT d'arrêter
docker compose run --rm ancnouv python -m ancnouv db status

# Si migration nécessaire : arrêter, sauvegarder, migrer
docker compose down
docker compose run --rm ancnouv python -m ancnouv db backup
docker compose run --rm ancnouv python -m ancnouv db migrate

# Si pas de migration : rebuild à chaud (réduction du downtime)
# docker compose up -d --build

# Reconstruire et redémarrer
docker compose build
docker compose up -d
```

> **[DEP-m6] `alembic` dans `requirements.txt` :** `alembic==1.*` est une dépendance directe déclarée dans `requirements.txt` (pas seulement transitive via SQLAlchemy). C'est intentionnel : les commandes `db migrate`, `db status`, `db rollback` l'invoquent directement.
>
> **Équivalents CLI ↔ Alembic :** `db migrate` = `alembic upgrade head`, `db status` = `alembic current`. Il n'existe **pas** de commande `db rollback` — utiliser directement `alembic downgrade -1` (ou `alembic downgrade <revision>`) pour les rollbacks. La commande `alembic history` reste disponible pour l'inspection de l'historique.

---

## Sauvegardes et rollback

### Sauvegarde manuelle

```bash
# Sauvegarde de la DB (depuis le VPS)
docker compose run --rm ancnouv python -m ancnouv db backup
# → Crée data/ancnouv_YYYYMMDD_HHMMSS.db

# Sauvegarde automatique (crontab root sur le VPS)
# ⚠️ DEP-I1 : inclure scheduler.db — les deux DBs forment un état cohérent.
# Sans scheduler.db, la restauration peut laisser des jobs orphelins.
# Rotation bornée : conserver les 7 dernières sauvegardes de scheduler.db
# (aligné sur database.backup_keep: 7 de ancnouv.db — cohérence inter-DBs)
# [DEP-M8] Le chemin /home/ancnouv/AnciennesNouvelles est canonique pour ce projet.
# Si le répertoire d'installation diffère, adapter ce chemin dans toutes les commandes cron.
# [D-08] Prérequis : sqlite3 doit être installé sur l'hôte pour VACUUM INTO
# Sur Debian/Ubuntu slim : apt-get install sqlite3
# Vérifier : which sqlite3
0 4 * * * cd /home/ancnouv/AnciennesNouvelles && \
    docker compose run --rm ancnouv python -m ancnouv db backup && \
    sqlite3 data/scheduler.db "VACUUM INTO 'data/scheduler_$(date +\%Y\%m\%d_\%H\%M\%S).db'" && \
    ls -t data/scheduler_*.db 2>/dev/null | tail -n +8 | xargs rm -f
```

**[DEP-M6] Sauvegarde de `data/images/` :** les images générées ne sont pas incluses dans `db backup`. Elles sont reproductibles (régénérées par `generate-test-image`) mais leur régénération en masse est coûteuse. Pour les inclure dans les sauvegardes :
```bash
# Archiver data/images/ dans une sauvegarde externe (rsync, tar, etc.)
rsync -a data/images/ /path/to/backup/images/
# Ou archiver
tar czf data/images_$(date +%Y%m%d).tar.gz data/images/
```
Les images sont conservées `config.content.image_retention_days` jours (défaut : 7). Une sauvegarde quotidienne de `data/images/` est donc optionnelle en v1.

**Inclure `scheduler.db` dans les sauvegardes :**

```bash
# ⚠️ DEP-I4 : une copie `cp` de SQLite pendant une écriture (mode WAL) peut produire
# une sauvegarde incohérente. Utiliser VACUUM INTO pour une copie atomique :
sqlite3 data/ancnouv.db "VACUUM INTO 'data/ancnouv_$(date +%Y%m%d_%H%M%S).db'"
# Pour scheduler.db, utiliser aussi VACUUM INTO par précaution (APScheduler avec
# SQLAlchemyJobStore peut activer WAL selon la version — ne pas supposer le mode journal) :
sqlite3 data/scheduler.db "VACUUM INTO 'data/scheduler_$(date +%Y%m%d_%H%M%S).db'"
```

> **`db backup`** utilise `VACUUM INTO` en interne pour garantir la cohérence de `ancnouv.db`.
> La sauvegarde manuelle via `cp` est correcte uniquement si l'app est arrêtée.

### Restauration

```bash
docker compose down

# Restaurer depuis une sauvegarde
cp data/ancnouv_20260320_040000.db data/ancnouv.db

# Vérifier l'état des migrations
docker compose run --rm ancnouv python -m ancnouv db status

docker compose up -d
```

### Rollback de migration Alembic (Docker)

```bash
docker compose down

# ⚠️ Toujours sauvegarder AVANT le downgrade — un downgrade peut supprimer des données
docker compose run --rm ancnouv python -m ancnouv db backup

# Voir l'historique
docker compose run --rm ancnouv alembic history

# Revenir à la version précédente
docker compose run --rm ancnouv alembic downgrade -1

# Ou revenir à une révision spécifique
docker compose run --rm ancnouv alembic downgrade <revision_id>

docker compose up -d
```

> En cas de corruption irrémédiable, restaurer depuis la sauvegarde physique avant de relancer Alembic.

### Rollback de migration Alembic (systemd sans Docker) — [D-11]

```bash
systemctl stop ancnouv

# Sauvegarder avant rollback
python -m ancnouv db backup

# Voir l'historique
.venv/bin/alembic history

# Revenir à la version précédente
.venv/bin/alembic downgrade -1

systemctl start ancnouv
```

---

## Monitoring

```bash
# État des conteneurs
docker compose ps

# Logs en temps réel
docker compose logs -f ancnouv
docker compose logs -f ancnouv-images

# [DEP-m7] Erreurs courantes dans les logs ancnouv-images :
# "KeyError: IMAGE_SERVER_TOKEN"  → .env non monté ou variable absente
# "OSError: [Errno 98] Address already in use"  → port 8765 occupé (autre processus)
# "aiohttp.web_exceptions.HTTPUnauthorized"  → TOKEN incorrect côté ancnouv
# Ces erreurs apparaissent dans "docker compose logs ancnouv-images"

# Vérification de santé
docker compose run --rm ancnouv python -m ancnouv health
# ✅ Base de données : OK (15 342 événements, 127 posts)
# ✅ Wikipedia API : OK
# ✅ Telegram Bot : OK (@ancnouv_bot)
# ✅ Token Meta : OK (expire dans 45 jours)  # [D-09] "Token Meta" = token utilisateur long durée (Instagram + Facebook)
# ✅ Serveur images : OK (http://ancnouv-images:8765)
# ✅ Scheduler : ACTIF (prochain post : 21/03/2026 16:00)
```

---

### Architecture Raspberry Pi + VPS (sans Docker) — [D-05]

Le RPi fait tourner le bot et le scheduler. Le VPS héberge les images.

**RPi (derrière NAT) :**
- `ancnouv start` avec `image_hosting.backend: "remote"`
- `image_hosting.remote_upload_url: "https://images.votre-domaine.com:8765/images/upload"`

**VPS (IP publique) :**
- `ancnouv images-server --port 8765` (ou service systemd)
- nginx avec TLS expose le port 443 → 8765

Connexion RPi → VPS pour l'upload : via réseau public avec `IMAGE_SERVER_TOKEN`.
Le bot Telegram (polling sortant depuis le RPi) ne nécessite aucun port entrant.

---

## Déploiement alternatif : systemd sans Docker (Linux)

> Pour les déploiements sans Docker (VPS minimal, Raspberry Pi). L'architecture reste la même : `ancnouv-images` tourne comme service séparé, nginx proxie HTTPS.

**Prérequis système :**

```bash
# [DEP-C4] Python 3.12+ obligatoire — python3 sur Debian 12 est souvent 3.11
# Vérifier : python3 --version
# Si < 3.12, installer Python 3.12 depuis deadsnakes PPA (Ubuntu/Debian) :
sudo apt-get install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev

# Dépendances système pour Pillow
apt-get install -y libjpeg-dev python3-dev build-essential
```

`libjpeg-dev` est obligatoire pour que Pillow compile le codec JPEG. Sans lui, `pip install pillow` réussit mais `img.save(..., "JPEG")` lève `KeyError: encoder jpeg not available` au runtime (voir IMAGE_GENERATION.md — section Contraintes techniques).

### Service application principale

Créer `/etc/systemd/system/ancnouv.service` :

```ini
[Unit]
Description=Anciennes Nouvelles — Bot Instagram
After=network-online.target ancnouv-images.service
Wants=network-online.target
# Protection contre les crash loops : max 3 redémarrages en 5 minutes
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=simple
User=ancnouv
Group=ancnouv
WorkingDirectory=/home/ancnouv/AnciennesNouvelles
Environment=TZ=Europe/Paris
EnvironmentFile=/home/ancnouv/AnciennesNouvelles/.env
ExecStart=/home/ancnouv/AnciennesNouvelles/.venv/bin/python -m ancnouv start
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ancnouv
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

### Service serveur d'images

Créer `/etc/systemd/system/ancnouv-images.service` :

```ini
[Unit]
Description=Anciennes Nouvelles — Serveur d'images
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ancnouv
Group=ancnouv
WorkingDirectory=/home/ancnouv/AnciennesNouvelles
Environment=TZ=Europe/Paris
EnvironmentFile=/home/ancnouv/AnciennesNouvelles/.env
ExecStart=/home/ancnouv/AnciennesNouvelles/.venv/bin/python -m ancnouv images-server --port 8765
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ancnouv-images
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
# Créer l'utilisateur dédié
sudo useradd -r -s /bin/false -m -d /home/ancnouv ancnouv
# Cloner le dépôt
sudo -u ancnouv git clone https://github.com/votre-user/AnciennesNouvelles.git /home/ancnouv/AnciennesNouvelles
cd /home/ancnouv/AnciennesNouvelles

# Fichiers de config
sudo -u ancnouv cp config.yml.example config.yml
# Éditer config.yml : image_hosting.backend = "remote", remote_upload_url = "http://localhost:8765/images/upload"
# telegram.authorized_user_ids, image_hosting.public_base_url
sudo -u ancnouv cp .env.example .env
# Remplir : TELEGRAM_BOT_TOKEN, META_APP_ID, META_APP_SECRET
# [DEP-C8] IMAGE_SERVER_TOKEN : requis — générer avec :
#   python3 -c "import secrets; print(secrets.token_hex(32))"
# [DEP-m1] TELEGRAM_CHAT_ID : facultatif — pour notifications de crash systemd
# [DEP-C5] Permissions secrets
sudo chmod 600 /home/ancnouv/AnciennesNouvelles/.env
sudo chown ancnouv:ancnouv /home/ancnouv/AnciennesNouvelles/.env

# [DEP-M4] Créer l'environnement virtuel avec Python 3.12+
sudo -u ancnouv python3.12 -m venv /home/ancnouv/AnciennesNouvelles/.venv
sudo -u ancnouv /home/ancnouv/AnciennesNouvelles/.venv/bin/pip install -r /home/ancnouv/AnciennesNouvelles/requirements.txt

# [DEP-C6] Séquence d'initialisation complète (systemd) — [D-03] ordre canonique
VENV=/home/ancnouv/AnciennesNouvelles/.venv/bin/python
PROJ=/home/ancnouv/AnciennesNouvelles

# 1. Initialiser la base de données (obligatoire en premier — auth meta requiert la DB)
sudo -u ancnouv $VENV -m ancnouv db init

# 2. Télécharger les polices
sudo -u ancnouv $VENV -m ancnouv setup fonts

# 3. Authentifier Meta
#    [DEP-M7] Sur VPS sans écran, ouvrir un tunnel SSH depuis votre machine locale AVANT :
#      Terminal local : ssh -L 8080:localhost:8080 user@votre-vps
#    Puis lancer auth meta sur le VPS :
sudo -u ancnouv $VENV -m ancnouv auth meta
#    → Ouvrir l'URL affichée dans votre navigateur LOCAL → autoriser → callback via tunnel

# 4. Pré-collecter les données Wikipedia
sudo -u ancnouv $VENV -m ancnouv fetch --prefetch

# 5. Vérifier les tokens et la configuration
sudo -u ancnouv $VENV -m ancnouv health

# 6. Activer et démarrer (images d'abord, puis app principale)
sudo systemctl daemon-reload
sudo systemctl enable ancnouv-images ancnouv
sudo systemctl start ancnouv-images
sudo systemctl start ancnouv

# Vérifier
sudo systemctl status ancnouv ancnouv-images
sudo journalctl -u ancnouv -f
```

> **[DEP-A5] Configuration `config.yml` spécifique au déploiement systemd :** En déploiement systemd, les deux services (`ancnouv` et `ancnouv-images`) tournent sur la **même machine** en réseau `localhost`. La valeur de `remote_upload_url` doit être `http://localhost:8765/images/upload` (et non `http://ancnouv-images:8765/images/upload` qui est spécifique au réseau Docker interne) :
>
> ```yaml
> # config.yml — version systemd (même machine)
> image_hosting:
>   backend: "remote"
>   public_base_url: "https://images.votre-domaine.com"
>   remote_upload_url: "http://localhost:8765/images/upload"  # localhost, pas ancnouv-images
> ```

**Notification Telegram sur crash :** ajouter dans `[Unit]` du service principal :

```ini
OnFailure=ancnouv-notify@%n.service
```

Créer `/etc/systemd/system/ancnouv-notify@.service` :

```ini
[Unit]
Description=Notification crash %i

[Service]
Type=oneshot
# [D-07] Vérifier que TELEGRAM_CHAT_ID est non vide avant d'envoyer la requête
ExecStart=/bin/sh -c 'if [ -n "$TELEGRAM_CHAT_ID" ]; then curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" -d "chat_id=${TELEGRAM_CHAT_ID}" -d "text=🚨 ancnouv en état FAILED après 3 crashs. Intervention manuelle requise."; fi'
EnvironmentFile=/home/ancnouv/AnciennesNouvelles/.env
```

---

## Structure des répertoires après déploiement

```
AnciennesNouvelles/
├── ancnouv/                # code source
├── assets/
│   └── fonts/              # polices téléchargées (setup fonts)
├── data/                   # données runtime (non versionné, monté en volume Docker)
│   ├── ancnouv.db          # base de données SQLite — créée par `db init`
│   ├── scheduler.db        # jobs APScheduler — créé automatiquement par APScheduler SQLAlchemyJobStore au premier `python -m ancnouv start`
│   ├── ancnouv_*.db        # sauvegardes horodatées (créées par `db backup`)
│   └── images/             # images générées (partagé entre les deux conteneurs)
├── logs/                   # logs rotatifs (non versionné)
├── docker-compose.yml      # orchestration Docker
├── Dockerfile              # image app principale
├── Dockerfile.images       # image serveur d'images
├── config.yml              # configuration non-secrète
├── .env                    # secrets (non versionné)
├── config.yml.example      # modèle (versionné)
└── .env.example            # modèle secrets (versionné, sans valeurs)
```

> **[DEP-A1] Création des bases de données :** `ancnouv.db` est créé par `python -m ancnouv db init` (étape de la séquence d'initialisation). `scheduler.db` est créé automatiquement par APScheduler `SQLAlchemyJobStore` au premier démarrage de l'application (`python -m ancnouv start`). Il n'y a rien à faire manuellement pour `scheduler.db`.
>
> **[DEP-m4] Distinction `scheduler.db` vs `scheduler_state` :** ces deux mécanismes sont **indépendants** :
> - `data/scheduler.db` — base SQLite d'APScheduler, créée automatiquement, contient les jobs programmés. Distincte de `ancnouv.db`.
> - Table `scheduler_state` — dans `data/ancnouv.db`, créée par la migration Alembic initiale (voir [DEP-A2] ci-dessous), contient l'état métier (paused, daily_post_count, etc.).
> Un redémarrage ne détruit pas `scheduler_state` (dans ancnouv.db). Un redémarrage ne détruit pas non plus `scheduler.db` (jobs persistants via SQLAlchemy jobstore).

> **[DEP-A2] `scheduler_state` : migration initiale obligatoire.** La table `scheduler_state` n'est pas un modèle ORM (`DeclarativeBase`) — `Base.metadata.create_all()` et l'autogenerate Alembic ne la créent pas. La migration initiale (générée via `alembic revision --autogenerate`) doit contenir un `op.execute("CREATE TABLE scheduler_state ...")` explicite. Sans cela, tout accès à `scheduler_state` (lecture de `paused`, `daily_post_count`, etc.) lève `OperationalError: no such table: scheduler_state` au premier démarrage. Voir DATABASE.md — section `scheduler_state`.

---

## `.gitignore` recommandé

> **Polices et `.gitignore` :** `assets/fonts/` est exclu de Git mais le Dockerfile copie `COPY assets/ ./assets/`. Si les polices ne sont pas téléchargées (`python -m ancnouv setup fonts`) **avant** `docker build`, le dossier sera vide dans l'image. Solution recommandée : télécharger les polices sur la machine hôte avant de construire, puis monter `./assets:/app/assets` en volume (comme documenté dans `docker-compose.yml`).

```gitignore
# Environnement
.venv/
__pycache__/
*.pyc
*.pyo

# Données runtime
data/
logs/

# Secrets
.env

# Polices (téléchargées au setup)
assets/fonts/

# Fichiers macOS/IDE
.DS_Store
.vscode/settings.json

# [DEP-m8] config.yml contient des IDs Telegram non-secrets (telegram.authorized_user_ids)
# mais aussi des infos spécifiques au déploiement (public_base_url, etc.).
# Stratégie recommandée : exclure config.yml de Git (l'opérateur gère sa propre copie)
# en ajoutant : config.yml
# Alternativement, versionner config.yml si le dépôt est privé et sans valeurs sensibles.
# config.yml.example est toujours versionné (modèle public, sans valeurs réelles).
```
