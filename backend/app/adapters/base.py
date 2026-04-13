from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum


class ToolCapability(Enum):
    CODE_SEARCH = "code_search"
    CALL_GRAPH = "call_graph"
    DEPENDENCY_GRAPH = "dependency_graph"
    TAINT_ANALYSIS = "taint_analysis"
    SECURITY_SCAN = "security_scan"
    DOCUMENTATION = "documentation"
    KNOWLEDGE_GRAPH = "knowledge_graph"
    ARCHITECTURE_DIAGRAM = "architecture_diagram"
    POINTER_ANALYSIS = "pointer_analysis"
    AST_ANALYSIS = "ast_analysis"


@dataclass
class ToolHealth:
    is_healthy: bool
    container_status: str
    version: str | None = None
    last_check: str = ""


@dataclass
class AnalysisRequest:
    repo_local_path: str
    target_files: list[str] | None = None
    task_type: str = "full_repo"
    options: dict = field(default_factory=dict)


@dataclass
class UnifiedResult:
    tool_name: str
    capability: ToolCapability
    data: dict
    raw_output: str = ""
    diagrams: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class BaseToolAdapter(ABC):
    """Abstract base class for all tool adapters.

    IRON LAW: analyze() may ONLY do:
      (a) HTTP/RPC calls to the external tool
      (b) Response format conversion
    No analysis logic (regex matching, AST traversal, graph building) allowed.
    """

    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def capabilities(self) -> list[ToolCapability]: ...

    @abstractmethod
    async def health_check(self) -> ToolHealth: ...

    @abstractmethod
    async def prepare(self, request: AnalysisRequest) -> None:
        """Pre-processing: index repo, import CPG, etc."""
        ...

    @abstractmethod
    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        """Run analysis. HTTP calls + response conversion ONLY."""
        ...

    @abstractmethod
    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        """Stream real-time logs."""
        ...

    async def cleanup(self, request: AnalysisRequest) -> None:
        """Optional cleanup after analysis."""
        pass
