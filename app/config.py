from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    PORT: int = 8000
    ENV: Literal["development", "staging", "production"] = "development"

    # Meta WhatsApp Cloud API
    WA_VERIFY_TOKEN: str
    WA_ACCESS_TOKEN: str
    WA_PHONE_NUMBER_ID: str
    WA_APP_SECRET: str

    # LLM / STT
    LLM_API_KEY: str
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-chat"
    OPENAI_API_KEY: str

    DATABASE_URL: PostgresDsn = Field(
        description="Must use the postgresql+asyncpg driver."
    )

    SQL_ECHO: bool = False

    @property
    def is_dev(self) -> bool:
        return self.ENV == "development"


@lru_cache(maxsize=1)
def _load() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings: Settings = _load()