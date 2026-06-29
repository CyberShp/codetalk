import os
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_CL100K_BPE = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Data storage root — all runtime files live here
    data_dir: str = "data"

    # SQLite database path
    sqlite_db: str = "data/codetalk.db"

    # Repository path translation (Docker host ↔ tool container)
    # Leave both empty for native mode or full-Docker mode (no translation needed).
    # Set for mixed mode (host backend + dockerized tools):
    #   REPOS_BASE_PATH  — host-side root where repos are stored
    #   TOOL_REPOS_BASE_PATH — path those repos appear at inside tool containers
    repos_base_path: str = ""
    tool_repos_base_path: str = ""

    # Local repos directory (Docker mode only — for analysing user-specified local folders).
    # Set LOCAL_REPOS_HOST_PATH to the host directory that contains your local projects.
    # LOCAL_REPOS_CONTAINER_PATH is auto-set by docker-compose to /local_repos; override only
    # if using a non-standard mount point.
    local_repos_host_path: str = ""
    local_repos_container_path: str = "/local_repos"

    # Git operation timeout in seconds (clone + pull)
    git_sync_timeout_seconds: int = 300

    # Tool process endpoints
    gitnexus_base_url: str = "http://localhost:7100"
    cgc_base_url: str = "http://localhost:7072"
    cgc_index_timeout: int = 600     # max seconds to wait for CGC Gateway indexing before CLI fallback
    joern_base_url: str = "http://localhost:8090"
    codecompass_base_url: str = "http://localhost:16251"

    # Tool process management
    gitnexus_port: int = 7100
    gitnexus_bin: str = "gitnexus"   # path to gitnexus binary
    gitnexus_source_reader: str = "cli_first"  # cli_first | http_only
    gitnexus_cli_timeout: int = 20    # seconds for short GitNexus CLI source reads
    cgc_cli_python: str = ""          # optional python executable for `python -m codegraphcontext`
    cgc_cli_timeout: int = 1800       # seconds for CGC CLI indexing / graph queries
    external_agents_enabled: bool = True
    external_agent_timeout_sec: int = 90
    external_agent_startup_probe_timeout_sec: int = 30
    external_agent_max_parallel: int = 2
    external_agent_max_output_chars: int = 120000
    external_agent_enforce_readonly_cli: bool = True
    external_agent_windows_shell_fallback_enabled: bool = True
    external_agent_windows_shell_load_profile: bool = True
    external_agent_command_allowlist: list[str] = Field(default_factory=lambda: [
        "rg", "git grep", "git ls-files", "Get-ChildItem", "Get-Content",
        "dir", "type", "python -c",
    ])
    agent_discovery_session_enabled: bool = True
    agent_discovery_max_rounds: int = 2
    agent_discovery_context_packet_max_chars: int = 180000
    agent_discovery_max_source_slices: int = 24
    agent_discovery_source_slice_lines: int = 120
    agent_discovery_store_prompts: bool = True
    agent_discovery_store_raw_outputs: bool = True
    agent_discovery_store_source_slices: bool = True
    agent_discovery_workspace_reuse_enabled: bool = False
    claude_code_command: str = "ccr code"
    claude_code_config_path: str = ""
    claude_code_fallback_commands: list[str] | str = Field(default_factory=list)
    claude_code_mcp_profiles: list[str] | str = Field(default_factory=list)
    claude_code_readonly_args: list[str] = Field(
        default_factory=lambda: [
            "--allowedTools",
            (
                "Read,Glob,Grep,"
                "Bash(rg:*),"
                "Bash(git grep:*),"
                "Bash(git ls-files:*),"
                "Bash(Get-ChildItem:*),"
                "Bash(Get-Content:*),"
                "Bash(dir:*),"
                "Bash(type:*),"
                "Bash(python -c:*)"
            ),
            "--disallowedTools",
            "Edit,Write,NotebookEdit",
        ]
    )
    opencode_command: str = "opencode"
    opencode_fallback_commands: list[str] | str = Field(default_factory=list)
    opencode_mcp_profiles: list[str] | str = Field(default_factory=list)
    opencode_readonly_args: list[str] = Field(default_factory=list)
    external_agent_supports_artifact_export: bool = True
    external_agent_supports_json_output: bool = True
    # JSON list of provider specs, e.g.
    # [{"id":"corp-agent","command":"corp-agent discover","prompt_transport":"stdin"}]
    external_agent_custom_providers: list[dict] | str = Field(default_factory=list)
    context_discovery_enabled: bool = True
    fast_context_enabled: bool = True
    fast_context_backend_bridge_enabled: bool = False
    tiktoken_cache_dir: str = ""     # override path for tiktoken BPE cache (TIKTOKEN_CACHE_DIR)
    tool_health_interval: int = 30   # seconds between health checks

    # Analysis tuning
    analysis_concurrency: int = 10   # max parallel module analyses
    llm_max_concurrency: int = 1     # admin env var LLM_MAX_CONCURRENCY; controls report-gen parallelism
    health_check_timeout: int = 5    # seconds for tool health probes
    llm_max_output_tokens: int = 8192  # LLM_MAX_OUTPUT_TOKENS — cap per-call output; set lower for intranet models
    ai_conversation_streaming_enabled: bool = True  # AI_CONVERSATION_STREAMING_ENABLED — disable for providers with broken SSE
    ai_conversation_max_output_tokens: int = 1024  # AI_CONVERSATION_MAX_OUTPUT_TOKENS — cap interactive thread turns
    ai_conversation_stream_timeout_sec: int = 120  # AI_CONVERSATION_STREAM_TIMEOUT_SEC — fallback from streaming to complete()
    gitnexus_poll_timeout: int = 600 # max seconds to wait for GitNexus indexing
    coverage_max_upload_mb: int = 100 # max single file size for coverage upload

    # CORS — comma-separated origins allowed to call the API
    cors_origins: str = "http://localhost:3003,http://127.0.0.1:3003,http://localhost:3005,http://127.0.0.1:3005"

    @model_validator(mode="after")
    def _resolve_repos_paths(self) -> "Settings":
        if not self.repos_base_path:
            from app.utils.repo_paths import default_repos_base_path
            self.repos_base_path = default_repos_base_path(Path(__file__).parent.parent.parent)
        if not self.tool_repos_base_path:
            self.tool_repos_base_path = self.repos_base_path
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        dev_origins = [
            "http://localhost:3003",
            "http://127.0.0.1:3003",
            "http://localhost:3005",
            "http://127.0.0.1:3005",
            "http://localhost:3205",
            "http://127.0.0.1:3205",
            "http://localhost:3218",
            "http://127.0.0.1:3218",
            "http://localhost:3219",
            "http://127.0.0.1:3219",
        ]
        for origin in dev_origins:
            if origin not in origins:
                origins.append(origin)
        return origins

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def outputs_path(self) -> Path:
        return self.data_path / "outputs"

    @property
    def tiktoken_cache_path(self) -> Path:
        candidates = []
        if self.tiktoken_cache_dir:
            candidates.append(Path(self.tiktoken_cache_dir))
        candidates.append(self.data_path / "tiktoken_cache")
        for p in candidates:
            p = p.resolve()
            if (p / _CL100K_BPE).exists():
                return p
        return (self.data_path / "tiktoken_cache").resolve()

settings = Settings()
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(settings.tiktoken_cache_path))
