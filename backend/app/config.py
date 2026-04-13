from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://codetalks:changeme@localhost:5432/codetalks"
    fernet_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""
    ollama_base_url: str = "http://host.docker.internal:11434"
    deepwiki_base_url: str = "http://deepwiki:8001"
    repos_base_path: str = "/data/repos"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
