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

    def scalars(self):
        """Support result.scalars().all() for list queries."""
        if isinstance(self._scalar_value, _FakeScalarsResult):
            return self._scalar_value
        return _FakeScalarsResult([self._scalar_value] if self._scalar_value else [])


class _FakeScalarsResult:
    """Wraps a list to satisfy .scalars().all() chain."""
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeDB:
    def __init__(self, *, execute_results=None, get_map=None):
        self._execute_results = list(execute_results or [])
        self._get_map = dict(get_map or {})
        self._added = []

    async def execute(self, _query):
        assert self._execute_results, "unexpected execute() call"
        return self._execute_results.pop(0)

    async def get(self, _model, key):
        return self._get_map.get(key)

    def add(self, obj):
        self._added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        # Simulate DB-generated fields
        import uuid as _uuid
        from datetime import datetime, timezone
        if not getattr(obj, "id", None):
            obj.id = _uuid.uuid4()
        if not getattr(obj, "created_at", None):
            obj.created_at = datetime.now(timezone.utc)


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
        return [{"name": "main", "filename": "iscsi.c", "line": 10, "lineEnd": 50, "paramCount": 2, "complexity": 5}]

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

    async def absence_analysis(self, _source, _sink):
        return [{"method": "read_data", "file": "io.c", "elements": [
            {"code": "open(path)", "file": "io.c", "line": "15", "role": "source"}
        ]}]

    async def scoped_taint_verify(self, method_name, source, sink):
        return [[{"code": source, "file": "iscsi.c", "line": "10"},
                 {"code": sink, "file": "iscsi.c", "line": "20"}]]

    async def call_context(self, method_name):
        return [{"caller": "init", "callerFile": "main.c", "callerLine": 5,
                 "callSites": [{"line": 15}], "callerBranches": []}]


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
        body = response.json()
        self.assertEqual(len(body["methods"]), 1)
        m = body["methods"][0]
        self.assertEqual(m["name"], "main")
        self.assertEqual(m["filename"], "iscsi.c")
        self.assertEqual(m["complexity"], 5)

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

    async def test_joern_variable_tracking_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        fake_joern = _FakeJoern()
        fake_joern.variable_tracking = AsyncMock(return_value=[{"code": "x", "line_number": 10}])

        with patch.object(
            repo_analysis_api, "_joern", return_value=fake_joern
        ):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/joern/method/main/variable/x/track"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "method": "main",
                "variable": "x",
                "usages": [{"code": "x", "line_number": 10}],
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

    async def test_joern_taint_absence_mode_contract(self) -> None:
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
                json={"source": "open", "sink": "close", "mode": "absence"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["mode"], "absence")
        self.assertEqual(len(body["paths"]), 1)
        self.assertEqual(body["paths"][0]["method"], "read_data")
        self.assertEqual(body["paths"][0]["elements"][0]["code"], "open(path)")
        self.assertTrue(body["paths"][0]["elements"][0]["is_source"])

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

    async def test_taint_verify_contract(self) -> None:
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
                f"/api/repos/{repo_id}/analysis/joern/taint-verify",
                json={"method": "main", "source": "getParameter", "sink": "executeQuery"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["verified"])
        self.assertEqual(len(body["flows"]), 1)
        self.assertEqual(body["flows"][0]["elements"][0]["code"], "getParameter")

    async def test_taint_verify_timeout_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        fake_joern = _FakeJoern()
        fake_joern.scoped_taint_verify = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))

        with patch.object(
            repo_analysis_api, "_joern", return_value=fake_joern
        ):
            response = await self.client.post(
                f"/api/repos/{repo_id}/analysis/joern/taint-verify",
                json={"method": "main", "source": "src", "sink": "snk"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["verified"])
        self.assertEqual(body["fallback"], "timeout")

    async def test_snapshot_save_and_list_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        db = _FakeDB(
            get_map={repo_id: repo},
            execute_results=[_FakeResult(_FakeScalarsResult([]))],
        )
        self.holder["db"] = db

        # Save snapshot
        response = await self.client.post(
            f"/api/repos/{repo_id}/analysis/snapshots",
            json={
                "risk_matrix": [{"name": "main", "risk": "HIGH"}],
                "summary": {"total": 10, "high": 3, "med": 2, "avgComplexity": 8.5},
            },
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn("id", body)
        self.assertEqual(body["repository_id"], str(repo_id))

        # List snapshots — re-set db since execute_results was consumed
        self.holder["db"] = _FakeDB(
            get_map={repo_id: repo},
            execute_results=[_FakeResult(_FakeScalarsResult([]))],
        )
        response = await self.client.get(
            f"/api/repos/{repo_id}/analysis/snapshots"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("snapshots", response.json())

    async def test_impact_radius_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_analysis_api, "_joern", return_value=_FakeJoern()
        ), patch(
            "app.adapters.gitnexus.GitNexusAdapter", side_effect=Exception("unavailable")
        ):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/impact-radius/main"
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["method"], "main")
        self.assertEqual(body["caller_count"], 1)
        self.assertIn("main.c", body["caller_files"])


if __name__ == "__main__":
    unittest.main()
