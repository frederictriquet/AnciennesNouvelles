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
