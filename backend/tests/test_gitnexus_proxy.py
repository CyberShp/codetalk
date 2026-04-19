import unittest
from unittest.mock import patch

from app.api import gitnexus_proxy


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, path: str, params: dict[str, str] | None = None):
        self.calls.append((path, params))
        return self.response


class _FakeSearchClient:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.calls: list[tuple[str, dict[str, str] | None, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(
        self,
        path: str,
        params: dict[str, str] | None = None,
        json: dict | None = None,
    ):
        self.calls.append((path, params, json))
        return self.response


class GitNexusFileProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_file_content_normalizes_repo_mode_to_file_slice(self) -> None:
        fake_client = _FakeAsyncClient(
            _FakeResponse(
                {
                    "content": "line 10\nline 11\n",
                    "path": "src/foo.c",
                }
            )
        )

        with patch.object(gitnexus_proxy.httpx, "AsyncClient", return_value=fake_client):
            result = await gitnexus_proxy.get_file_content(
                repo="repo-uuid",
                path="foo.c",
                start_line=10,
                end_line=11,
            )

        self.assertEqual(fake_client.calls, [("/api/file", {"repo": "repo-uuid", "path": "foo.c", "startLine": "10", "endLine": "11"})])
        self.assertEqual(
            result,
            {
                "content": "line 10\nline 11\n",
                "startLine": 10,
                "endLine": 11,
                "totalLines": 2,
                "actualPath": "src/foo.c",
            },
        )

    async def test_get_file_content_accepts_snake_case_gitnexus_payload(self) -> None:
        fake_client = _FakeAsyncClient(
            _FakeResponse(
                {
                    "content": "alpha\nbeta\n",
                    "start_line": 7,
                    "end_line": 8,
                    "total_lines": 42,
                    "actual_path": "drivers/iscsi.c",
                }
            )
        )

        with patch.object(gitnexus_proxy.httpx, "AsyncClient", return_value=fake_client):
            result = await gitnexus_proxy.get_file_content(
                repo="repo-uuid",
                path="drivers/iscsi.c",
                start_line=None,
                end_line=None,
            )

        self.assertEqual(result["startLine"], 7)
        self.assertEqual(result["endLine"], 8)
        self.assertEqual(result["totalLines"], 42)
        self.assertEqual(result["actualPath"], "drivers/iscsi.c")

    async def test_search_knowledge_graph_forwards_repo_and_payload(self) -> None:
        fake_client = _FakeSearchClient(
            _FakeResponse({"results": [{"name": "main"}], "total": 1})
        )

        with patch.object(
            gitnexus_proxy.httpx,
            "AsyncClient",
            return_value=fake_client,
        ):
            result = await gitnexus_proxy.search_knowledge_graph(
                gitnexus_proxy.SearchRequest(
                    query="main",
                    repo="repo-uuid",
                    mode="hybrid",
                    limit=5,
                    enrich=True,
                )
            )

        self.assertEqual(
            fake_client.calls,
            [
                (
                    "/api/search",
                    {"repo": "repo-uuid"},
                    {
                        "query": "main",
                        "mode": "hybrid",
                        "limit": 5,
                        "enrich": True,
                    },
                )
            ],
        )
        self.assertEqual(result, {"results": [{"name": "main"}], "total": 1})

    async def test_cypher_query_forwards_repo_and_query(self) -> None:
        fake_client = _FakeSearchClient(
            _FakeResponse({"results": [{"id": "Function:main"}]})
        )

        with patch.object(
            gitnexus_proxy.httpx,
            "AsyncClient",
            return_value=fake_client,
        ):
            result = await gitnexus_proxy.cypher_query(
                gitnexus_proxy.CypherRequest(
                    cypher="MATCH (n) RETURN n LIMIT 1",
                    repo="repo-uuid",
                )
            )

        self.assertEqual(
            fake_client.calls,
            [
                (
                    "/api/query",
                    {"repo": "repo-uuid"},
                    {"cypher": "MATCH (n) RETURN n LIMIT 1"},
                )
            ],
        )
        self.assertEqual(result, {"results": [{"id": "Function:main"}]})


if __name__ == "__main__":
    unittest.main()
