from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./data/lastwhisper.sqlite3"
    storage_dir: str = "./data/audio"
    max_upload_size_mb: int = 100
    allowed_origins: str = "http://localhost:5173"
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_download_root: str | None = None
    max_processing_duration_seconds: int = 14400
    ollama_model: str | None = None
    ollama_url: str = "http://127.0.0.1:11434"

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_dir).resolve()

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]


settings = Settings()

