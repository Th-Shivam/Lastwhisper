import os
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore", validate_default=True)
    database_url: str = "sqlite:///./data/lastwhisper.sqlite3"
    storage_dir: str = "./data/audio"
    max_upload_size_mb: int = Field(default=100, ge=1, le=2048)
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173"
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    whisper_model: str = "base"
    whisper_revision: str | None = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_download_root: str | None = None
    whisper_local_files_only: bool = False
    max_processing_duration_seconds: int = Field(default=14400, ge=1, le=86400)
    max_transcription_jobs: int = Field(default=3, ge=1, le=20)
    ollama_model: str | None = None
    ollama_url: str = "http://127.0.0.1:11434"
    allow_remote_ollama: bool = False
    ollama_max_response_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_device: str = "cpu"
    embedding_revision: str | None = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
    embedding_cache_dir: str | None = None
    embedding_local_files_only: bool = False
    hf_hub_disable_xet: bool = True
    search_min_similarity: float = Field(default=0.30, ge=-1.0, le=1.0)
    search_chunk_characters: int = Field(default=700, ge=128, le=10000)
    search_chunk_overlap_segments: int = Field(default=1, ge=0, le=10)

    @field_validator("database_url")
    @classmethod
    def anchor_sqlite_database(cls, value: str) -> str:
        prefix = "sqlite:///"
        if not value.startswith(prefix):
            return value
        raw_path = Path(value[len(prefix):])
        if str(raw_path) == ":memory:":
            return value
        if not raw_path.is_absolute():
            raw_path = (BACKEND_DIR / raw_path).resolve()
        return f"{prefix}{raw_path.as_posix()}"

    @property
    def storage_path(self) -> Path:
        path = Path(self.storage_dir)
        return path.resolve() if path.is_absolute() else (BACKEND_DIR / path).resolve()

    @property
    def database_path(self) -> Path | None:
        prefix = "sqlite:///"
        return Path(self.database_url[len(prefix):]) if self.database_url.startswith(prefix) else None

    @property
    def whisper_cache_path(self) -> Path | None:
        if not self.whisper_download_root:
            return None
        path = Path(self.whisper_download_root)
        return path.resolve() if path.is_absolute() else (BACKEND_DIR / path).resolve()

    @property
    def embedding_cache_path(self) -> Path | None:
        if not self.embedding_cache_dir:
            return None
        path = Path(self.embedding_cache_dir)
        return path.resolve() if path.is_absolute() else (BACKEND_DIR / path).resolve()

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def trusted_hosts(self) -> list[str]:
        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]


settings = Settings()
if settings.hf_hub_disable_xet:
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
