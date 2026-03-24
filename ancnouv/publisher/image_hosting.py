# Hébergement d'images [SPEC-3.4.1, docs/INSTAGRAM_API.md — IG-5A, IG-5B]
from __future__ import annotations

import asyncio
import errno as errno_module
import logging
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from aiohttp import web

from ancnouv.exceptions import ImageHostingError

if TYPE_CHECKING:
    from ancnouv.config import Config

logger = logging.getLogger(__name__)


async def upload_image(image_path: Path, config: "Config") -> str:
    """Retourne l'URL publique de l'image selon le backend configuré.

    - backend='local' : l'image est servie par start_local_image_server,
      l'URL est construite directement sans upload.
    - backend='remote' : upload multipart vers le VPS distant.
    """
    if config.image_hosting.backend == "local":
        return f"{config.image_hosting.public_base_url}/images/{image_path.name}"
    return await upload_to_remote(image_path, config)


async def upload_to_remote(image_path: Path, config: "Config") -> str:
    """Upload l'image vers le serveur distant via POST multipart.

    Retry x3 interne avec backoff (1s, 2s, 4s) pour erreurs 5xx et timeouts.
    Erreurs 4xx (401, 413, etc.) : non-retriables, ImageHostingError immédiate.
    Réponse succès attendue : {"filename": "..."}.
    """
    headers = {"Authorization": f"Bearer {config.image_server_token}"}
    delays = [1, 2, 4]

    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            async with httpx.AsyncClient() as client:
                with image_path.open("rb") as f:
                    response = await client.post(
                        config.image_hosting.remote_upload_url,
                        headers=headers,
                        files={"file": (image_path.name, f, "image/jpeg")},
                        timeout=60,
                    )

            # Erreurs 4xx : non-retriables
            if 400 <= response.status_code < 500:
                raise ImageHostingError(
                    f"Upload refusé (HTTP {response.status_code}) — "
                    "vérifier IMAGE_SERVER_TOKEN et la taille de l'image."
                )

            response.raise_for_status()
            data = response.json()
            filename = data["filename"]
            return f"{config.image_hosting.public_base_url}/images/{filename}"

        except ImageHostingError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < 3:
                logger.warning(
                    "Upload distant échoué (tentative %d/4) : %s — retry dans %ds",
                    attempt + 1,
                    exc,
                    delays[attempt],
                )
                await asyncio.sleep(delays[attempt])

    raise ImageHostingError(
        f"Upload distant échoué après 4 tentatives : {last_exc}"
    ) from last_exc


async def start_local_image_server(images_dir: Path, port: int) -> web.AppRunner:
    """Démarre le serveur HTTP statique pour servir data/images/ [IG-5B].

    Retourne l'AppRunner après setup et démarrage complet du TCPSite.
    Appelé dans main_async() si backend=local, avant recover_pending_posts [IG-5A].
    """
    app = web.Application()

    async def handle_get_image(request: web.Request) -> web.Response:
        # Protection path traversal : extraire uniquement le nom du fichier
        safe_name = Path(request.match_info["filename"]).name
        file_path = images_dir / safe_name
        if not file_path.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(file_path)

    app.router.add_get("/images/{filename}", handle_get_image)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Serveur d'images local démarré sur le port %d.", port)
    return runner


async def run_image_server(port: int = 8765, token: str = "") -> int:
    """Démarre le serveur d'images complet (GET + POST upload) [CLI.md — images-server].

    Sert data/images/ en GET public et expose POST /images/upload (authentifié).
    TOKEN est obligatoire — exit 1 immédiat si vide.
    EADDRINUSE → message explicite + exit 1 [ARCH-22].
    """
    if not token:
        print(
            "IMAGE_SERVER_TOKEN est vide — obligatoire pour démarrer le serveur d'images.",
            file=sys.stderr,
        )
        sys.exit(1)

    images_dir = Path("data") / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    app = web.Application()

    async def handle_get_image(request: web.Request) -> web.Response:
        safe_name = Path(request.match_info["filename"]).name
        file_path = images_dir / safe_name
        if not file_path.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(file_path)

    async def handle_upload(request: web.Request) -> web.Response:
        # Vérification Bearer token [IG-5B — sécurité handle_upload]
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {token}":
            raise web.HTTPUnauthorized()

        reader = await request.multipart()
        field = await reader.next()

        # field.filename peut être None [IG-5B]
        if field is None or not field.filename:
            raise web.HTTPBadRequest(reason="Aucun fichier fourni")

        # Protection path traversal : extraire uniquement le nom [IG-5B]
        safe_name = Path(field.filename).name
        if not safe_name:
            raise web.HTTPBadRequest(reason="Nom de fichier invalide")

        file_path = images_dir / safe_name
        with file_path.open("wb") as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                f.write(chunk)

        return web.json_response({"filename": safe_name})

    app.router.add_get("/images/{filename}", handle_get_image)
    app.router.add_post("/images/upload", handle_upload)

    runner = web.AppRunner(app)
    await runner.setup()

    try:
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
    except OSError as e:
        if e.errno == errno_module.EADDRINUSE:
            print(
                f"Port {port} déjà utilisé — arrêter le processus occupant ce port.",
                file=sys.stderr,
            )
            sys.exit(1)
        raise

    print(f"Serveur d'images démarré sur le port {port}. Ctrl+C pour arrêter.")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    # SIGTERM envoyé par `docker stop` — déclenche l'arrêt propre [ARCH-22]
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)

    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        await runner.cleanup()

    return 0
