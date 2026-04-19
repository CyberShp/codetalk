import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.adapters.base import ToolCapability, ToolHealth, UnifiedResult
from app.api import repo_analysis as repo_analysis_api
from app.main import app


class _FakeResult:
    def __init__(self, scalar_value=None):
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value


class _FakeDB:
    def __init__(self, *, execute_results=None, get_map=None):
        self._execute_results = list(execute_results or [])
        self._get_map = dict(get_map or {})

    async def execute(self, _query):
        assert self._execute_results, "unexpected execute() call"
        return self._execute_results.pop(0)

    async def get(self, _model, key):
        return self._get_map.get(key)


class _FakeJoern:
    def capabilities(self):
        return [ToolCapability.CALL_GRAPH, ToolCapability.TAINT_ANALYSIS]

    async def health_check(self):
        return ToolHealth(True, "running")

    async def prepare(self, _request):
        return None

    async def cleanup(self, _request):
        return None

    async def method_list(self):
        return [{"name": "main", "filename": "iscsi.c", "line": 10}]

    async def query_custom(self, query):
        return [{"query": query, "result": "ok"}]

    async def function_branches(self, method_name):
        return [{"type": "IF", "method": method_name}]

    async def error_paths(self, method_name):
        return [{"type": "error", "method": method_name}]

    async def boundary_values(self, method_name):
        return [{"operator": "<=", "method": method_name}]

    async def taint_analysis(self, _source, _sink):
        return [[("input", "iscsi.c", 10), ("sink", "iscsi.c", 20)]]


class _FakeSemgrep:
    def capabilities(self):
        return [ToolCapability.SECURITY_SCAN]

    async def health_check(self):
        return ToolHealth(True, "running")

    async def analyze(self, _request):
        return UnifiedResult(
            tool_name="semgrep",
            capability=ToolCapability.SECURITY_SCAN,
            data={
                "summary": {"total": 1},
                "categorized": {"security": 1},
                "findings": [
                    {
                        "check_id": "security.sql-injection",
                        "extra": {"metadata": {"category": "security"}},
                    }
                ],
            },
            metadata={"engine": "semgrep"},
        )

    async def scan_with_severity(self, _tool_path, severity):
        return {
            "results": [
                {
                    "check_id": f"{severity.lower()}.example",
                    "extra": {"metadata": {"category": "security"}},
                }
            ]
        }

    async def scan_incremental(self, _tool_path, baseline_commit):
        return {
            "status": "completed",
            "baseline_commit": baseline_commit,
            "findings": [{"check_id": "security.new-only"}],
        }


class RepoAnalysisRouteContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.holder = {"db": None}

        async def _fake_db():
            yield self.holder["db"]

        app.dependency_overrides[repo_analysis_api.get_db] = _fake_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self._saved_cache = dict(repo_analysis_api._findings_cache)
        repo_analysis_api._findings_cache.clear()

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        repo_analysis_api._findings_cache.clear()
        repo_analysis_api._findings_cache.update(self._saved_cache)

    async def test_analysis_summary_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_analysis_api, "_joern", return_value=_FakeJoern()
        ), patch.object(
            repo_analysis_api, "_semgrep", return_value=_FakeSemgrep()
        ):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/summary"
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["repo_id"], str(repo_id))
        self.assertEqual(body["repo_name"], "open-iscsi")
        self.assertTrue(body["tools"]["joern"]["healthy"])
        self.assertTrue(body["tools"]["semgrep"]["healthy"])

    async def test_semgrep_findings_cached_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})
        repo_analysis_api._findings_cache[str(repo_id)] = [
            {
                "check_id": "security.sql-injection",
                "extra": {"metadata": {"category": "security"}},
            },
            {
                "check_id": "style.dead-code",
                "extra": {"metadata": {"category": "style"}},
            },
        ]

        response = await self.client.get(
            f"/api/repos/{repo_id}/analysis/semgrep/findings?category=security"
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["pages"], 1)
        self.assertEqual(body["findings"][0]["check_id"], "security.sql-injection")

    async def test_semgrep_scan_connect_error_maps_to_503(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})
        fake_semgrep = _FakeSemgrep()
        fake_semgrep.analyze = AsyncMock(
            side_effect=httpx.ConnectError("boom", request=httpx.Request("POST", "http://semgrep"))
        )

        with patch.object(
            repo_analysis_api, "_semgrep", return_value=fake_semgrep
        ):
            response = await self.client.post(
                f"/api/repos/{repo_id}/analysis/semgrep/scan"
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"detail": "Semgrep service unavailable"}
        )

    async def test_semgrep_scan_success_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_analysis_api, "_semgrep", return_value=_FakeSemgrep()
        ):
            response = await self.client.post(
                f"/api/repos/{repo_id}/analysis/semgrep/scan"
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["summary"], {"total": 1})
        self.assertEqual(body["categorized"], {"security": 1})
        self.assertEqual(body["findings"][0]["check_id"], "security.sql-injection")
        self.assertEqual(
            repo_analysis_api._findings_cache[str(repo_id)][0]["check_id"],
            "security.sql-injection",
        )

    async def test_joern_methods_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_analysis_api, "_joern", return_value=_FakeJoern()
        ):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/joern/methods"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"methods": [{"name": "main", "filename": "iscsi.c", "line": 10}]},
        )

    async def test_joern_custom_query_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_analysis_api, "_joern", return_value=_FakeJoern()
        ):
            response = await self.client.post(
                f"/api/repos/{repo_id}/analysis/joern/query",
                json={"query": "cpg.method.name(\"main\").l"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "result": [
                    {
                        "query": 'cpg.method.name("main").l',
                        "result": "ok",
                    }
                ]
            },
        )

    async def test_joern_method_all_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_analysis_api, "_joern", return_value=_FakeJoern()
        ):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/joern/method/main/all"
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["method"], "main")
        self.assertEqual(body["branches"][0]["type"], "IF")
        self.assertEqual(body["errors"][0]["type"], "error")
        self.assertEqual(body["boundaries"][0]["operator"], "<=")

    async def test_joern_method_branches_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_analysis_api, "_joern", return_value=_FakeJoern()
        ):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/joern/method/main/branches"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"method": "main", "branches": [{"type": "IF", "method": "main"}]},
        )

    async def test_joern_method_errors_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_analysis_api, "_joern", return_value=_FakeJoern()
        ):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/joern/method/main/errors"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"method": "main", "errors": [{"type": "error", "method": "main"}]},
        )

    async def test_joern_method_boundaries_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_analysis_api, "_joern", return_value=_FakeJoern()
        ):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/joern/method/main/boundaries"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "method": "main",
                "boundaries": [{"operator": "<=", "method": "main"}],
            },
        )

    async def test_joern_taint_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_analysis_api, "_joern", return_value=_FakeJoern()
        ):
            response = await self.client.post(
                f"/api/repos/{repo_id}/analysis/joern/taint",
                json={"source": "input", "sink": "sink"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source"], "input")
        self.assertEqual(body["sink"], "sink")
        self.assertEqual(body["paths"][0]["elements"][0]["code"], "input")
        self.assertEqual(body["paths"][0]["elements"][1]["line_number"], 20)

    async def test_generate_test_points_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        llm_cfg = SimpleNamespace(
            provider="custom",
            model_name="mimo-v2-pro",
        )
        self.holder["db"] = _FakeDB(
            get_map={repo_id: repo},
            execute_results=[_FakeResult(scalar_value=llm_cfg)],
        )

        with patch(
            "app.services.test_point_generator.generate_test_points",
            new=AsyncMock(return_value=[{"title": "boundary case"}]),
        ) as gen_mock:
            response = await self.client.post(
                f"/api/repos/{repo_id}/analysis/test-points",
                json={"target": "main", "perspective": "black_box"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["test_points"][0]["title"], "boundary case")
        kwargs = gen_mock.await_args.kwargs
        self.assertEqual(kwargs["repo_path"], "/data/repos/open-iscsi")
        self.assertEqual(
            kwargs["llm_config"], {"provider": "openai", "model": "mimo-v2-pro"}
        )

    async def test_semgrep_incremental_scan_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_analysis_api, "_semgrep", return_value=_FakeSemgrep()
        ):
            response = await self.client.post(
                f"/api/repos/{repo_id}/analysis/semgrep/scan/incremental",
                json={"baseline_commit": "abc123"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "completed",
                "baseline_commit": "abc123",
                "findings": [{"check_id": "security.new-only"}],
            },
        )


if __name__ == "__main__":
    unittest.main()
