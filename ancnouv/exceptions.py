# Hiérarchie des exceptions custom [ARCH-21]


class AncNouvError(Exception):
    """Exception de base de l'application."""


class FetcherError(AncNouvError):
    """Erreur lors de la collecte de données (Wikipedia, RSS)."""


class GeneratorError(AncNouvError):
    """Erreur lors de la génération d'image ou de légende."""


class PublisherError(AncNouvError):
    """Erreur lors de la publication sur Instagram ou Facebook."""


class TokenExpiredError(PublisherError):
    """Token Meta expiré (remaining <= 0). Publications suspendues jusqu'à `auth meta`."""


class ImageHostingError(AncNouvError):
    """Erreur lors de l'upload ou du service d'images.

    Marquée NON_RETRIABLE dans utils/retry.py : upload_to_remote gère
    ses propres retries en interne — pas de double-wrapping.
    """


class DatabaseError(AncNouvError):
    """Erreur d'accès ou d'intégrité de la base de données.

    Catche les OperationalError SQLAlchemy (DB inaccessible) non gérées
    par le code métier. Déclenche un arrêt immédiat de l'application.
    """
