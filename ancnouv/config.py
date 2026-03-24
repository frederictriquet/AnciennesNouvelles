# Configuration Pydantic Settings [SPEC-3.6, docs/CONFIGURATION.md]
from __future__ import annotations

import re
from typing import Literal

from apscheduler.triggers.cron import CronTrigger  # [CONF-16] dépendance intentionnelle
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, YamlConfigSettingsSource


class DatabaseConfig(BaseSettings):
    filename: str = "ancnouv.db"
    auto_backup: bool = True
    backup_keep: int = Field(default=7, ge=1)


class SchedulerConfig(BaseSettings):
    timezone: str = "Europe/Paris"
    generation_cron: str = "0 */4 * * *"
    max_pending_posts: int = Field(default=1, ge=1)
    approval_timeout_hours: int = Field(default=48, ge=1, le=8760)
    auto_publish: bool = False
    misfire_grace_time: int = Field(default=300, ge=1)
    # v2 : max_queue_size: int = 10


class RssFeedConfig(BaseSettings):
    url: str
    name: str

    @field_validator("url")
    @classmethod
    def validate_url_format(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"URL de flux RSS invalide : {v!r}")
        return v


class RssConfig(BaseSettings):
    enabled: bool = False
    min_delay_days: int = 90
    max_age_days: int = 180
    feeds: list[RssFeedConfig] = []

    @model_validator(mode="after")
    def validate_rss_delays(self) -> "RssConfig":
        # [CONF-08] min_delay_days doit être strictement inférieur à max_age_days
        if self.min_delay_days >= self.max_age_days:
            raise ValueError(
                f"rss.min_delay_days ({self.min_delay_days}) doit être < "
                f"rss.max_age_days ({self.max_age_days})"
            )
        return self


class ContentConfig(BaseSettings):
    prefetch_days: int = Field(default=30, ge=1)
    wikipedia_event_types: list[str] = ["events"]
    wikipedia_lang_primary: str = "fr"
    wikipedia_lang_fallback: str = "en"
    wikipedia_min_events: int = 3
    deduplication_policy: Literal["never", "window", "always"] = "never"
    deduplication_window_days: int = Field(default=365, ge=1)
    image_retention_days: int = Field(default=7, ge=1)
    low_stock_threshold: int = Field(default=3, ge=1)
    mix_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    rss: RssConfig = RssConfig()


class ImageConfig(BaseSettings):
    width: int = 1080
    height: int = 1350
    jpeg_quality: int = Field(default=95, ge=1, le=100)
    paper_texture: bool = True
    paper_texture_intensity: int = 8
    masthead_text: str = "ANCIENNES NOUVELLES"
    force_template: str | None = None  # [SPEC-7bis, RF-7bis.4]


class CaptionConfig(BaseSettings):
    hashtags: list[str] = [
        "#histoire",
        "#onthisday",
        "#anciennesnews",
        "#memoireducollectif",
        "#ephemeride",
    ]
    hashtags_separator: str = "\n\n"
    include_wikipedia_url: bool = False
    source_template_fr: str = "Source : Wikipédia"
    source_template_en: str = "Source : Wikipedia (EN)"


class ImageHostingConfig(BaseSettings):
    backend: Literal["local", "remote"] = "local"
    # [CONF-14] : défaut "" accepté par Pydantic, rejeté par validate_image_hosting
    public_base_url: str = ""
    local_port: int = 8765
    remote_upload_url: str = ""


class InstagramConfig(BaseSettings):
    enabled: bool = False
    user_id: str = ""
    api_version: str = "v21.0"
    max_daily_posts: int = Field(default=25, ge=1, le=50)


class FacebookConfig(BaseSettings):
    enabled: bool = False
    page_id: str = ""
    # [CONF-11] api_version partagé depuis InstagramConfig


class TelegramConfig(BaseSettings):
    # [CONF-C4] : [] intentionnellement invalide — force la configuration explicite
    authorized_user_ids: list[int] = []
    notification_debounce: int = Field(default=2, ge=0)


class StoriesConfig(BaseSettings):
    """Configuration des Stories Instagram + Facebook. [SPEC-7, RF-7.3.5]"""
    enabled: bool = False
    max_text_chars: int = Field(default=400, ge=50, le=1000)


# Placeholder évitant une liste de rejet dans validate_image_hosting
_URL_REJECT_PATTERNS = ("VOTRE", "VOTRE-IP", "example", "localhost")


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        yaml_file="config.yml",
        env_file=".env",
        env_nested_delimiter="__",
        env_prefix="",
        extra="ignore",
    )

    # Secrets depuis .env (champs racines — pas dans config.yml)
    telegram_bot_token: str = ""
    meta_app_id: str = ""
    meta_app_secret: str = ""
    image_server_token: str = ""

    # Paramètres depuis config.yml
    data_dir: str = "data"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database: DatabaseConfig = DatabaseConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    content: ContentConfig = ContentConfig()
    image: ImageConfig = ImageConfig()
    caption: CaptionConfig = CaptionConfig()
    image_hosting: ImageHostingConfig = ImageHostingConfig()
    instagram: InstagramConfig = InstagramConfig()
    facebook: FacebookConfig = FacebookConfig()
    telegram: TelegramConfig = TelegramConfig()
    stories: StoriesConfig = StoriesConfig()

    @classmethod
    def settings_customise_sources(cls, settings_cls, **kwargs):
        return (
            kwargs["env_settings"],
            kwargs["dotenv_settings"],
            YamlConfigSettingsSource(settings_cls),
            kwargs["init_settings"],
        )

    @field_validator("telegram_bot_token")
    @classmethod
    def validate_telegram_token(cls, v: str) -> str:
        # Format : <id_numérique>:<chaîne_alphanum_≥35_chars>
        if v and not re.match(r"^\d+:[A-Za-z0-9_-]{35,}$", v):
            raise ValueError(
                "TELEGRAM_BOT_TOKEN invalide — format attendu : <id>:<token>"
            )
        return v

    @model_validator(mode="after")
    def validate_image_hosting(self) -> "Config":
        # [CONF-09] Vérification public_base_url uniquement si une plateforme est activée
        if self.instagram.enabled or self.facebook.enabled:
            url = self.image_hosting.public_base_url
            if not url:
                raise ValueError(
                    "image_hosting.public_base_url est vide — "
                    "obligatoire quand instagram.enabled ou facebook.enabled"
                )
            for pattern in _URL_REJECT_PATTERNS:
                if pattern in url:
                    raise ValueError(
                        f"image_hosting.public_base_url contient un placeholder ({pattern!r}) — "
                        "remplacer par l'IP ou le domaine réel du serveur d'images"
                    )
            if self.image_hosting.backend == "remote":
                if not self.image_hosting.remote_upload_url:
                    raise ValueError(
                        "image_hosting.remote_upload_url est vide — "
                        "obligatoire avec backend=remote"
                    )
                if not self.image_server_token:
                    raise ValueError(
                        "IMAGE_SERVER_TOKEN est vide — "
                        "obligatoire avec backend=remote"
                    )
        return self

    @model_validator(mode="after")
    def validate_cron(self) -> "Config":
        # [CONF-16] Valide l'expression cron via APScheduler
        try:
            CronTrigger.from_crontab(self.scheduler.generation_cron)
        except Exception as exc:
            raise ValueError(
                f"scheduler.generation_cron invalide : {self.scheduler.generation_cron!r} — {exc}"
            ) from exc
        return self

    @model_validator(mode="after")
    def validate_meta(self) -> "Config":
        # [CONF-15]
        if self.instagram.enabled and not self.instagram.user_id:
            raise ValueError(
                "instagram.user_id est vide. Lancer : python -m ancnouv auth meta"
            )
        if self.facebook.enabled and not self.facebook.page_id:
            raise ValueError(
                "facebook.page_id est vide. Lancer : python -m ancnouv auth meta"
            )
        return self

    @model_validator(mode="after")
    def validate_telegram(self) -> "Config":
        # [CONF-15]
        if not self.telegram.authorized_user_ids:
            raise ValueError(
                "telegram.authorized_user_ids est vide — "
                "au moins un ID utilisateur est requis"
            )
        return self
