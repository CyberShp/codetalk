import base64
import unittest

from app.api import chat


def _line_match(line_number: int, text: str) -> dict:
    return {
        "LineNumber": line_number,
        "Line": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


class ChatHelperTests(unittest.TestCase):
    def test_extract_symbol_candidates_handles_cjk_adjacent_identifier(self) -> None:
        self.assertEqual(
            chat._extract_symbol_candidates("collectOne函数有什么用"),
            ["collectOne"],
        )

    def test_build_scoped_queries_prioritizes_symbol_lookup(self) -> None:
        queries = chat._build_scoped_queries(
            repo_key="repo-123",
            query="what does collectOne do",
        )

        self.assertGreaterEqual(len(queries), 3)
        self.assertEqual(queries[0], "repo:repo-123 sym:collectOne")
        self.assertEqual(queries[1], "repo:repo-123 collectOne")

    def test_pick_best_line_match_prefers_definition(self) -> None:
        line_number = chat._pick_best_line_match(
            [
                _line_match(88, "result = collectOne(item)"),
                _line_match(12, "def collectOne(test_name):"),
            ],
            ["collectOne"],
        )

        self.assertEqual(line_number, 12)

    def test_extract_symbol_candidates_ignores_short_plain_words(self) -> None:
        self.assertEqual(
            chat._extract_symbol_candidates("what does this do"),
            [],
        )

    def test_pick_best_line_match_prefers_c_style_definition(self) -> None:
        line_number = chat._pick_best_line_match(
            [
                _line_match(320, "collectOne(ctx);"),
                _line_match(41, "int collectOne(struct worker_ctx *ctx) {"),
            ],
            ["collectOne"],
        )
        self.assertEqual(line_number, 41)


if __name__ == "__main__":
    unittest.main()
