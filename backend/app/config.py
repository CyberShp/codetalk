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

    # Public local API port. Keep this aligned with the frontend dev and E2E
    # defaults so logs and generated URLs do not point at stale runtimes.
    backend_port: int = Field(default=3004, validation_alias="CODETALK_BACKEND_PORT")

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
    gitnexus_auto_embed_enabled: bool = False  # keep /api/embed from blocking sequential indexing by default
    gitnexus_index_queue_max: int = Field(default=8, ge=1, le=100)
    cgc_cli_python: str = ""          # optional python executable for `python -m codegraphcontext`
    cgc_cli_timeout: int = 1800       # seconds for CGC CLI indexing / graph queries
    external_agents_enabled: bool = True
    external_agent_timeout_sec: int = 90
    external_agent_startup_probe_timeout_sec: int = 30
    external_agent_max_parallel: int = 2
    max_global_agent_processes: int = Field(default=2, ge=1, le=64)
    max_processes_per_provider: int = Field(default=1, ge=1, le=32)
    agent_provider_process_limits: dict[str, int] = Field(default_factory=dict)
    external_agent_max_output_chars: int = 120000
    external_agent_enforce_readonly_cli: bool = True
    external_agent_sandbox_mode: str = "auto"  # auto | required | off
    external_agent_sandbox_allow_network: bool = True
    external_agent_sandbox_write_paths: list[str] = Field(default_factory=list)
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
    llm_max_concurrency: int = 2     # process-wide provider capacity; keeps outline branches concurrent
    health_check_timeout: int = 5    # seconds for tool health probes
    llm_max_output_tokens: int = 8192  # LLM_MAX_OUTPUT_TOKENS — cap per-call output; set lower for intranet models
    ai_conversation_streaming_enabled: bool = True  # AI_CONVERSATION_STREAMING_ENABLED — disable for providers with broken SSE
    ai_conversation_max_output_tokens: int = 1024  # AI_CONVERSATION_MAX_OUTPUT_TOKENS — cap interactive thread turns
    ai_conversation_stream_timeout_sec: int = 120  # AI_CONVERSATION_STREAM_TIMEOUT_SEC — fallback from streaming to complete()
    # Staged source analysis is an evidence-ranking assist, not a second source
    # discovery pass. ``source_analysis_model`` accepts an LLM config id/model
    # name; an empty value keeps the active model while retaining every limit.
    source_analysis_model: str = "auto"
    source_analysis_max_tokens: int = Field(default=1600, ge=256, le=4096)
    source_analysis_max_chinese_characters: int = Field(default=1200, ge=200, le=8000)
    source_analysis_max_evidence_anchors: int = Field(default=12, ge=1, le=48)
    source_analysis_max_files: int = Field(default=6, ge=1, le=24)
    source_analysis_excerpt_chars: int = Field(default=1200, ge=200, le=6000)
    source_analysis_context_timeout_seconds: int = Field(default=30, ge=1, le=300)
    source_analysis_timeout_seconds: int = Field(default=300, ge=1, le=480)
    source_analysis_repair_max_tokens: int = Field(default=500, ge=128, le=800)
    source_analysis_repair_timeout_seconds: int = Field(default=120, ge=1, le=180)
    source_analysis_total_timeout_seconds: int = Field(default=480, ge=1, le=600)
    source_analysis_cache_enabled: bool = True
    source_analysis_schema_version: str = "source-evidence-pack-v1"
    staged_workflow_timeout_seconds: int = Field(default=1200, ge=60, le=1200)
    staged_workflow_max_tokens: int = Field(default=12000, ge=1000, le=32000)
    staged_quality_repair_enabled: bool = True
    staged_quality_repair_max_attempts: int = Field(default=2, ge=0, le=2)
    # Every staged LLM phase owns a bounded execution policy.  The generic
    # ceiling is six minutes; business-flow uses a tighter default because its
    # deterministic outline is already available before model enhancement.
    regular_stage_provider_timeout_seconds: int = Field(default=300, ge=1, le=360)
    regular_stage_total_timeout_seconds: int = Field(default=360, ge=1, le=360)
    regular_stage_repair_timeout_seconds: int = Field(default=60, ge=1, le=60)
    regular_stage_repair_max_tokens: int = Field(default=500, ge=128, le=600)
    regular_stage_cache_enabled: bool = True
    regular_stage_cache_version: str = "regular-stage-cache-v1"
    regular_stage_structured_fast_model_enabled: bool = True
    business_flow_max_tokens: int = Field(default=8000, ge=512, le=8000)
    black_box_cases_max_tokens: int = Field(default=12000, ge=6000, le=16000)
    business_flow_provider_timeout_seconds: int = Field(default=180, ge=1, le=360)
    business_flow_total_timeout_seconds: int = Field(default=240, ge=1, le=360)
    business_flow_repair_timeout_seconds: int = Field(default=30, ge=1, le=60)
    business_flow_streaming: bool = True
    business_flow_checkpoint_characters: int = Field(default=256, ge=64, le=4000)
    regular_stage_heartbeat_seconds: int = Field(default=10, ge=1, le=60)
    regular_stage_cancel_grace_seconds: float = Field(default=0.25, ge=0.01, le=2.0)
    flow_evidence_timeout_seconds: int = Field(default=45, ge=1, le=45)
    flow_evidence_max_files: int = Field(default=12, ge=1, le=24)
    flow_evidence_schema_version: str = "flow-evidence-pack-v1"
    flow_outline_schema_version: str = "flow-outline-v1"
    behavior_claim_audit_enabled: bool = True
    behavior_claim_audit_runtime_id: str = "default-codex"
    behavior_claim_audit_model: str = "gpt-5.5"
    behavior_claim_audit_reasoning_effort: str = "medium"
    behavior_claim_audit_timeout_seconds: int = Field(default=360, ge=30, le=600)
    behavior_claim_audit_max_claims: int = Field(default=64, ge=1, le=128)
    behavior_claim_audit_context_chars: int = Field(default=6000, ge=1000, le=12000)
    behavior_claim_audit_batch_size: int = Field(default=16, ge=1, le=32)
    behavior_claim_audit_concurrency: int = Field(default=4, ge=1, le=8)
    gitnexus_poll_timeout: int = 600 # max seconds to wait for GitNexus indexing
    coverage_max_upload_mb: int = 100 # max single file size for coverage upload

    # Workbench V2 is the default experience. Operators can set this false for
    # one release cycle to restore the legacy entry and API behavior.
    workbench_v2_enabled: bool = True

    # CORS — comma-separated origins allowed to call the API
    cors_origins: str = "http://localhost:3003,http://127.0.0.1:3003"

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
            "http://localhost:3123",
            "http://127.0.0.1:3123",
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
