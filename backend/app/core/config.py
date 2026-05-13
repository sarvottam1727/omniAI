from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "OmniAI Email Shooter"
    environment: str = "local"
    database_url: str = "postgresql+psycopg://omniai:omniai@localhost:5432/omniai_email"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    credential_encryption_key: str = "replace-with-fernet-key-before-real-use"
    local_mail_host: str = "localhost"
    local_mail_port: int = 1025
    default_from_email: str = "dev@omniai.local"
    public_base_url: str = "http://localhost:8000"
    tracking_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
