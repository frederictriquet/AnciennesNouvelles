# Entry point CLI [docs/CLI.md, SPEC-3.6, ARCHITECTURE.md — _dispatch_inner]
from __future__ import annotations

import argparse
import os
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ancnouv",
        description="Anciennes Nouvelles — Bot Instagram/Facebook de retour dans le temps",
    )
    sub = parser.add_subparsers(dest="command", metavar="<commande>")

    # start
    sub.add_parser("start", help="Démarrer le scheduler + bot Telegram")

    # setup fonts
    setup = sub.add_parser("setup", help="Commandes de configuration")
    setup_sub = setup.add_subparsers(dest="setup_subcommand")
    setup_sub.add_parser("fonts", help="Télécharger les polices Google Fonts")

    # auth meta / test
    auth = sub.add_parser("auth", help="Authentification Meta")
    auth_sub = auth.add_subparsers(dest="auth_subcommand")
    auth_sub.add_parser("meta", help="Flux OAuth Meta interactif")
    auth_sub.add_parser("test", help="Vérifier les tokens Meta en DB")

    # fetch
    fetch_cmd = sub.add_parser("fetch", help="Collecter les événements Wikipedia")
    fetch_cmd.add_argument(
        "--prefetch",
        action="store_true",
        help=f"Collecter les N prochains jours (content.prefetch_days)",
    )

    # generate-test-image
    sub.add_parser("generate-test-image", help="Générer une image de test")

    # test telegram / instagram
    test_cmd = sub.add_parser("test", help="Tests de publication")
    test_sub = test_cmd.add_subparsers(dest="test_subcommand")
    test_sub.add_parser("telegram", help="Envoyer un message Telegram de test")
    test_sub.add_parser("instagram", help="Publier un post Instagram de test (réel)")

    # health
    sub.add_parser("health", help="Vérification de santé de l'application")

    # escalation reset
    esc = sub.add_parser("escalation", help="Gestion de l'escalade")
    esc_sub = esc.add_subparsers(dest="escalation_subcommand")
    esc_sub.add_parser("reset", help="Réinitialiser le niveau d'escalade")

    # images-server
    img_srv = sub.add_parser("images-server", help="Démarrer le serveur d'images")
    img_srv.add_argument("--port", type=int, default=8765, help="Port d'écoute (défaut: 8765)")

    # db
    db = sub.add_parser("db", help="Gestion de la base de données")
    db_sub = db.add_subparsers(dest="db_subcommand")
    db_sub.add_parser("init", help="Créer la DB et appliquer les migrations")
    db_sub.add_parser("migrate", help="Appliquer les migrations en attente")
    db_sub.add_parser("status", help="Afficher l'état des migrations")
    db_sub.add_parser("backup", help="Sauvegarder la DB")
    db_sub.add_parser("reset", help="DANGER : supprimer et recréer la DB")

    return parser


def _dispatch(args: argparse.Namespace) -> int:
    """Dispatch principal — catch BaseException pour SystemExit(2) argparse. [T-07]"""
    try:
        return _dispatch_inner(args)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 1
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


# Commandes sans config complète — validate_meta non déclenchée
_COMMANDS_WITHOUT_FULL_CONFIG = {"db", "setup", "images-server"}


def _dispatch_inner(args: argparse.Namespace) -> int:
    """Routing vers les sous-modules. [ARCHITECTURE.md — _dispatch_inner]"""
    import asyncio

    command = args.command

    if command is None:
        print("Commande manquante. Utiliser --help.", file=sys.stderr)
        return 2

    # ── Commandes sans config complète ──────────────────────────────────────
    if command == "db":
        from ancnouv.db.cli import run_db_command
        subcommand = getattr(args, "db_subcommand", None)
        if not subcommand:
            print("Sous-commande db manquante. Options : init, migrate, status, backup, reset", file=sys.stderr)
            return 2
        return run_db_command(subcommand)

    if command == "setup":
        subcommand = getattr(args, "setup_subcommand", None)
        if subcommand == "fonts":
            from ancnouv.cli.setup import download_fonts
            return download_fonts()
        print("Sous-commande setup inconnue. Options : fonts", file=sys.stderr)
        return 2

    if command == "images-server":
        from ancnouv.publisher.image_hosting import run_image_server
        token = os.environ.get("IMAGE_SERVER_TOKEN", "")
        return asyncio.run(run_image_server(port=args.port, token=token))

    # ── Commandes avec config complète ──────────────────────────────────────
    from ancnouv.config import Config
    try:
        config = Config()
    except Exception as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 1

    if command == "start":
        import logging
        from ancnouv.db.cli import cmd_db_migrate
        from ancnouv.scheduler import run
        rc = cmd_db_migrate()
        if rc != 0:
            logging.getLogger(__name__).warning(
                "db migrate a échoué (code %d) — démarrage quand même. "
                "Si l'erreur est 'table already exists', la DB n'est pas suivie par Alembic : "
                "lancer `alembic stamp <revision>` puis `db migrate`.",
                rc,
            )
        return run(config)

    if command == "auth":
        subcommand = getattr(args, "auth_subcommand", None)

        async def _auth_main() -> int:
            from ancnouv.db.session import get_session, init_db
            db_path = os.environ.get("ANCNOUV_DB_PATH", "") or f"{config.data_dir}/{config.database.filename}"
            init_db(db_path)
            async with get_session() as session:
                if subcommand == "meta":
                    from ancnouv.cli.auth import cmd_auth_meta
                    return await cmd_auth_meta(config, session)
                elif subcommand == "test":
                    from ancnouv.cli.auth import cmd_auth_test
                    return await cmd_auth_test(config, session)
                else:
                    print("Sous-commande auth inconnue. Options : meta, test", file=sys.stderr)
                    return 2

        return asyncio.run(_auth_main())

    if command == "fetch":
        from ancnouv.cli.fetch import run_fetch
        return asyncio.run(run_fetch(config, prefetch=args.prefetch))

    if command == "generate-test-image":
        from ancnouv.cli.generate import generate_test_image
        return generate_test_image(config)

    if command == "test":
        subcommand = getattr(args, "test_subcommand", None)
        if not subcommand:
            print("Sous-commande test manquante. Options : telegram, instagram", file=sys.stderr)
            return 2
        from ancnouv.cli.test_commands import run_test
        return asyncio.run(run_test(config, target=subcommand))

    if command == "health":
        from ancnouv.cli.health import run_health
        return asyncio.run(run_health(config))

    if command == "escalation":
        subcommand = getattr(args, "escalation_subcommand", None)
        if subcommand == "reset":
            from ancnouv.cli.escalation import reset_escalation
            return asyncio.run(reset_escalation(config))
        print("Sous-commande escalation inconnue. Options : reset", file=sys.stderr)
        return 2

    print(f"Commande inconnue : {command}", file=sys.stderr)
    return 2


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(_dispatch(args))


if __name__ == "__main__":
    main()
