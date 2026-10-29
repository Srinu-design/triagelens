from typing import Literal, Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    database_url: str
    redis_url: str
    app_env: Literal["prod", "dev", "test"] = "prod"
    model_dir: str = Field(
        default="/models", validation_alias=AliasChoices("MODEL_DIR", "FASTEMBED_CACHE_PATH")
    )
    embed_threads: int = Field(default=2, ge=1)
    dup_t_auto: float = Field(default=0.80, ge=0, le=1)
    dup_t_review: float = Field(default=0.70, ge=0, le=1)
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_json_mode: bool = False
    llm_daily_budget: int = Field(default=200, ge=0)
    rate_limit_enabled: bool = True
    session_ttl_hours: int = Field(default=24, gt=0)
    embedding_backend: Literal["fastembed", "hash"] = "fastembed"

    @model_validator(mode="after")
    def validate_embedding_backend(self) -> Self:
        if self.embedding_backend == "hash" and self.app_env != "test":
            raise ValueError("The hash embedding backend is allowed only in APP_ENV=test")
        return self

    @property
    def llm_enabled(self) -> bool:
        return all((self.llm_base_url, self.llm_api_key, self.llm_model))
