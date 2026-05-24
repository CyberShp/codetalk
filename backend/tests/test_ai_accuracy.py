"""AI generation accuracy evaluation using a real LLM (DeepSeek).

Uses CodeTalk's OpenAICompatClient to send C source code for analysis,
then scores the LLM response against a hand-crafted ground truth.

Requires DEEPSEEK_API_KEY environment variable.
"""

import os
import re

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("DEEPSEEK_API_KEY"),
        reason="DEEPSEEK_API_KEY not set",
    ),
]

_BASE_URL = "https://api.deepseek.com"
_MODEL = "deepseek-chat"
_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# ---------------------------------------------------------------------------
# Test input — header-only SPSC ring buffer (public domain style)
# ---------------------------------------------------------------------------

_C_SOURCE = r"""
/* ringbuf.h — lock-free single-producer single-consumer ring buffer */
#ifndef RINGBUF_H
#define RINGBUF_H

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>
#include <stdatomic.h>

typedef struct {
    uint8_t        *buf;
    size_t          capacity;   /* must be power of two */
    atomic_size_t   head;       /* written by producer */
    atomic_size_t   tail;       /* written by consumer */
} ringbuf_t;

static inline int ringbuf_init(ringbuf_t *rb, size_t cap) {
    if (cap == 0 || (cap & (cap - 1)) != 0)   /* not power of two */
        return -1;
    rb->buf = (uint8_t *)malloc(cap);
    if (!rb->buf) return -1;
    rb->capacity = cap;
    atomic_init(&rb->head, 0);
    atomic_init(&rb->tail, 0);
    return 0;
}

static inline void ringbuf_free(ringbuf_t *rb) {
    free(rb->buf);
    rb->buf = NULL;
}

static inline size_t ringbuf_avail_read(const ringbuf_t *rb) {
    return atomic_load_explicit(&rb->head, memory_order_acquire)
         - atomic_load_explicit(&rb->tail, memory_order_relaxed);
}

static inline size_t ringbuf_avail_write(const ringbuf_t *rb) {
    return rb->capacity - ringbuf_avail_read(rb);
}

static inline size_t ringbuf_write(ringbuf_t *rb,
                                   const uint8_t *data, size_t len) {
    size_t avail = ringbuf_avail_write(rb);
    if (len > avail) len = avail;
    size_t head = atomic_load_explicit(&rb->head, memory_order_relaxed);
    size_t pos  = head & (rb->capacity - 1);
    size_t first = rb->capacity - pos;
    if (first > len) first = len;
    memcpy(rb->buf + pos, data, first);
    memcpy(rb->buf, data + first, len - first);
    atomic_store_explicit(&rb->head, head + len, memory_order_release);
    return len;
}

static inline size_t ringbuf_read(ringbuf_t *rb,
                                  uint8_t *out, size_t len) {
    size_t avail = ringbuf_avail_read(rb);
    if (len > avail) len = avail;
    size_t tail = atomic_load_explicit(&rb->tail, memory_order_relaxed);
    size_t pos  = tail & (rb->capacity - 1);
    size_t first = rb->capacity - pos;
    if (first > len) first = len;
    memcpy(out, rb->buf + pos, first);
    memcpy(out + first, rb->buf + pos + first, len - first);   /* BUG */
    atomic_store_explicit(&rb->tail, tail + len, memory_order_release);
    return len;
}

#endif
"""

# ---------------------------------------------------------------------------
# Ground truth assertions
# ---------------------------------------------------------------------------

_GROUND_TRUTH = [
    {
        "id": "purpose",
        "question": "What is the main purpose of this code?",
        "keywords": ["ring buffer", "circular buffer", "queue"],
        "required_any": True,
    },
    {
        "id": "data_structure",
        "question": "What core data structure is used?",
        "keywords": ["struct", "ringbuf_t", "head", "tail", "buf"],
        "required_any": False,
        "min_matches": 3,
    },
    {
        "id": "constraint",
        "question": "What constraint exists on the capacity?",
        "keywords": ["power of two", "power-of-two", "power of 2"],
        "required_any": True,
    },
    {
        "id": "algorithm",
        "question": "How does the buffer handle wrap-around?",
        "keywords": ["mask", "bitwise", "modulo", "& (capacity - 1)", "wrap"],
        "required_any": True,
    },
    {
        "id": "concurrency",
        "question": "What concurrency model does this implement?",
        "keywords": [
            "lock-free", "lockfree", "atomic", "spsc",
            "single-producer", "single-consumer",
            "memory_order", "acquire", "release",
        ],
        "required_any": True,
    },
    {
        "id": "function_count",
        "question": "How many public functions are there?",
        "keywords": ["6", "six", "init", "free", "read", "write", "avail"],
        "required_any": True,
    },
    {
        "id": "error_handling",
        "question": "How does init handle errors?",
        "keywords": ["return -1", "returns -1", "negative", "error code", "NULL", "malloc"],
        "required_any": True,
    },
    {
        "id": "header_only",
        "question": "Is this a header-only library?",
        "keywords": ["header-only", "header only", "inline", "static inline", ".h"],
        "required_any": True,
    },
    {
        "id": "memory",
        "question": "How is memory managed?",
        "keywords": ["malloc", "free", "dynamic", "heap"],
        "required_any": True,
    },
    {
        "id": "issue",
        "question": "Are there any bugs?",
        "keywords": [
            "bug", "error", "incorrect", "wrong",
            "ringbuf_read", "wrap", "memcpy",
            "second memcpy", "pos + first",
        ],
        "required_any": True,
    },
]


def _score_response(text: str, truths: list[dict]) -> dict:
    """Score an LLM response against ground truth assertions."""
    text_lower = text.lower()
    results = []
    for gt in truths:
        kws = gt["keywords"]
        matched = [k for k in kws if k.lower() in text_lower]
        if gt.get("required_any"):
            passed = len(matched) > 0
        else:
            passed = len(matched) >= gt.get("min_matches", 1)
        results.append({
            "id": gt["id"],
            "passed": passed,
            "matched": matched,
            "total_keywords": len(kws),
        })
    passed_count = sum(1 for r in results if r["passed"])
    return {
        "score": passed_count / len(results) if results else 0,
        "passed": passed_count,
        "total": len(results),
        "details": results,
    }


async def _create_client():
    from app.llm.openai_compat import OpenAICompatClient
    return OpenAICompatClient(
        base_url=_BASE_URL,
        api_key=_API_KEY,
        model=_MODEL,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_ai_analysis_accuracy():
    """LLM should score >= 60% on ground truth assertions (minimax2.5 level)."""
    client = await _create_client()
    try:
        prompt = (
            "Analyze this C source code thoroughly. Cover:\n"
            "1. Purpose and functionality\n"
            "2. Data structures used\n"
            "3. Algorithms and techniques\n"
            "4. Concurrency model\n"
            "5. Error handling\n"
            "6. Memory management\n"
            "7. Any bugs or issues\n\n"
            f"```c\n{_C_SOURCE}\n```"
        )
        resp = await client.complete(
            [{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.3,
        )
        result = _score_response(resp.content, _GROUND_TRUTH)
        print(f"\n{'='*60}")
        print(f"AI Analysis Accuracy: {result['score']:.0%} "
              f"({result['passed']}/{result['total']})")
        print(f"{'='*60}")
        for d in result["details"]:
            status = "PASS" if d["passed"] else "FAIL"
            print(f"  [{status}] {d['id']}: matched {d['matched']}")
        print(f"{'='*60}\n")

        assert result["score"] >= 0.6, (
            f"Accuracy {result['score']:.0%} below 60% threshold. "
            f"Failed: {[d['id'] for d in result['details'] if not d['passed']]}"
        )
    finally:
        await client.close()


async def test_ai_function_identification():
    """LLM should correctly identify all public functions."""
    client = await _create_client()
    try:
        prompt = (
            "List ALL function names defined in this C header. "
            "Output ONLY the function names, one per line.\n\n"
            f"```c\n{_C_SOURCE}\n```"
        )
        resp = await client.complete(
            [{"role": "user", "content": prompt}],
            max_tokens=256,
            temperature=0.1,
        )
        expected = [
            "ringbuf_init", "ringbuf_free",
            "ringbuf_avail_read", "ringbuf_avail_write",
            "ringbuf_write", "ringbuf_read",
        ]
        found = [fn for fn in expected if fn in resp.content]
        accuracy = len(found) / len(expected)
        print(f"\nFunction ID accuracy: {accuracy:.0%} "
              f"({len(found)}/{len(expected)})")
        print(f"  Found: {found}")
        missing = set(expected) - set(found)
        if missing:
            print(f"  Missing: {missing}")

        assert accuracy >= 0.8, (
            f"Function identification {accuracy:.0%} below 80%. "
            f"Missing: {missing}"
        )
    finally:
        await client.close()


async def test_ai_bug_detection():
    """LLM should detect the memcpy bug in ringbuf_read."""
    client = await _create_client()
    try:
        prompt = (
            "This C code contains a bug. Find it and explain what's wrong. "
            "Be specific about which function and which line.\n\n"
            f"```c\n{_C_SOURCE}\n```"
        )
        resp = await client.complete(
            [{"role": "user", "content": prompt}],
            max_tokens=512,
            temperature=0.3,
        )
        content_lower = resp.content.lower()
        indicators = [
            "ringbuf_read" in content_lower,
            "memcpy" in content_lower,
            any(w in content_lower for w in [
                "wrap", "second", "pos + first", "bug",
                "incorrect", "wrong", "error", "overflow",
            ]),
        ]
        score = sum(indicators) / len(indicators)
        print(f"\nBug detection score: {score:.0%}")
        print(f"  Mentions ringbuf_read: {indicators[0]}")
        print(f"  Mentions memcpy: {indicators[1]}")
        print(f"  Identifies the issue: {indicators[2]}")

        assert score >= 0.66, (
            f"Bug detection {score:.0%} below 66% — LLM failed to "
            f"identify the memcpy bug in ringbuf_read"
        )
    finally:
        await client.close()
