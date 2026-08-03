---
feature_ids: [F012]
topics: [quality-evaluation, corpus, truth-isolation, domain-audit]
doc_kind: review-note
created: 2026-08-03
---

# F012 P5 Independent Corpus-Domain Audit

## Scope and independence

- **Auditor**: F012 P5 independent Domain Corpus Auditor.
- **Scope**: AC7 and the truth-isolation portions of AC1/AC10 only.
- **Read boundary**: F012 feature, ADR-025, implementation plan, registry/cases,
  loader/runner/generator/sandbox code, tests, and pinned source trees. No other
  axis audit note was read.
- **Write boundary**: this file only. No implementation, fixture, benchmark, or
  source-tree content was changed by this audit.
- **Snapshot history**: the initial audit caught in-flight depth-truth edits. The
  final re-review below was run after the package statements and hashes settled;
  all final commands use that later stable snapshot.

## Identity and domain matrix

`resolve_registered_corpus()` accepted all 12 clean external source trees. Each
row below records the canonical GitHub origin and the full pinned commit/tree
pair actually verified by the loader. Registry execution metadata for every row
is `case_allowlist_only`, `loader_execution=forbidden`, and `network=disabled`.
Every currently registered case declares Tier `S`; each represented domain has a
critical static obligation in its committed truth package.

| Domain selector | Project | Canonical origin | Commit | Tree | License | P5 disposition |
| --- | --- | --- | --- | --- | --- | --- |
| storage | SPDK | `https://github.com/spdk/spdk.git` | `d64c4fa89233397460e2e4ff55a1c69b8e498598` | `b8c41cac12ca9c9cb34a8da6e028d35f826f581b` | BSD-3-Clause | P5 accepted; baseline pending separately. |
| storage | FEMU | `https://github.com/MoatLab/FEMU.git` | `b130c614afbc6e77f88e272533e9d71f8509e234` | `012c1d0277ac77a77f90bcb480bb99538382f768` | GPL-2.0 with mixed file licenses | P5 accepted; baseline pending separately. |
| storage | nvme-csd | `https://github.com/rick-heig/nvme_csd.git` | `d906b6a29e559a3d613a1eccf3712611587311ba` | `c90d9ef4c47b2c1c7551ef481d0a8978e3503940` | NOASSERTION, repository-level absence explicitly recorded | P5 accepted; baseline pending separately. |
| storage | Open-CAS | `https://github.com/Open-CAS/open-cas-linux.git` | `f1befa8dddf810733e720dec07c71de892951e39` | `509e5c7987cd3c83aa213ac23b715a9180d65a6b` | BSD-3-Clause | P5 accepted; baseline pending separately. |
| bmc | phosphor-nvme | `https://github.com/openbmc/phosphor-nvme.git` | `5ef51383d77fc32f5d5d314e70f860126de623e7` | `18fbb0cd8c9a0dd93bbeb54b8f19474a53b7c354` | Apache-2.0 | P5 accepted; baseline pending separately. |
| bmc | phosphor-state-manager | `https://github.com/openbmc/phosphor-state-manager.git` | `3f6517cbce44f84f9cea95f3f72b4f6401a52d49` | `4fbec101d4be874fed853e0f344d174f977450a0` | Apache-2.0 | P5 accepted; baseline pending separately. |
| bmc | bmcweb | `https://github.com/openbmc/bmcweb.git` | `9e59f0a176aac9dfa7f029370ea03c25a088d9a2` | `4e4e40cb4850fb110375ead0c1cd6d5b05425e05` | Apache-2.0 | P5 accepted; baseline pending separately. |
| kv-cache | LMCache | `https://github.com/LMCache/LMCache.git` | `f625b9733ad38c6b1bb3ba3d5083998ab5307ffb` | `c970140ccbe796aec9a25a915ea62390223100ec` | Apache-2.0 | P5 accepted; baseline pending separately. |
| kv-cache | Mooncake | `https://github.com/kvcache-ai/Mooncake.git` | `131d6addae64c31b340f1909350049eb41fcb790` | `0b1a5c2baf5b98ebe1d44f09dd03a256aaa31290` | Apache-2.0 | P5 accepted; baseline pending separately. |
| rdma | rdma-core | `https://github.com/linux-rdma/rdma-core.git` | `d45834e0fe3ff1248e40d995f2f51c51739e6f1c` | `e50a1a4f7eeb90608bbd7dc8042bd5b53dce0eff` | GPL-2.0-or-later OR LGPL-2.1-or-later | P5 accepted; baseline pending separately. |
| rdma, roce | UCX | `https://github.com/openucx/ucx.git` | `1ce08f6ed89caa0bc2dcef5c2e9ad837455da168` | `62435473638ce3f1f392d7e4fa43333f759e7f4b` | BSD-3-Clause | P5 accepted; baseline pending separately. |
| rdma, roce | perftest | `https://github.com/linux-rdma/perftest.git` | `00b55b6660d0170dabe2c1b49193e8fbe265086e` | `0fa1b3385f5193d09915c247d1bb7266efa5c7aa` | GPL-2.0 | P5 accepted; baseline pending separately. |

### Domain and execution observations

- Selector execution returned storage: SPDK/FEMU/nvme-csd/Open-CAS; bmc:
  phosphor-nvme/phosphor-state-manager/bmcweb; kv-cache: LMCache/Mooncake;
  rdma: rdma-core/UCX/perftest; roce: **UCX/perftest only**. `rdma-core` is
  intentionally absent from `--domain roce`, so generic RDMA is not being
  counted as RoCE.
- Project metadata declares S/E availability for the applicable repositories and
  H capability for Mooncake, rdma-core, UCX, and perftest. This initial corpus
  contains only S cases. The case oracle records `L3_NOT_RUN` or an explicit
  static-only limitation where execution is unavailable; no S evidence is
  promoted to an E/H result. No registered E/H case currently exists, so there
  is no E/H environment claim to accept.
- All 12 external source worktrees were clean during the identity check.

## Truth-package evidence review

The truth packages contain 57 gold claims and 109 independent universe items.
I extracted 604 distinct `source://`/`test://`/`oracle://` references from all
four truth files per case. The runner's own normalizer resolved all 592
source/test references to an existing path and in-range line interval inside the
matching pinned clean source tree; the remaining 12 `oracle://` records are
explicit execution-status records, not source assertions. All four descriptor
SHA-256 values per case also matched their files. Gold, universe, and depth
truth therefore have concrete, digest-bound source anchors.

The intended generation boundary is also structurally separated: the generator
receives only `case_id`, pinned `source_dir`, output location, model, and mode;
the runner opens the four truth files only after generated artifacts exist.
The macOS benchmark sandbox test verifies an absolute read of a truth file is
denied. The current intended path is accepted for truth separation.

## Findings

### Closed on final re-review: Hash-bound truth packages were not integral

At the audit snapshot, `load_quality_case()` rejected eight registered cases
because a truth file no longer matched the SHA-256 declared by its `case.json`:

- `bmcweb`: `critical_chains.json`
- `femu`: `execution_oracles.json`
- `lmcache`: `critical_chains.json`
- `mooncake`: `critical_chains.json`
- `nvme-csd`: `execution_oracles.json`
- `perftest`: `critical_chains.json`
- `phosphor-nvme`: `critical_chains.json`
- `phosphor-state-manager`: `critical_chains.json`

This was correctly fail-closed. On final re-review, all 12 cases loaded, every
one of the 48 truth descriptors matched its current file, and
`load_quality_baseline_corpus()` produced exactly 12 S identities. The registry,
isolation, and mutation suites are green. This P1 is closed.

### Closed on final re-review: Six critical-chain packages were not semantically judgeable

`tests/test_quality_corpus_mutations.py` requires a non-empty `statement` on
every node, edge, and disconfirming check. At snapshot, FEMU (26), nvme-csd
(26), Open-CAS (26), rdma-core (27), SPDK (26), and UCX (27) still had that many
missing statements. These are not cosmetic fields: strict depth parsing rejects
them, so their causal semantics cannot be independently evaluated. The later
hash mismatch in already-edited packages was consistent with in-flight repair,
not evidence of a completed repair at that time.

On final re-review, all 12 packages had zero missing node, edge, or
disconfirming-check statements. Each critical package's declared
`evidence_catalog_sha256` also matched a fresh digest of its execution catalog,
and semantic requests materialized against the pinned sources. This P1 is
closed.

### Closed on re-review: Explicit file-path selection admits an unregistered valid case

**Original finding.** `_select_case_paths()` in
`backend/app/services/quality_benchmark_runner.py` returns any existing path
given to `--case` before it discovers the registered `projects/*/*/case.json`
set. `load_quality_case()` validates project identity and truth hashes but does
not require that case file to be beneath the registry's projects root or to be
the registered case for that project. Consequently, a valid stale/duplicate
case copy for a registered project can be evaluated as an independent benchmark
when passed by path. That breaks the registry as the authoritative corpus and
means a future unregistered duplicate can influence runner results.

**Re-review result: closed.** The selector now resolves every discovered case
under the registry's `projects` root and rejects a direct file path unless its
resolved path is a member of that discovered set. The new
`test_cli_rejects_external_copy_of_registered_case` is RED-to-GREEN coverage:
the formerly accepted copied `case.json` now raises `ValueError: benchmark
selector must identify a registered case`. A real registered SPDK path remains
accepted, and all domain selectors preserve their expected case sets.

### Ruled out: Current empty stale directories change normal discovery

The following unregistered directories currently contain no files, especially
no `case.json` or truth artifact:

- `lmcache-local-cpu-pinned-eviction-001`
- `mooncake-store-put-commit-readiness-001`
- `phosphor-state-manager-hypervisor-boot-progress-001`

The normal glob finds exactly 12 `projects/*/*/case.json` files, and selecting
one of those stale names by case ID fails with `ValueError: benchmark selector
did not match any cases`. They do not affect current domain or `--all`
discovery. The formerly separate explicit-path admission defect is closed by
the re-review above.

### Ruled out: RDMA is silently counted as RoCE

The actual selector result for `roce` contains UCX and perftest only. `rdma-core`
remains available for generic RDMA and the combined historical `rdma-roce`
baseline stratum, but is not represented as an explicit RoCE case.

## Commands and exact results

```bash
cd /Volumes/Media/codetalk-quality-eval-baseline/backend
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_quality_benchmark_corpus.py
# 52 passed in 7.37s

PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_quality_truth_isolation.py
# 43 passed in 2.07s

PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_quality_corpus_mutations.py
# 47 passed in 2.03s

PYTHONPATH=. CODETALK_QUALITY_CORPUS_ROOT=/Volumes/Media/codetalk-quality-corpus/sources \
  .venv/bin/python - <<'PY'
from pathlib import Path
from app.services.quality_benchmark_corpus import load_quality_registry, resolve_registered_corpus
root = Path('..').resolve()
registry = load_quality_registry(root / 'benchmarks/quality/registry.json')
print(len(resolve_registered_corpus(registry)))
PY
# 12; every origin, 40-char commit, and 40-char tree hash matched.
# load_quality_baseline_corpus() also returned 12 S identities.
# registry SHA-256: 00e5796f34c8c694581ce05b6b1d7acd354cfe56c3f0c58f158318bd7020934d
# corpus SHA-256: fe3170bd0d3c90b4b9545d2a7fe68e97384ac0d17f02bd712eb85be6fd6e16e3

PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_quality_benchmark_runner.py \
  -k 'cli_selectors_are_mutually_exclusive or cli_uses_explicit_independent_judge_model_default or cli_network_domain_selectors_distinguish_explicit_roce_cases or cli_rejects_external_copy_of_registered_case or cli_rejects_precomputed_file_that_would_bypass_selector_semantics'
# 7 passed, 32 deselected in 0.11s

PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_quality_benchmark_runner.py \
  -k 'cli_network_domain_selectors_distinguish_explicit_roce_cases or cli_rejects_external_copy_of_registered_case'
# 4 passed, 35 deselected in 0.19s
```

The author's reported `207 focused` and `499 full quality` results were not used
as substitutes for this reviewer's required independent commands.

## Required re-review evidence

1. **Completed:** every registered case loads with its four descriptor SHA-256
   values intact, and `load_quality_baseline_corpus()` yields exactly 12 S
   identities.
2. **Completed:** all nodes, edges, and disconfirming checks have source-backed
   semantic statements; the complete mutation suite passes.
3. **Completed on re-review:** a valid external copied case is rejected, while
   a registered path and every domain selector still work (`7 passed`).
4. **Completed:** the three required corpus suites were rerun on the stable
   package snapshot.

## Baseline status

**Pending outside P5.** The registry-derived 12-case identity and corpus digest
are reproducible, but that is not the P7 model-run baseline, calibration,
threshold freeze, or regression matrix. This P5 ACCEPT does not approve or
imply completion of those later baseline activities.

## Verdict

**ACCEPT.** No unresolved P0/P1 finding remains in the P5 corpus scope. All
three original P1 findings are closed: package hashes and internal catalog
digests match, all critical-chain semantics are judgeable and source-backed,
and external copied cases are rejected without regressing registered/domain
selection. P7 baseline execution and calibration remain explicitly pending.
