# Commandes auth meta / auth test [docs/CLI.md — section "auth meta/test", docs/INSTAGRAM_API.md]
from __future__ import annotations

import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread

from sqlalchemy.ext.asyncio import AsyncSession

from ancnouv.config import Config


def cmd_auth_meta(config: Config, session: AsyncSession):
    """Coroutine : flux OAuth Meta interactif. Retourne un int (code de sortie).

    Séquence : serveur HTTP localhost:8080 → URL OAuth → code → token court
    → token long (60j) → Page Access Token → stockage DB meta_tokens.
    [INSTAGRAM_API.md — cmd_auth_meta, IG-F7]
    """
    return _cmd_auth_meta_impl(config, session)


async def _cmd_auth_meta_impl(config: Config, session: AsyncSession) -> int:
    import httpx

    # Vérification port 8080 disponible [CLI.md]
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("localhost", 8082))
        except OSError as exc:
            print(
                f"Erreur : port 8080 déjà utilisé ({exc}). "
                "Vérifier avec : lsof -i :8080",
                file=sys.stderr,
            )
            return 1

    # Serveur HTTP temporaire pour capturer le callback OAuth
    received_code: list[str] = []
    done_event = Event()

    class _CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                received_code.append(params["code"][0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<h1>Authentification r\xc3\xa9ussie ! "
                    b"Vous pouvez fermer cette page.</h1>"
                )
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Code OAuth manquant")
            done_event.set()

        def log_message(self, *args):
            pass  # silence les logs HTTP

    server = HTTPServer(("localhost", 8082), _CallbackHandler)
    server.timeout = 120

    # Construction de l'URL OAuth
    scopes = ",".join([
        "instagram_basic",
        "instagram_content_publish",
        "instagram_creator_manage_content",
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_posts",
    ])
    auth_url = (
        "https://www.facebook.com/v21.0/dialog/oauth?"
        + urllib.parse.urlencode({
            "client_id": config.meta_app_id,
            "redirect_uri": "http://localhost:8080/callback",
            "scope": scopes,
            "response_type": "code",
        })
    )

    print("\nOuvrir cette URL dans votre navigateur :")
    print(f"\n  {auth_url}\n")
    print("En attente du callback OAuth (timeout : 120s)...")

    def _serve():
        if not done_event.is_set():
            server.handle_request()

    thread = Thread(target=_serve, daemon=True)
    thread.start()
    thread.join(timeout=125)

    if not received_code:
        print("Timeout OAuth — aucun code reçu.", file=sys.stderr)
        return 1

    code = received_code[0]
    print("Code OAuth reçu. Échange en cours...")

    async with httpx.AsyncClient() as client:
        # 1. Code → token court
        resp = await client.get(
            "https://graph.facebook.com/v21.0/oauth/access_token",
            params={
                "client_id": config.meta_app_id,
                "redirect_uri": "http://localhost:8080/callback",
                "client_secret": config.meta_app_secret,
                "code": code,
            },
        )
        if resp.status_code != 200:
            print(f"Erreur échange code : {resp.text}", file=sys.stderr)
            return 1
        short_token = resp.json()["access_token"]

        # 2. Token court → token long (60 jours)
        resp = await client.get(
            "https://graph.facebook.com/v21.0/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": config.meta_app_id,
                "client_secret": config.meta_app_secret,
                "fb_exchange_token": short_token,
            },
        )
        if resp.status_code != 200:
            print(f"Erreur token long : {resp.text}", file=sys.stderr)
            return 1
        long_data = resp.json()
        long_token = long_data["access_token"]
        expires_in = long_data.get("expires_in", 5184000)
        expires_at = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + expires_in, tz=timezone.utc
        )

        # 3. Récupération de l'IG User ID
        resp = await client.get(
            "https://graph.facebook.com/v21.0/me",
            params={"fields": "id,name", "access_token": long_token},
        )
        if resp.status_code != 200:
            print(f"Erreur récupération profil : {resp.text}", file=sys.stderr)
            return 1
        me = resp.json()
        ig_user_id = me["id"]
        ig_username = me.get("name", "")

        # 4. Pages administrées → Page Access Token
        resp = await client.get(
            "https://graph.facebook.com/v21.0/me/accounts",
            params={"access_token": long_token},
        )
        if resp.status_code != 200:
            print(f"Erreur récupération pages : {resp.text}", file=sys.stderr)
            return 1
        pages = resp.json().get("data", [])
        if not pages:
            print("Aucune Page Facebook administrée trouvée.", file=sys.stderr)
            return 1

        # Sélection interactive si plusieurs pages
        if len(pages) > 1:
            print("\nPages disponibles :")
            for i, p in enumerate(pages):
                print(f"  {i + 1}. {p['name']} (ID: {p['id']})")
            choice = input("Choisir la page (numéro) : ").strip()
            try:
                page = pages[int(choice) - 1]
            except (ValueError, IndexError):
                print("Choix invalide.", file=sys.stderr)
                return 1
        else:
            page = pages[0]

        page_token = page["access_token"]
        page_id = page["id"]
        page_name = page["name"]

    # 5. Stockage en DB (UPSERT sur token_kind)
    from sqlalchemy import text as sa_text
    await session.execute(
        sa_text(
            "INSERT INTO meta_tokens "
            "(token_kind, ig_user_id, ig_username, access_token, expires_at, "
            "last_refreshed_at, updated_at) "
            "VALUES ('user_long', :uid, :uname, :token, :exp, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "ON CONFLICT(token_kind) DO UPDATE SET "
            "ig_user_id=excluded.ig_user_id, ig_username=excluded.ig_username, "
            "access_token=excluded.access_token, expires_at=excluded.expires_at, "
            "last_refreshed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP"
        ),
        {"uid": ig_user_id, "uname": ig_username, "token": long_token, "exp": expires_at},
    )
    await session.execute(
        sa_text(
            "INSERT INTO meta_tokens "
            "(token_kind, fb_page_id, fb_page_name, access_token, updated_at) "
            "VALUES ('page', :pid, :pname, :token, CURRENT_TIMESTAMP) "
            "ON CONFLICT(token_kind) DO UPDATE SET "
            "fb_page_id=excluded.fb_page_id, fb_page_name=excluded.fb_page_name, "
            "access_token=excluded.access_token, updated_at=CURRENT_TIMESTAMP"
        ),
        {"pid": page_id, "pname": page_name, "token": page_token},
    )
    # Lever publications_suspended si précédemment suspendu [CLI.md]
    from ancnouv.db.utils import set_scheduler_state
    await set_scheduler_state(session, "publications_suspended", "false")
    await session.commit()

    print(f"\n✓ Tokens stockés en DB.")
    print(f"  Compte IG : {ig_username} (ID: {ig_user_id})")
    print(f"  Page FB   : {page_name} (ID: {page_id})")
    print(f"  Expiration : {expires_at.strftime('%d/%m/%Y %H:%M UTC')}")
    return 0


async def cmd_auth_test(config: Config, session: AsyncSession) -> int:
    """Vérifie les tokens Meta stockés en DB."""
    import httpx
    from sqlalchemy import text as sa_text

    result = await session.execute(
        sa_text("SELECT token_kind, access_token, expires_at, ig_user_id FROM meta_tokens")
    )
    tokens = {row[0]: row for row in result.fetchall()}

    if "user_long" not in tokens:
        print("Token utilisateur absent. Lancer : python -m ancnouv auth meta", file=sys.stderr)
        return 1

    user_token = tokens["user_long"][1]
    expires_at_str = tokens["user_long"][2]
    ig_user_id = tokens["user_long"][3]

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://graph.facebook.com/v21.0/me",
            params={"fields": "id,name", "access_token": user_token},
        )
        if resp.status_code != 200:
            print(f"Token invalide : {resp.text}", file=sys.stderr)
            return 1
        me = resp.json()

    print(f"✓ Token utilisateur valide")
    print(f"  Identité : {me.get('name')} (ID: {me.get('id')})")
    if expires_at_str:
        print(f"  Expiration : {expires_at_str}")
    return 0
