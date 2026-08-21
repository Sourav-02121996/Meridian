from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    default_query: str = "Software Engineer"
    default_days: int = 2
    score_threshold: float = 82
    model_name: str = "sentence-transformers/all-mpnet-base-v2"
    db_url: str = "sqlite:///./hirelight.db"
    frontend_origin: str = "http://localhost:5173"
    crawler_headless: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
