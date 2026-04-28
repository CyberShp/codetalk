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

        fake_cc = AsyncMock()
        fake_cc.health_check = AsyncMock(return_value=ToolHealth(False, "error", last_check="connect refused"))
        fake_cc.capabilities = lambda: [ToolCapability.CALL_GRAPH, ToolCapability.POINTER_ANALYSIS, ToolCapability.DEPENDENCY_GRAPH, ToolCapability.ARCHITECTURE_DIAGRAM]

        with patch.object(
            repo_analysis_api, "_joern", return_value=_FakeJoern()
        ), patch.object(
            repo_analysis_api, "_codecompass", return_value=fake_cc
        ):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/summary"
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["repo_id"], str(repo_id))
        self.assertEqual(body["repo_name"], "open-iscsi")
        self.assertTrue(body["tools"]["joern"]["healthy"])
        # CodeCompass returns unhealthy but still appears in summary
        self.assertFalse(body["tools"]["codecompass"]["healthy"])
        self.assertIn("pointer_analysis", body["tools"]["codecompass"]["capabilities"])

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

    async def test_impact_radius_degraded_contract(self) -> None:
        """GitNexus unavailable — endpoint still returns 200 with empty module_deps."""
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
        self.assertEqual(body["module_dep_count"], 0)

    async def test_impact_radius_happy_path_contract(self) -> None:
        """GitNexus available — endpoint returns Joern callers + GitNexus module deps."""
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path="/Volumes/Media/codetalk/.repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        # Build a fake GitNexus adapter that returns relationship data
        fake_gn = AsyncMock()
        fake_gn.prepare = AsyncMock()
        fake_gn.cleanup = AsyncMock()
        fake_gn.analyze = AsyncMock(return_value=SimpleNamespace(data={
            "relationships": [
                {"source": "main.c", "target": "util.c", "type": "IMPORTS"},
                {"source": "config.h", "target": "unrelated.c", "type": "IMPORTS"},
            ]
        }))

        with patch.object(
            repo_analysis_api, "_joern", return_value=_FakeJoern()
        ), patch(
            "app.adapters.gitnexus.GitNexusAdapter", return_value=fake_gn
        ):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/impact-radius/main"
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["method"], "main")
        self.assertEqual(body["caller_count"], 1)
        self.assertIn("main.c", body["caller_files"])
        # GitNexus returned a relationship matching a caller file → module deps populated
        self.assertGreater(body["module_dep_count"], 0)
        self.assertTrue(
            any(d["source"] == "main.c" for d in body["module_dependencies"])
        )

    async def test_codecompass_adapter_contract(self) -> None:
        from app.adapters.codecompass import CodeCompassAdapter
        adapter = CodeCompassAdapter(base_url="http://localhost:6251")
        self.assertEqual(adapter.name(), "codecompass")
        caps = {c.value for c in adapter.capabilities()}
        self.assertEqual(caps, {"call_graph", "pointer_analysis", "dependency_graph", "architecture_diagram"})

    async def test_codecompass_rebuild_passes_raw_local_path(self) -> None:
        """Regression: rebuild must pass raw local_path, not pre-translated tool path.

        CodeCompassAdapter.prepare() does its own to_tool_repo_path() internally.
        If rebuild pre-translates, _has_supported_files() gets a container path
        that doesn't exist on the host, silently skipping parse.
        """
        repo_id = uuid.uuid4()
        host_path = "/Volumes/Media/codetalk/.repos/some-project"
        repo = SimpleNamespace(
            id=repo_id,
            name="some-project",
            local_path=host_path,
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        fake_cc = AsyncMock()
        fake_cc.base_url = "http://codecompass:6251"
        fake_cc._current_workspace = "old-project"
        fake_cc.prepare = AsyncMock()

        with patch.object(
            repo_analysis_api, "_codecompass", return_value=fake_cc
        ), patch.object(
            repo_analysis_api.CodeCompassAdapter, "clear_cached_project"
        ) as clear_mock:
            response = await self.client.post(
                f"/api/repos/{repo_id}/analysis/codecompass/rebuild"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "rebuilt")

        # Critical assertion: prepare() must receive the RAW host path,
        # NOT the tool-translated /data/repos/... path
        call_args = fake_cc.prepare.await_args
        actual_path = call_args[0][0].repo_local_path
        self.assertEqual(actual_path, host_path)
        self.assertNotIn("/data/repos", actual_path)

        # Verify cache was cleared
        clear_mock.assert_called_once_with(fake_cc.base_url)
        self.assertIsNone(fake_cc._current_workspace)


    async def test_codecompass_call_graph_route(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id, name="linux-driver",
            local_path="/Volumes/Media/codetalk/.repos/linux-driver",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        fake_cc = AsyncMock()
        fake_cc.prepare = AsyncMock()
        fake_cc.function_call_graph = AsyncMock(return_value={
            "callers": [{"name": "init_module", "file": "main.c", "line": 10}],
            "callees": [{"name": "alloc_buffer", "file": "mem.c", "line": 45}],
        })

        with patch.object(repo_analysis_api, "_codecompass", return_value=fake_cc):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/codecompass/call-graph/process_packet"
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["function"], "process_packet")
        self.assertIn("callers", body["call_graph"])
        self.assertIn("callees", body["call_graph"])
        fake_cc.function_call_graph.assert_awaited_once_with("process_packet")

    async def test_codecompass_pointer_analysis_route(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id, name="linux-driver",
            local_path="/Volumes/Media/codetalk/.repos/linux-driver",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        fake_cc = AsyncMock()
        fake_cc.prepare = AsyncMock()
        fake_cc.pointer_analysis_for = AsyncMock(return_value={
            "aliases": [{"ptr": "buf", "may_alias": ["shared_buf", "temp_buf"]}],
        })

        with patch.object(repo_analysis_api, "_codecompass", return_value=fake_cc):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/codecompass/pointer-analysis/write_data"
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["function"], "write_data")
        self.assertIn("aliases", body["pointer_analysis"])
        fake_cc.pointer_analysis_for.assert_awaited_once_with("write_data")

    async def test_codecompass_indirect_calls_route(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id, name="linux-driver",
            local_path="/Volumes/Media/codetalk/.repos/linux-driver",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        fake_cc = AsyncMock()
        fake_cc.prepare = AsyncMock()
        fake_cc.indirect_calls = AsyncMock(return_value={
            "targets": [
                {"name": "usb_reset", "file": "usb.c", "line": 120},
                {"name": "pci_reset", "file": "pci.c", "line": 88},
            ],
        })

        with patch.object(repo_analysis_api, "_codecompass", return_value=fake_cc):
            response = await self.client.get(
                f"/api/repos/{repo_id}/analysis/codecompass/indirect-calls/dispatch_reset"
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["function"], "dispatch_reset")
        self.assertEqual(len(body["indirect_calls"]["targets"]), 2)

    async def test_codecompass_alias_analysis_route(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id, name="linux-driver",
            local_path="/Volumes/Media/codetalk/.repos/linux-driver",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        fake_cc = AsyncMock()
        fake_cc.prepare = AsyncMock()
        fake_cc.alias_analysis = AsyncMock(return_value={
            "alias_set": ["ctx->buf", "shared_buffer", "dma_region"],
            "confidence": "may",
        })

        with patch.object(repo_analysis_api, "_codecompass", return_value=fake_cc):
            response = await self.client.post(
                f"/api/repos/{repo_id}/analysis/codecompass/alias",
                json={"variable": "buf", "file_path": "driver.c", "line": 42},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["variable"], "buf")
        self.assertEqual(body["file"], "driver.c")
        self.assertEqual(body["line"], 42)
        self.assertIn("dma_region", body["aliases"]["alias_set"])
        fake_cc.alias_analysis.assert_awaited_once_with("buf", "driver.c", 42)

    async def test_codecompass_routes_pass_raw_local_path(self) -> None:
        """All CodeCompass routes must pass raw local_path to prepare()."""
        repo_id = uuid.uuid4()
        host_path = "/Volumes/Media/codetalk/.repos/test-project"
        repo = SimpleNamespace(
            id=repo_id, name="test-project", local_path=host_path,
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        fake_cc = AsyncMock()
        fake_cc.prepare = AsyncMock()
        fake_cc.function_call_graph = AsyncMock(return_value={})
        fake_cc.pointer_analysis_for = AsyncMock(return_value={})
        fake_cc.indirect_calls = AsyncMock(return_value={})

        with patch.object(repo_analysis_api, "_codecompass", return_value=fake_cc):
            await self.client.get(
                f"/api/repos/{repo_id}/analysis/codecompass/call-graph/fn1"
            )
            await self.client.get(
                f"/api/repos/{repo_id}/analysis/codecompass/pointer-analysis/fn2"
            )
            await self.client.get(
                f"/api/repos/{repo_id}/analysis/codecompass/indirect-calls/fn3"
            )

        # Every prepare() call must receive the raw host path
        for call in fake_cc.prepare.await_args_list:
            actual_path = call[0][0].repo_local_path
            self.assertEqual(actual_path, host_path,
                f"prepare() got '{actual_path}' instead of raw host path")


if __name__ == "__main__":
    unittest.main()
