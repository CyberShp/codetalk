import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.adapters.base import ToolCapability, ToolHealth
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

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()

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
        ):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/summary"
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["repo_id"], str(repo_id))
        self.assertEqual(body["repo_name"], "open-iscsi")
        self.assertTrue(body["tools"]["joern"]["healthy"])

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

if __name__ == "__main__":
    unittest.main()
