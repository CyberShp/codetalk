from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.utils import repo_paths


class RepoPathTests(unittest.TestCase):
    def test_default_repos_base_path_uses_project_local_dir_for_host_run(self) -> None:
        repo_root = Path("/workspace/codetalk")

        result = repo_paths.default_repos_base_path(
            repo_root=repo_root,
            in_container=False,
        )

        self.assertEqual(result, "/workspace/codetalk/.repos")

    def test_default_repos_base_path_keeps_container_mount_inside_container(self) -> None:
        repo_root = Path("/workspace/codetalk")

        result = repo_paths.default_repos_base_path(
            repo_root=repo_root,
            in_container=True,
        )

        self.assertEqual(result, "/data/repos")

    def test_to_tool_repo_path_maps_host_repo_into_container_mount(self) -> None:
        result = repo_paths.to_tool_repo_path(
            "/Volumes/Media/codetalk/.repos/abc123",
            host_base_path="/Volumes/Media/codetalk/.repos",
            tool_base_path="/data/repos",
        )

        self.assertEqual(result, "/data/repos/abc123")

    def test_to_tool_repo_path_leaves_unmanaged_paths_unchanged(self) -> None:
        result = repo_paths.to_tool_repo_path(
            "/tmp/external-repo",
            host_base_path="/Volumes/Media/codetalk/.repos",
            tool_base_path="/data/repos",
        )

        self.assertEqual(result, "/tmp/external-repo")

    def test_to_tool_repo_path_handles_windows_host_path_in_linux_container(self) -> None:
        result = repo_paths.to_tool_repo_path(
            r"D:\coworkers\codetalk\.repos\7cf1a08b-abcd-1234-efgh-000000000000",
            host_base_path=r"D:\coworkers\codetalk\.repos",
            tool_base_path="/data/repos",
        )

        self.assertEqual(result, "/data/repos/7cf1a08b-abcd-1234-efgh-000000000000")

    def test_to_tool_repo_path_windows_unmanaged_path_returned_unchanged(self) -> None:
        result = repo_paths.to_tool_repo_path(
            r"C:\other-location\some-repo",
            host_base_path=r"D:\coworkers\codetalk\.repos",
            tool_base_path="/data/repos",
        )

        self.assertEqual(result, r"C:\other-location\some-repo")

    def test_ensure_repos_base_path_creates_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "repos"

            repo_paths.ensure_repos_base_path(str(base))

            self.assertTrue(base.is_dir())

    def test_running_in_container_returns_false_on_host(self) -> None:
        """On a host machine (no /.dockerenv), returns False."""
        result = repo_paths.running_in_container()
        self.assertFalse(result)

    def test_default_repos_base_path_autodetects_container(self) -> None:
        """When in_container=None, calls running_in_container() — mocked to True."""
        repo_root = Path("/workspace/codetalk")
        with patch.object(repo_paths, "running_in_container", return_value=True):
            result = repo_paths.default_repos_base_path(repo_root=repo_root)
        self.assertEqual(result, "/data/repos")

    def test_default_repos_base_path_autodetects_host(self) -> None:
        """When in_container=None and running_in_container()=False, uses local .repos."""
        repo_root = Path("/workspace/codetalk")
        with patch.object(repo_paths, "running_in_container", return_value=False):
            result = repo_paths.default_repos_base_path(repo_root=repo_root)
        self.assertEqual(result, str((repo_root / ".repos").resolve()))

    def test_translate_path_empty_host_base_returns_none(self) -> None:
        """_translate_path returns None immediately when host_base is empty."""
        result = repo_paths._translate_path("/some/path", "", "/container")
        self.assertIsNone(result)

    def test_ensure_repos_base_path_raises_when_not_writable(self) -> None:
        """ensure_repos_base_path raises RuntimeError when directory is not writable."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "locked"
            base.mkdir()
            with patch("os.access", return_value=False):
                with self.assertRaises(RuntimeError):
                    repo_paths.ensure_repos_base_path(str(base))

    def test_to_tool_repo_path_uses_local_mapping_first(self) -> None:
        """to_tool_repo_path tries local_host_path before host_base_path."""
        result = repo_paths.to_tool_repo_path(
            "/local/repos/myrepo",
            host_base_path="/managed/repos",
            tool_base_path="/data/repos",
            local_host_path="/local/repos",
            local_container_path="/container/local",
        )
        self.assertEqual(result, "/container/local/myrepo")

    def test_to_tool_repo_path_falls_back_to_managed_when_local_no_match(self) -> None:
        """to_tool_repo_path falls back to host_base_path when local path doesn't match."""
        result = repo_paths.to_tool_repo_path(
            "/managed/repos/myrepo",
            host_base_path="/managed/repos",
            tool_base_path="/data/repos",
            local_host_path="/local/repos",
            local_container_path="/container/local",
        )
        self.assertEqual(result, "/data/repos/myrepo")


if __name__ == "__main__":
    unittest.main()
