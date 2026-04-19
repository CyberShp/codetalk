"""Semgrep adapter.

IRON LAW: analyze() may ONLY do:
  (a) HTTP calls to the Semgrep wrapper service
  (b) Response format conversion

CAPABILITY UTILIZATION RULE: Must use ALL of:
  - Multiple rule sets (p/default, p/security-audit, p/owasp-top-ten)
  - Custom rules for boundary/exception patterns
  - --dataflow-traces for taint path tracking
  - --severity filtering
  - --baseline-commit for incremental scans
  - JSON output with full metadata
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import (
    AnalysisRequest,
    BaseToolAdapter,
    ToolCapability,
    ToolHealth,
    UnifiedResult,
)

logger = logging.getLogger(__name__)

# ── Built-in rule sets to run — exhaustive, not "just p/default" ──
DEFAULT_RULE_SETS = [
    "p/default",           # 高置信度基线
    "p/security-audit",    # 宽泛安全审计
    "p/owasp-top-ten",     # OWASP Top 10 覆盖
]

# Custom rules directory (mounted into container at /rules)
CUSTOM_RULES_DIR = "/rules"


class SemgrepAdapter(BaseToolAdapter):
    def __init__(self, base_url: str = "http://semgrep:9090"):
        self.base_url = base_url

    def name(self) -> str:
        return "semgrep"

    def capabilities(self) -> list[ToolCapability]:
        return [
            ToolCapability.SECURITY_SCAN,
            ToolCapability.TAINT_ANALYSIS,
            ToolCapability.CODE_SEARCH,
        ]

    async def health_check(self) -> ToolHealth:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=10
            ) as client:
                resp = await client.get("/health")
                resp.raise_for_status()
                data = resp.json()
                return ToolHealth(
                    is_healthy=True,
                    container_status="running",
                    version=data.get("version"),
                )
        except Exception as exc:
            return ToolHealth(
                is_healthy=False,
                container_status="error",
                last_check=str(exc),
            )

    async def prepare(self, request: AnalysisRequest) -> None:
        """No preparation needed — Semgrep scans on the fly."""

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        """Run full Semgrep scan with all rule sets + custom rules.

        HTTP calls + response format conversion ONLY.
        """
        all_findings: list[dict] = []
        scan_meta: dict[str, Any] = {}

        # 1. Run built-in rule sets
        for ruleset in DEFAULT_RULE_SETS:
            try:
                result = await self._scan(
                    path=request.repo_local_path,
                    configs=[ruleset],
                    extra_args=["--dataflow-traces"],
                )
                findings = result.get("results", [])
                all_findings.extend(findings)
                scan_meta[ruleset] = len(findings)
                logger.info(
                    "semgrep: %s found %d findings", ruleset, len(findings)
                )
            except Exception as exc:
                logger.warning("semgrep: %s failed: %s", ruleset, exc)
                scan_meta[ruleset] = {"error": str(exc)}

        # 2. Run custom boundary/exception rules
        try:
            result = await self._scan(
                path=request.repo_local_path,
                configs=[CUSTOM_RULES_DIR],
                extra_args=["--dataflow-traces"],
            )
            custom_findings = result.get("results", [])
            all_findings.extend(custom_findings)
            scan_meta["custom_rules"] = len(custom_findings)
        except Exception as exc:
            logger.warning("semgrep: custom rules failed: %s", exc)
            scan_meta["custom_rules"] = {"error": str(exc)}

        # 3. Clean up OSS "requires login" placeholders
        _strip_login_placeholders(all_findings)

        # 4. Categorize findings (pure format conversion)
        categorized = _categorize_findings(all_findings)

        return UnifiedResult(
            tool_name="semgrep",
            capability=ToolCapability.SECURITY_SCAN,
            data={
                "findings": all_findings,
                "categorized": categorized,
                "summary": {
                    "total": len(all_findings),
                    "by_severity": _count_by_severity(all_findings),
                    "by_category": {k: len(v) for k, v in categorized.items()},
                },
            },
            raw_output=(
                f"{len(all_findings)} findings from "
                f"{len(DEFAULT_RULE_SETS) + 1} rule sets"
            ),
            metadata=scan_meta,
        )

    # ── High-level scan methods (exposed to API) ──

    async def scan_incremental(
        self, path: str, baseline_commit: str
    ) -> dict:
        """Incremental scan — only new findings since baseline."""
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=600
        ) as client:
            resp = await client.post(
                "/scan/baseline",
                json={
                    "path": path,
                    "baseline_commit": baseline_commit,
                    "configs": DEFAULT_RULE_SETS + [CUSTOM_RULES_DIR],
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def scan_with_severity(
        self, path: str, severity: str
    ) -> dict:
        """Scan filtered by severity (INFO, WARNING, ERROR)."""
        return await self._scan(
            path=path,
            configs=DEFAULT_RULE_SETS,
            severity=severity,
            extra_args=["--dataflow-traces"],
        )

    async def scan_with_custom_rules(
        self, path: str, rules_yaml: str
    ) -> dict:
        """Scan with user-provided YAML rules content."""
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=600
        ) as client:
            resp = await client.post(
                "/scan/inline-rules",
                json={"path": path, "rules_yaml": rules_yaml},
            )
            resp.raise_for_status()
            return resp.json()

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        yield "semgrep: scanning with p/default..."
        yield "semgrep: scanning with p/security-audit..."
        yield "semgrep: scanning with p/owasp-top-ten..."
        yield "semgrep: running custom boundary rules..."
        yield "semgrep: completed"

    # ── internal ──

    async def _scan(
        self,
        path: str,
        configs: list[str],
        extra_args: list[str] | None = None,
        severity: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "path": path,
            "configs": configs,
            "extra_args": extra_args or [],
        }
        if severity:
            payload["severity"] = severity

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(600, connect=10),
        ) as client:
            resp = await client.post("/scan", json=payload)
            resp.raise_for_status()
            return resp.json()


# ---------------------------------------------------------------------------
# Response format conversion — no analysis logic, pure reshaping
# ---------------------------------------------------------------------------


def _strip_login_placeholders(findings: list[dict]) -> None:
    """Remove 'requires login' placeholders from Semgrep OSS output.

    Semgrep Registry rules return 'requires login' for fields like
    `lines` and `fingerprint` when running without authentication.
    Replace with None so the frontend skips rendering them.
    """
    for f in findings:
        extra = f.get("extra", {})
        for key in ("lines", "fingerprint"):
            if extra.get(key) == "requires login":
                extra[key] = None


def _categorize_findings(findings: list[dict]) -> dict[str, list[dict]]:
    """Categorize findings into test-relevant groups.

    Pure format conversion based on metadata fields already in the Semgrep output.
    """
    categories: dict[str, list[dict]] = {
        "injection": [],
        "auth_bypass": [],
        "error_handling": [],
        "boundary_condition": [],
        "null_safety": [],
        "race_condition": [],
        "crypto_weakness": [],
        "config_issue": [],
        "other": [],
    }

    for f in findings:
        check_id = f.get("check_id", "").lower()
        metadata = f.get("extra", {}).get("metadata", {})
        cwe_list = metadata.get("cwe", [])
        cwe_str = " ".join(str(c).lower() for c in cwe_list)
        category_meta = metadata.get("category", "").lower()

        if "injection" in cwe_str or "injection" in check_id:
            categories["injection"].append(f)
        elif "auth" in cwe_str or "auth" in check_id:
            categories["auth_bypass"].append(f)
        elif (
            "error" in cwe_str or "exception" in cwe_str
            or "error" in check_id or "catch" in check_id
            or category_meta == "error-handling"
        ):
            categories["error_handling"].append(f)
        elif (
            "boundary" in check_id or "overflow" in check_id
            or "division" in check_id
            or category_meta == "boundary-condition"
        ):
            categories["boundary_condition"].append(f)
        elif "null" in check_id or "none" in check_id:
            categories["null_safety"].append(f)
        elif "race" in check_id or "toctou" in check_id:
            categories["race_condition"].append(f)
        elif "crypto" in check_id or "hash" in check_id:
            categories["crypto_weakness"].append(f)
        elif "config" in check_id:
            categories["config_issue"].append(f)
        else:
            categories["other"].append(f)

    return {k: v for k, v in categories.items() if v}


def _count_by_severity(findings: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {"ERROR": 0, "WARNING": 0, "INFO": 0}
    for f in findings:
        sev = f.get("extra", {}).get("severity", "INFO")
        counts[sev] = counts.get(sev, 0) + 1
    return counts
