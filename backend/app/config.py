from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.repo_paths import default_repos_base_path

# Locate env files relative to this source file so they are found regardless
# of the process working directory.
#
# Host layout:   codetalk/backend/app/config.py
#   _backend_dir = codetalk/backend/
#   _repo_root   = codetalk/              ← repo-root .env (Docker-network values)
#   _host_env    = codetalk/backend/.env.local  ← host-run overrides (gitignored)
#
# Docker layout: /app/app/config.py
#   _backend_dir = /app/
#   _repo_root   = /                      ← almost certainly no .env there
#   _host_env    = /app/.env.local        ← almost certainly absent too
#   → env vars already set by docker-compose; .env loading is a no-op → safe
_backend_dir = Path(__file__).parent.parent
_repo_root = _backend_dir.parent
_host_env = _backend_dir / ".env.local"
_default_repos_base_path = default_repos_base_path(_repo_root)


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://codetalks:changeme@localhost:5432/codetalks"
    fernet_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""
    ollama_base_url: str = "http://host.docker.internal:11434"
    deepwiki_base_url: str = "http://deepwiki:8001"
    gitnexus_base_url: str = "http://gitnexus:7100"
    zoekt_base_url: str = "http://zoekt:6070"
    zoekt_container_name: str = "codetalk-zoekt-1"
    joern_base_url: str = "http://joern:8080"
    codecompass_base_url: str = "http://codecompass:6251"
    repos_base_path: str = _default_repos_base_path
    tool_repos_base_path: str = "/data/repos"

    model_config = SettingsConfigDict(
        # Load order: repo-root .env first (Docker-network defaults),
        # then backend/.env.local (host-run overrides).  Later file wins.
        # Missing files are silently ignored by pydantic-settings.
        # In Docker, env vars injected by compose take precedence over all files.
        env_file=[str(_repo_root / ".env"), str(_host_env)],
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
