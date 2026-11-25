from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Kiroku"
    app_env: str = "development"
    default_locale: str = "ru"
    supported_locales: str = "ru,en"
    session_secret: str = "kiroku-dev-session-secret-change-in-prod"

    database_url: str = "sqlite:///./kiroku.db"

    meilisearch_url: str = ""
    meilisearch_api_key: str = ""

    anilist_api_url: str = "https://graphql.anilist.co"
    vndb_api_url: str = "https://api.vndb.org/kana"
    twitch_client_id: str = ""
    twitch_client_secret: str = ""

    deepl_api_key: str = ""
    openai_api_key: str = ""

    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "kiroku-media"
    r2_public_url: str = ""

    # OAuth / Social Login Providers
    google_client_id: str = ""
    google_client_secret: str = ""
    apple_client_id: str = ""
    apple_team_id: str = ""
    discord_client_id: str = ""
    discord_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    vk_client_id: str = ""
    vk_client_secret: str = ""
    telegram_bot_username: str = ""
    telegram_bot_token: str = ""

    @property
    def locales(self) -> list[str]:
        return [loc.strip() for loc in self.supported_locales.split(",") if loc.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
