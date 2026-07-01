"""Cleanup contracts for removed DeepWiki deploy/start runtime residue."""


def test_purge_removed_deepwiki_bytecode_removes_legacy_modules(tmp_path):
    import server

    cache_dir = tmp_path / "backend" / "app" / "api" / "__pycache__"
    cache_dir.mkdir(parents=True)
    removed = cache_dir / "deepwiki_pages.cpython-311.pyc"
    removed_model = cache_dir / "wiki_cache_meta.cpython-311.pyc"
    removed.write_bytes(b"legacy deepwiki bytecode")
    removed_model.write_bytes(b"legacy wiki model bytecode")

    deleted = server.purge_removed_deepwiki_bytecode(tmp_path)

    assert set(deleted) == {removed, removed_model}
    assert not removed.exists()
    assert not removed_model.exists()


def test_purge_removed_deepwiki_bytecode_keeps_unrelated_bytecode(tmp_path):
    import server

    cache_dir = tmp_path / "backend" / "app" / "api" / "__pycache__"
    cache_dir.mkdir(parents=True)
    kept = cache_dir / "agent_workbench.cpython-311.pyc"
    kept.write_bytes(b"current runtime bytecode")

    deleted = server.purge_removed_deepwiki_bytecode(tmp_path)

    assert deleted == []
    assert kept.exists()
