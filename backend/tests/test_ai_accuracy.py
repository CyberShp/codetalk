"""AI generation accuracy evaluation using real SPDK source code.

Reads actual C source from SPDK (Storage Performance Development Kit)
and evaluates LLM analysis quality against hand-crafted ground truth.
Uses open-ended prompts without leading the model.

Requires DEEPSEEK_API_KEY environment variable.
"""

import os
from pathlib import Path

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

_SPDK_ROOT = Path(r"D:\coworkers\spdk")
_NVME_NS_PATH = _SPDK_ROOT / "lib" / "nvme" / "nvme_ns.c"


async def _create_client():
    from app.llm.openai_compat import OpenAICompatClient
    return OpenAICompatClient(
        base_url=_BASE_URL,
        api_key=_API_KEY,
        model=_MODEL,
    )


def _read_source(path: Path, max_lines: int = 800) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[:max_lines])


# ---------------------------------------------------------------------------
# Ground truth for SPDK lib/nvme/nvme_ns.c
#
# Each assertion requires min_matches keywords to pass.
# No "required_any: True" shortcuts — every assertion needs real evidence.
# ---------------------------------------------------------------------------

_GROUND_TRUTH = [
    {
        "id": "domain",
        "description": "Identifies this as NVMe / storage driver code",
        "keywords": ["nvme", "namespace", "storage", "driver", "block device", "ssd"],
        "min_matches": 2,
    },
    {
        "id": "project",
        "description": "Recognizes this is from SPDK",
        "keywords": ["spdk", "storage performance development kit"],
        "min_matches": 1,
    },
    {
        "id": "identify_pattern",
        "description": "Understands the NVMe Identify command pattern",
        "keywords": [
            "identify", "admin command", "identify namespace",
            "nvme_ctrlr_cmd_identify", "completion",
        ],
        "min_matches": 2,
    },
    {
        "id": "lba_format",
        "description": "Understands LBA format / sector size calculation",
        "keywords": [
            "lba", "sector size", "lbaf", "lbads", "extended lba",
            "metadata", "format index",
        ],
        "min_matches": 2,
    },
    {
        "id": "command_sets",
        "description": "Identifies multiple IO command set support",
        "keywords": ["zns", "kv", "nvm", "command set", "csi", "io command"],
        "min_matches": 2,
    },
    {
        "id": "memory_mgmt",
        "description": "Explains dual memory allocation strategy",
        "keywords": [
            "calloc", "spdk_zmalloc", "spdk_free", "free",
            "dma", "hugepage", "shared memory",
        ],
        "min_matches": 2,
    },
    {
        "id": "error_handling",
        "description": "Identifies errno-based error handling pattern",
        "keywords": [
            "enomem", "enxio", "einval", "errno", "error code",
            "negative return", "return.*-",
        ],
        "min_matches": 2,
    },
    {
        "id": "feature_flags",
        "description": "Identifies capability flag bitmask pattern",
        "keywords": [
            "flag", "bitmask", "deallocate", "compare", "flush",
            "write zeroes", "reservation", "protection information",
        ],
        "min_matches": 3,
    },
    {
        "id": "quirks",
        "description": "Notices hardware quirk/workaround mechanism",
        "keywords": [
            "quirk", "workaround", "vendor-specific", "intel",
            "hardware-specific", "device-specific",
        ],
        "min_matches": 1,
    },
    {
        "id": "lifecycle",
        "description": "Understands namespace construct/destruct lifecycle",
        "keywords": [
            "construct", "destruct", "lifecycle", "initialization",
            "cleanup", "teardown", "nvme_ns_construct", "nvme_ns_destruct",
        ],
        "min_matches": 2,
    },
    {
        "id": "poll_completion",
        "description": "Identifies synchronous poll-based completion model",
        "keywords": [
            "poll", "completion", "synchronous", "blocking",
            "wait", "adminq", "admin queue",
        ],
        "min_matches": 2,
    },
    {
        "id": "ns_id_descriptor",
        "description": "Recognizes namespace ID descriptor list handling",
        "keywords": [
            "id descriptor", "uuid", "nguid", "nidt",
            "descriptor list", "namespace identifier",
        ],
        "min_matches": 2,
    },
]

# False claims that should NOT appear (penalty for hallucination)
_FALSE_CLAIMS = [
    "user.?space",       # this is kernel-adjacent, not userspace networking
    "file.?system",      # not a filesystem
    "network.?stack",    # not networking code
    "tcp|udp|socket",    # no networking in nvme_ns.c
    "encryption|crypto", # no crypto in this file
    "thread.?pool",      # no thread pool
]


def _score_response(text: str, truths: list[dict], false_claims: list[str]) -> dict:
    import re
    text_lower = text.lower()
    results = []
    for gt in truths:
        kws = gt["keywords"]
        matched = []
        for k in kws:
            if ".*" in k or "|" in k:
                if re.search(k, text_lower):
                    matched.append(k)
            elif k.lower() in text_lower:
                matched.append(k)
        min_req = gt.get("min_matches", 2)
        passed = len(matched) >= min_req
        results.append({
            "id": gt["id"],
            "passed": passed,
            "matched": matched,
            "required": min_req,
            "total_keywords": len(kws),
        })

    hallucinations = []
    for pattern in false_claims:
        if re.search(pattern, text_lower):
            hallucinations.append(pattern)

    passed_count = sum(1 for r in results if r["passed"])
    penalty = len(hallucinations)
    raw_score = passed_count / len(results) if results else 0
    adjusted_score = max(0, (passed_count - penalty) / len(results))

    return {
        "raw_score": raw_score,
        "adjusted_score": adjusted_score,
        "passed": passed_count,
        "total": len(results),
        "penalty": penalty,
        "hallucinations": hallucinations,
        "details": results,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _NVME_NS_PATH.exists(), reason="SPDK source not found")
async def test_spdk_analysis_accuracy():
    """Open-ended analysis of real SPDK code (no leading prompt)."""
    source = _read_source(_NVME_NS_PATH)
    client = await _create_client()
    try:
        resp = await client.complete(
            [{"role": "user", "content": f"Analyze this C source code.\n\n```c\n{source}\n```"}],
            max_tokens=2048,
            temperature=0.3,
        )
        result = _score_response(resp.content, _GROUND_TRUTH, _FALSE_CLAIMS)

        print(f"\n{'='*60}")
        print(f"SPDK Analysis Accuracy (raw):      {result['raw_score']:.0%} "
              f"({result['passed']}/{result['total']})")
        print(f"SPDK Analysis Accuracy (adjusted): {result['adjusted_score']:.0%} "
              f"(penalty: -{result['penalty']} hallucinations)")
        print(f"{'='*60}")
        for d in result["details"]:
            status = "PASS" if d["passed"] else "FAIL"
            print(f"  [{status}] {d['id']}: "
                  f"matched {len(d['matched'])}/{d['required']} required "
                  f"— {d['matched']}")
        if result["hallucinations"]:
            print(f"  [PENALTY] hallucinations: {result['hallucinations']}")
        print(f"{'='*60}\n")
        print(f"Token usage: {resp.usage}")

        assert result["adjusted_score"] >= 0.5, (
            f"Adjusted accuracy {result['adjusted_score']:.0%} below 50%. "
            f"Failed: {[d['id'] for d in result['details'] if not d['passed']]}. "
            f"Hallucinations: {result['hallucinations']}"
        )
    finally:
        await client.close()


@pytest.mark.skipif(not _NVME_NS_PATH.exists(), reason="SPDK source not found")
async def test_spdk_function_identification():
    """LLM should identify public API functions in nvme_ns.c."""
    source = _read_source(_NVME_NS_PATH)
    client = await _create_client()
    try:
        resp = await client.complete(
            [{"role": "user", "content": (
                "List all public API functions (non-static) in this file. "
                "Output only function names, one per line.\n\n"
                f"```c\n{source}\n```"
            )}],
            max_tokens=512,
            temperature=0.1,
        )
        expected_public = [
            "nvme_ns_set_identify_data",
            "spdk_nvme_ns_get_id",
            "spdk_nvme_ns_is_active",
            "spdk_nvme_ns_get_ctrlr",
            "spdk_nvme_ns_get_sector_size",
            "spdk_nvme_ns_get_extended_sector_size",
            "spdk_nvme_ns_get_num_sectors",
            "spdk_nvme_ns_get_size",
            "spdk_nvme_ns_get_flags",
            "spdk_nvme_ns_get_data",
            "spdk_nvme_ns_get_format_index",
            "nvme_ns_construct",
            "nvme_ns_destruct",
        ]
        expected_static = [
            "_nvme_ns_get_data",
            "nvme_ctrlr_identify_ns",
            "nvme_ns_find_id_desc",
        ]
        found_public = [fn for fn in expected_public if fn in resp.content]
        false_static = [fn for fn in expected_static if fn in resp.content]

        precision_penalty = len(false_static)
        recall = len(found_public) / len(expected_public)
        adjusted = max(0, (len(found_public) - precision_penalty)) / len(expected_public)

        print(f"\nFunction ID recall: {recall:.0%} "
              f"({len(found_public)}/{len(expected_public)})")
        print(f"  Found public: {found_public}")
        missing = set(expected_public) - set(found_public)
        if missing:
            print(f"  Missing: {missing}")
        if false_static:
            print(f"  [PENALTY] incorrectly listed static: {false_static}")
        print(f"  Adjusted score: {adjusted:.0%}")

        assert recall >= 0.6, (
            f"Function recall {recall:.0%} below 60%. Missing: {missing}"
        )
    finally:
        await client.close()


@pytest.mark.skipif(not _NVME_NS_PATH.exists(), reason="SPDK source not found")
async def test_spdk_architectural_understanding():
    """LLM should explain the relationship between namespace and controller."""
    source = _read_source(_NVME_NS_PATH)
    client = await _create_client()
    try:
        resp = await client.complete(
            [{"role": "user", "content": (
                "What is the relationship between namespace and controller "
                "in this code? Explain briefly.\n\n"
                f"```c\n{source}\n```"
            )}],
            max_tokens=512,
            temperature=0.3,
        )
        content_lower = resp.content.lower()
        checks = {
            "ns_belongs_to_ctrlr": any(w in content_lower for w in [
                "belongs to", "associated with", "attached to",
                "owned by", "ns->ctrlr", "pointer to",
            ]),
            "ctrlr_provides_capabilities": any(w in content_lower for w in [
                "capabilit", "feature", "oncs", "quirk",
                "controller data", "cdata",
            ]),
            "ns_has_own_properties": any(w in content_lower for w in [
                "sector size", "lba", "format", "metadata",
                "namespace data", "nsdata",
            ]),
            "lifecycle_dependency": any(w in content_lower for w in [
                "construct", "identify", "initialization",
                "active", "lifecycle",
            ]),
        }
        score = sum(checks.values()) / len(checks)
        print(f"\nArchitectural understanding: {score:.0%}")
        for name, passed in checks.items():
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}")

        assert score >= 0.5, (
            f"Architectural understanding {score:.0%} below 50%. "
            f"Failed: {[k for k, v in checks.items() if not v]}"
        )
    finally:
        await client.close()
