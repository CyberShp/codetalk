"""AI generation accuracy evaluation using real SPDK source code.

Reads actual C source from SPDK (Storage Performance Development Kit)
and evaluates LLM analysis quality against hand-crafted ground truth.

Two prompt strategies are tested:
  - Baseline: bare "Analyze this C source code" (no guidance)
  - Enhanced: system prompt with domain expertise + structured output request

Requires DEEPSEEK_API_KEY environment variable.
"""

import os
import re
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

_SYSTEM_PROMPT = (
    "You are a senior storage systems engineer specializing in NVMe drivers "
    "and kernel-adjacent C code. When analyzing source code, always cover:\n"
    "- Error return paths and errno conventions\n"
    "- Memory allocation patterns (standard libc vs framework-specific allocators)\n"
    "- API surface visibility (static vs non-static, naming conventions)\n"
    "- Hardware quirk/workaround mechanisms\n"
    "- Data structure relationships and lifecycle management\n"
    "Be precise. Distinguish between public API functions and internal helpers."
)


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

_FALSE_CLAIMS = [
    "user.?space",
    "file.?system",
    "network.?stack",
    "tcp|udp|socket",
    "encryption|crypto",
    "thread.?pool",
]


def _score_response(text: str, truths: list[dict], false_claims: list[str]) -> dict:
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


def _print_result(label: str, result: dict, usage: dict | None = None) -> None:
    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"  Raw:      {result['raw_score']:.0%} ({result['passed']}/{result['total']})")
    print(f"  Adjusted: {result['adjusted_score']:.0%} (penalty: -{result['penalty']})")
    print(f"{'='*60}")
    for d in result["details"]:
        status = "PASS" if d["passed"] else "FAIL"
        print(f"  [{status}] {d['id']}: "
              f"{len(d['matched'])}/{d['required']} — {d['matched']}")
    if result["hallucinations"]:
        print(f"  [PENALTY] {result['hallucinations']}")
    if usage:
        print(f"  Tokens: {usage}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Test 1: Baseline vs Enhanced — side-by-side comparison
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _NVME_NS_PATH.exists(), reason="SPDK source not found")
async def test_spdk_baseline_vs_enhanced():
    """Compare bare prompt vs system-prompt-enhanced analysis."""
    source = _read_source(_NVME_NS_PATH)
    client = await _create_client()
    try:
        baseline_resp = await client.complete(
            [{"role": "user", "content": f"Analyze this C source code.\n\n```c\n{source}\n```"}],
            max_tokens=2048,
            temperature=0.3,
        )
        baseline = _score_response(baseline_resp.content, _GROUND_TRUTH, _FALSE_CLAIMS)
        _print_result("BASELINE (bare prompt)", baseline, baseline_resp.usage)

        enhanced_resp = await client.complete(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": (
                    "Analyze this C source file. Structure your response with these sections:\n"
                    "1. Overview (project, domain, purpose)\n"
                    "2. Data structures and relationships\n"
                    "3. Public API vs internal functions\n"
                    "4. Error handling patterns\n"
                    "5. Memory management\n"
                    "6. Notable patterns (quirks, flags, completion model)\n\n"
                    f"```c\n{source}\n```"
                )},
            ],
            max_tokens=2048,
            temperature=0.3,
        )
        enhanced = _score_response(enhanced_resp.content, _GROUND_TRUTH, _FALSE_CLAIMS)
        _print_result("ENHANCED (system prompt + structure)", enhanced, enhanced_resp.usage)

        delta = enhanced["adjusted_score"] - baseline["adjusted_score"]
        print(f"\n>>> DELTA: {delta:+.0%} "
              f"(baseline {baseline['adjusted_score']:.0%} → "
              f"enhanced {enhanced['adjusted_score']:.0%})")

        baseline_fails = {d["id"] for d in baseline["details"] if not d["passed"]}
        enhanced_fails = {d["id"] for d in enhanced["details"] if not d["passed"]}
        fixed = baseline_fails - enhanced_fails
        regressed = enhanced_fails - baseline_fails
        if fixed:
            print(f"    Fixed by enhancement: {fixed}")
        if regressed:
            print(f"    Regressed: {regressed}")

        assert enhanced["adjusted_score"] >= 0.5, (
            f"Enhanced accuracy {enhanced['adjusted_score']:.0%} below 50%"
        )
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Test 2: Function identification with static/public distinction
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _NVME_NS_PATH.exists(), reason="SPDK source not found")
async def test_spdk_function_identification():
    """Enhanced function identification with static/public distinction."""
    source = _read_source(_NVME_NS_PATH)
    client = await _create_client()
    try:
        resp = await client.complete(
            [
                {"role": "system", "content": (
                    "You are a C code analyzer. 'static' and 'static inline' functions "
                    "are file-internal. Functions without 'static' qualifier are public API. "
                    "Pay attention to the 'static' keyword at the start of function definitions."
                )},
                {"role": "user", "content": (
                    "List all PUBLIC (non-static) functions defined in this file. "
                    "Do NOT include static or static inline functions. "
                    "Output only function names, one per line.\n\n"
                    f"```c\n{source}\n```"
                )},
            ],
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
        trap_static = [
            "_nvme_ns_get_data",
            "nvme_ctrlr_identify_ns",
            "nvme_ctrlr_identify_ns_zns_specific",
            "nvme_ctrlr_identify_ns_nvm_specific",
            "nvme_ctrlr_identify_ns_kv_specific",
            "nvme_ctrlr_identify_ns_iocs_specific",
            "nvme_ctrlr_identify_id_desc",
            "nvme_ns_find_id_desc",
            "nvme_ns_get_csi",
        ]
        found_public = [fn for fn in expected_public if fn in resp.content]
        false_positives = [fn for fn in trap_static if fn in resp.content]

        recall = len(found_public) / len(expected_public)
        precision_penalty = len(false_positives)
        adjusted = max(0, (len(found_public) - precision_penalty)) / len(expected_public)

        print(f"\nFunction ID recall: {recall:.0%} "
              f"({len(found_public)}/{len(expected_public)})")
        print(f"  Found: {found_public}")
        missing = set(expected_public) - set(found_public)
        if missing:
            print(f"  Missing: {missing}")
        if false_positives:
            print(f"  [PENALTY] static functions leaked: {false_positives} (-{precision_penalty})")
        print(f"  Adjusted: {adjusted:.0%}")

        assert recall >= 0.6, (
            f"Function recall {recall:.0%} below 60%. Missing: {missing}"
        )
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Test 3: Architectural understanding
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _NVME_NS_PATH.exists(), reason="SPDK source not found")
async def test_spdk_architectural_understanding():
    """LLM should explain namespace-controller relationship."""
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


# ---------------------------------------------------------------------------
# Test 4: Robustness analysis — boundary values, fragile variables,
#          exception branches, exception propagation
# ---------------------------------------------------------------------------

_ROBUSTNESS_ISSUES = [
    {
        "id": "silent_error_swallow",
        "category": "exception_propagation",
        "description": "nvme_ctrlr_identify_ns returns 0 when admin queue times out, swallowing error",
        "keywords": [
            "silent", "swallow", "timeout", "return 0",
            "error.*lost", "error.*ignored", "error.*discard",
            "completion.*ignored", "status.*not.*check",
        ],
        "min_matches": 1,
        "severity": "high",
    },
    {
        "id": "assert_in_release",
        "category": "exception_branch",
        "description": "assert(0) in default case of iocs_specific is no-op in release builds",
        "keywords": [
            "assert.*0", "assert.*release", "assert.*ndebug",
            "no.?op", "unreachable", "default.*case.*assert",
            "assert.*production", "assert.*disabled",
        ],
        "min_matches": 1,
        "severity": "high",
    },
    {
        "id": "id_desc_ambiguous_null",
        "category": "exception_branch",
        "description": "nvme_ns_find_id_desc returns NULL for both not-found and invalid descriptor",
        "keywords": [
            "null.*ambig", "null.*both", "null.*distinguish",
            "find_id_desc.*null", "not.?found.*invalid",
            "sentinel", "error.*same.*return",
        ],
        "min_matches": 1,
        "severity": "medium",
    },
    {
        "id": "fallthrough_nvm_default",
        "category": "exception_branch",
        "description": "NVM case falls through to default/assert(0) when elbas is false",
        "keywords": [
            "fall.?through", "nvm.*default", "elbas.*false",
            "nvm.*case.*assert", "implicit.*fall",
        ],
        "min_matches": 1,
        "severity": "medium",
    },
    {
        "id": "format_index_no_bounds",
        "category": "boundary_value",
        "description": "spdk_nvme_ns_get_format_index returns index without bounds check",
        "keywords": [
            "bounds", "out.?of.?range", "format.*index.*check",
            "nlbaf", "array.*bound", "index.*valid",
            "overflow.*index", "unchecked.*index",
        ],
        "min_matches": 1,
        "severity": "high",
    },
    {
        "id": "sector_size_shift_overflow",
        "category": "fragile_variable",
        "description": "1 << lbads can overflow if lbads >= 32",
        "keywords": [
            "shift.*overflow", "lbads.*32", "1.*<<.*overflow",
            "undefined.*behavior", "shift.*width",
            "lbads.*large", "sector.*overflow",
        ],
        "min_matches": 1,
        "severity": "high",
    },
    {
        "id": "null_nsdata_nvm",
        "category": "fragile_variable",
        "description": "nsdata_nvm used without consistent NULL check across functions",
        "keywords": [
            "nsdata_nvm.*null", "null.*check.*missing",
            "null.*dereference", "nsdata_nvm.*not.*check",
            "inconsistent.*null", "optional.*null",
        ],
        "min_matches": 1,
        "severity": "medium",
    },
]

_ROBUSTNESS_SYSTEM_PROMPT = (
    "You are a senior C code auditor specializing in systems software reliability. "
    "Review the code for these specific categories of issues:\n"
    "1. **Exception propagation**: errors silently swallowed, status codes ignored\n"
    "2. **Exception branches**: unreachable code, assert() in release, ambiguous return values\n"
    "3. **Boundary values**: unchecked array indices, integer overflow, shift overflow\n"
    "4. **Fragile variables**: pointers used without NULL checks, inconsistent validation\n\n"
    "For each issue found, state: category, location (function name), severity (high/medium/low), "
    "and a brief explanation of why it is problematic."
)


@pytest.mark.skipif(not _NVME_NS_PATH.exists(), reason="SPDK source not found")
async def test_spdk_robustness_analysis():
    """LLM should identify real robustness issues in SPDK nvme_ns.c."""
    source = _read_source(_NVME_NS_PATH)
    client = await _create_client()
    try:
        resp = await client.complete(
            [
                {"role": "system", "content": _ROBUSTNESS_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    "Audit this C source file for robustness issues. "
                    "Focus on: silent error swallowing, assert() misuse, "
                    "unchecked indices, shift overflow, NULL dereference risks, "
                    "and ambiguous return values.\n\n"
                    f"```c\n{source}\n```"
                )},
            ],
            max_tokens=2048,
            temperature=0.3,
        )
        text_lower = resp.content.lower()

        results = []
        high_found = 0
        for issue in _ROBUSTNESS_ISSUES:
            matched = []
            for kw in issue["keywords"]:
                if ".*" in kw or "|" in kw or ".?" in kw:
                    if re.search(kw, text_lower):
                        matched.append(kw)
                elif kw.lower() in text_lower:
                    matched.append(kw)
            passed = len(matched) >= issue["min_matches"]
            if passed and issue["severity"] == "high":
                high_found += 1
            results.append({
                "id": issue["id"],
                "category": issue["category"],
                "severity": issue["severity"],
                "passed": passed,
                "matched": matched,
            })

        total = len(results)
        passed_count = sum(1 for r in results if r["passed"])
        score = passed_count / total

        print(f"\n{'='*60}")
        print(f"ROBUSTNESS ANALYSIS")
        print(f"  Score: {score:.0%} ({passed_count}/{total} issues detected)")
        print(f"  High-severity found: {high_found}")
        print(f"{'='*60}")

        by_cat = {}
        for r in results:
            by_cat.setdefault(r["category"], []).append(r)
        for cat, items in by_cat.items():
            print(f"\n  [{cat}]")
            for r in items:
                status = "PASS" if r["passed"] else "MISS"
                print(f"    [{status}] {r['id']} ({r['severity']}): {r['matched']}")

        if resp.usage:
            print(f"\n  Tokens: {resp.usage}")
        print(f"{'='*60}")

        assert high_found >= 1, (
            f"LLM found {high_found} high-severity issues, expected at least 1. "
            f"Missed: {[r['id'] for r in results if not r['passed'] and r['severity'] == 'high']}"
        )
    finally:
        await client.close()
