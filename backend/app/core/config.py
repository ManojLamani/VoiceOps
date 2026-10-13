"""VoiceOps core configuration."""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT_DIR = os.path.dirname(BACKEND_DIR)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(os.path.join(PROJECT_DIR, ".env"), os.path.join(BACKEND_DIR, ".env")),
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "VoiceOps"
    app_env: str = "development"
    app_debug: bool = True
    log_level: str = "INFO"
    auto_seed: bool = True  # seed demo customers/orders/KB on first start if the DB is empty
    models_offline: bool = False  # use only locally cached embedding/Whisper models (no Hugging Face calls)

    # Database (PostgreSQL in production; SQLite supported for tests / quick dev)
    database_url: str = "postgresql+asyncpg://voiceops:voiceops@localhost:5432/voiceops"

    # Auth
    jwt_secret: str = "change-me-to-a-random-secret-key-at-least-32-chars"  # see DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 1440
    registration_enabled: bool = True  # self-registered accounts are read-only "viewer"s with masked PII

    # LLM: auto | openai | offline. "openai" means any OpenAI-compatible API
    # (OpenAI, Groq, OpenRouter, Ollama at http://localhost:11434/v1, ...).
    llm_provider: str = "auto"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 200
    llm_timeout_seconds: float = 60.0

    # STT: auto | openai | local | browser
    stt_provider: str = "auto"
    stt_base_url: str = ""
    stt_api_key: str = ""
    stt_model: str = "whisper-1"
    stt_local_model: str = "openai/whisper-base.en"

    # TTS: auto | openai | edge | browser
    tts_provider: str = "auto"
    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"
    tts_edge_voice: str = "en-US-AriaNeural"

    # RAG
    knowledge_base_dir: str = os.path.join(PROJECT_DIR, "data", "knowledge_base")
    embedding_model: str = "all-MiniLM-L6-v2"
    chunk_size: int = 600
    chunk_overlap: int = 80
    rag_top_k: int = 3
    rag_min_score: float = 0.40

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


DEFAULT_JWT_SECRET = "change-me-to-a-random-secret-key-at-least-32-chars"


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    if settings.jwt_secret.startswith("change-me") or len(settings.jwt_secret) < 32:
        import logging
        import secrets
        if settings.app_env.lower() != "development":
            # Never sign tokens with a publicly known key outside development.
            settings.jwt_secret = secrets.token_urlsafe(48)
            logging.getLogger("voiceops").warning(
                "JWT_SECRET is unset or weak; using a random per-process secret (sessions reset on restart).")
        else:
            logging.getLogger("voiceops").warning("JWT_SECRET is the development default; set it in .env.")
    return settings
