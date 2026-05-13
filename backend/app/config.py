from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Data storage root — all runtime files live here
    data_dir: str = "data"

    # SQLite database path
    sqlite_db: str = "data/codetalk.db"

    # Tool process endpoints
    gitnexus_base_url: str = "http://localhost:7100"
    deepwiki_api_url: str = "http://localhost:8001"
    deepwiki_ui_url: str = "http://localhost:3000"

    # Tool process management
    gitnexus_port: int = 7100
    deepwiki_api_port: int = 8001
    deepwiki_ui_port: int = 3000
    deepwiki_path: str = ""          # path to deepwiki-open installation
    gitnexus_bin: str = "gitnexus"   # path to gitnexus binary
    tool_health_interval: int = 30   # seconds between health checks

    # CORS — comma-separated origins allowed to call the API
    cors_origins: str = "http://localhost:3005,http://127.0.0.1:3005"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def outputs_path(self) -> Path:
        return self.data_path / "outputs"

    @property
    def tiktoken_cache_path(self) -> Path:
        return self.data_path / "tiktoken_cache"


settings = Settings()
