import itertools

import pytest
from pydantic import ValidationError

from app.config import Settings


def settings(**kwargs):
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://localhost/triagelens_test",
        redis_url="redis://localhost/0",
        **kwargs,
    )


@pytest.mark.parametrize("app_env", ["prod", "dev"])
def test_hash_requires_test_environment(app_env):
    with pytest.raises(ValidationError, match="APP_ENV=test"):
        settings(app_env=app_env, embedding_backend="hash")


def test_hash_allowed_in_tests():
    assert settings(app_env="test", embedding_backend="hash").embedding_backend == "hash"


@pytest.mark.parametrize("values", list(itertools.product([None, "configured"], repeat=3)))
def test_llm_requires_all_three_settings(values):
    config = settings(llm_base_url=values[0], llm_api_key=values[1], llm_model=values[2])
    assert config.llm_enabled is all(values)


def test_config_defaults(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    config = settings()
    assert config.app_env == "prod"
    assert config.embedding_backend == "fastembed"
    assert config.model_dir == "/models"
    assert config.embed_threads == 2
    assert config.dup_t_auto == 0.80
    assert config.dup_t_review == 0.70
    assert config.session_ttl_hours == 24


def test_cache_path_alias(monkeypatch):
    monkeypatch.delenv("MODEL_DIR", raising=False)
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", "/cached-models")
    assert settings().model_dir == "/cached-models"


def test_dotenv_allows_compose_and_test_variables(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TL_PG_PORT=5434\nTEST_DATABASE_URL=unused\n")
    assert Settings(
        _env_file=env,
        database_url="postgresql+asyncpg://localhost/triagelens_test",
        redis_url="redis://localhost/0",
    )
