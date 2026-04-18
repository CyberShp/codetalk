"""Lightweight FastAPI wrapper around the Semgrep CLI.

This is NOT analysis logic — it simply exposes Semgrep's CLI as an HTTP API
so the backend adapter can call it over the network instead of docker exec.
"""

import json
import logging
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Semgrep Wrapper", version="1.0.0")


@app.get("/health")
async def health():
    """Verify semgrep binary is available."""
    result = subprocess.run(
        ["semgrep", "--version"], capture_output=True, text=True, timeout=10
    )
    return {"status": "healthy", "version": result.stdout.strip()}


@app.post("/scan")
async def scan(body: dict):
    """Run semgrep scan with specified configs and return JSON results.

    Body:
        path: str — repo path to scan
        configs: list[str] — rule configs (e.g. ["p/default", "/rules"])
        severity: str | None — filter by severity (INFO, WARNING, ERROR)
        extra_args: list[str] — additional CLI args
    """
    repo_path = body["path"]
    configs = body.get("configs", ["p/default"])
    severity = body.get("severity")
    extra_args = body.get("extra_args", [])

    cmd = ["semgrep", "scan", "--json"]
    for c in configs:
        cmd += ["--config", c]
    if severity:
        cmd += ["--severity", severity]
    cmd += extra_args
    cmd.append(repo_path)

    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=600
    )

    if result.stdout:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"error": "invalid_json", "raw_stdout": result.stdout[:2000]}
    return {"error": result.stderr[:2000] if result.stderr else "no_output", "results": []}


@app.post("/scan/baseline")
async def scan_baseline(body: dict):
    """Incremental scan: only report findings since baseline_commit."""
    repo_path = body["path"]
    baseline = body["baseline_commit"]
    configs = body.get("configs", ["p/default"])

    cmd = ["semgrep", "scan", "--json", "--dataflow-traces",
           "--baseline-commit", baseline]
    for c in configs:
        cmd += ["--config", c]
    cmd.append(repo_path)

    logger.info("Running baseline scan: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=600
    )

    if result.stdout:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"error": "invalid_json"}
    return {"error": result.stderr[:2000] if result.stderr else "no_output", "results": []}


@app.post("/scan/inline-rules")
async def scan_with_inline_rules(body: dict):
    """Scan with user-provided YAML rules content."""
    repo_path = body["path"]
    rules_yaml = body["rules_yaml"]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yml", delete=False, dir="/tmp"
    ) as f:
        f.write(rules_yaml)
        rules_path = f.name

    try:
        cmd = ["semgrep", "scan", "--json", "--config", rules_path, repo_path]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        if result.stdout:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {"error": "invalid_json"}
        return {"error": result.stderr[:2000] if result.stderr else "no_output", "results": []}
    finally:
        Path(rules_path).unlink(missing_ok=True)
