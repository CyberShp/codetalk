"""Unit tests for material_rag pure functions (Layer 1 — no DB, no network)."""

import math
import struct

import pytest

from app.services.material_rag import (
    _cosine_similarity,
    _estimate_tokens,
    _pack_embedding,
    _read_material_file,
    _unpack_embedding,
    chunk_text,
)


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------


class TestChunkText:
    def test_empty_string(self):
        assert chunk_text("") == []

    def test_whitespace_only(self):
        assert chunk_text("   \n\n  ") == []

    def test_short_document_single_chunk(self):
        text = "Hello world. This is a short document."
        chunks = chunk_text(text, chunk_size=1000)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_markdown_header_splitting(self):
        text = "## Section 1\nContent A\n## Section 2\nContent B"
        chunks = chunk_text(text, chunk_size=1000)
        assert len(chunks) == 2
        assert "Section 1" in chunks[0]
        assert "Section 2" in chunks[1]

    def test_single_hash_not_split(self):
        text = "# Title\nIntro\n## Section\nBody"
        chunks = chunk_text(text, chunk_size=1000)
        assert len(chunks) == 2
        assert "# Title" in chunks[0]
        assert "## Section" in chunks[1]

    def test_long_section_splits_on_paragraphs(self):
        paragraphs = [f"Paragraph {i}. " + "word " * 80 for i in range(10)]
        text = "\n\n".join(paragraphs)
        chunks = chunk_text(text, chunk_size=100, overlap=10)
        assert len(chunks) > 1
        for c in chunks:
            assert c.strip()

    def test_overlap_carries_content(self):
        para_a = "Alpha paragraph. " + "filler " * 60
        para_b = "Beta paragraph. " + "filler " * 60
        para_c = "Gamma paragraph. " + "filler " * 60
        text = f"{para_a}\n\n{para_b}\n\n{para_c}"
        chunks = chunk_text(text, chunk_size=80, overlap=40)
        if len(chunks) >= 2:
            assert any(
                "Beta" in c or "Alpha" in c for c in chunks[1:]
            ), "Overlap should carry content from previous chunk"

    def test_unicode_cjk(self):
        text = "## 测试标题\n这是中文测试内容，包含多个段落。"
        chunks = chunk_text(text, chunk_size=1000)
        assert len(chunks) == 1
        assert "测试标题" in chunks[0]

    def test_empty_sections_filtered(self):
        text = "## A\n\n## B\nContent"
        chunks = chunk_text(text, chunk_size=1000)
        assert all(c.strip() for c in chunks)

    def test_no_header_document(self):
        text = "Just a plain text document with no markdown headers at all."
        chunks = chunk_text(text, chunk_size=1000)
        assert len(chunks) == 1


# ---------------------------------------------------------------------------
# _cosine_similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert _cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_dimension_mismatch_returns_zero(self):
        assert _cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0

    def test_zero_vector_a(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_zero_vector_b(self):
        assert _cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0

    def test_both_zero(self):
        assert _cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0

    def test_known_angle(self):
        a = [1.0, 0.0]
        b = [1.0, 1.0]
        expected = 1.0 / math.sqrt(2)
        assert _cosine_similarity(a, b) == pytest.approx(expected)

    def test_empty_vectors(self):
        assert _cosine_similarity([], []) == 0.0


# ---------------------------------------------------------------------------
# _pack_embedding / _unpack_embedding
# ---------------------------------------------------------------------------


class TestPackUnpack:
    def test_roundtrip(self):
        vec = [0.1, 0.2, -0.3, 1.0, 0.0]
        packed = _pack_embedding(vec)
        unpacked = _unpack_embedding(packed)
        assert len(unpacked) == len(vec)
        for a, b in zip(vec, unpacked):
            assert a == pytest.approx(b, abs=1e-6)

    def test_empty_vector(self):
        packed = _pack_embedding([])
        assert packed == b""
        assert _unpack_embedding(b"") == []

    def test_single_element(self):
        vec = [42.5]
        assert _unpack_embedding(_pack_embedding(vec)) == pytest.approx(vec)

    def test_packed_size(self):
        vec = [1.0] * 1536
        packed = _pack_embedding(vec)
        assert len(packed) == 1536 * 4

    def test_float32_precision(self):
        original = 0.123456789
        packed = _pack_embedding([original])
        unpacked = _unpack_embedding(packed)[0]
        assert unpacked == pytest.approx(original, abs=1e-6)
        f32 = struct.unpack("<f", struct.pack("<f", original))[0]
        assert unpacked == f32


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_string(self):
        assert _estimate_tokens("") == 0

    def test_returns_positive_for_text(self):
        assert _estimate_tokens("Hello world") > 0

    def test_longer_text_more_tokens(self):
        short = _estimate_tokens("hi")
        long = _estimate_tokens("This is a significantly longer piece of text with many words")
        assert long > short


# ---------------------------------------------------------------------------
# _read_material_file
# ---------------------------------------------------------------------------


class TestReadMaterialFile:
    def test_normal_file(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("Hello world", encoding="utf-8")
        assert _read_material_file(str(f)) == "Hello world"

    def test_nonexistent_file(self):
        assert _read_material_file("/nonexistent/path/file.txt") == ""

    def test_directory_path(self, tmp_path):
        assert _read_material_file(str(tmp_path)) == ""

    def test_large_file_truncated(self, tmp_path):
        f = tmp_path / "large.txt"
        content = "x" * 200_000
        f.write_text(content, encoding="utf-8")
        result = _read_material_file(str(f))
        assert len(result) <= 100_000

    def test_exactly_at_limit(self, tmp_path):
        f = tmp_path / "exact.txt"
        content = "a" * 100_000
        f.write_text(content, encoding="utf-8")
        result = _read_material_file(str(f))
        assert result == content

    def test_binary_file_replace_errors(self, tmp_path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\xff\xfe\x00\x01hello\xff")
        result = _read_material_file(str(f))
        assert "hello" in result

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        assert _read_material_file(str(f)) == ""

    def test_utf8_bom(self, tmp_path):
        f = tmp_path / "bom.txt"
        f.write_bytes(b"\xef\xbb\xbfHello BOM")
        result = _read_material_file(str(f))
        assert "Hello BOM" in result
