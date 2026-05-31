from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    kakao_rest_key: str = ""
    kakao_js_key: str = ""
    odsay_key: str = ""
    dart_api_key: str = ""

    ollama_host: str = "http://localhost:11434"
    # Defaults are overridable via /settings page (UserSetting takes precedence at runtime).
    ollama_text_model: str = "qwen3.5:9b"
    ollama_vision_model: str = "qwen2.5vl:7b"

    redis_url: str = "redis://localhost:6379/0"
    database_url: str = f"sqlite+aiosqlite:///{ROOT_DIR / 'data' / 'whoareyou.db'}"

    debug: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
