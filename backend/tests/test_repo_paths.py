from pathlib import Path
import tempfile
import unittest

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

    def test_ensure_repos_base_path_creates_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "repos"

            repo_paths.ensure_repos_base_path(str(base))

            self.assertTrue(base.is_dir())


if __name__ == "__main__":
    unittest.main()
