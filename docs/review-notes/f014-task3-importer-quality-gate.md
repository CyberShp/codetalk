---
feature_ids: [F014]
topics: [quality-gate, safe-zip, skill-importer, task-3]
doc_kind: quality-gate-report
created: 2026-08-04
---

# F014 Task 3 Safe Importer Quality Gate

## Scope

This gate covers Task 3 only: untrusted ZIP path validation, bounded and atomic
package import, source inventory, and logical `workflows/*.md` scenario split.
It is not a claim that F014 or Phase A is complete. Terminal Skill IDs and IR
remain Task 4 compiler responsibilities; filesystem Draft ownership remains
Task 5 store responsibility.

## Vision And Contract Check

The implementation was checked against the original refactor plan, the F014
spec and implementation plan, the approved Task 2 contracts, and the pinned
source inventory. It preserves these boundaries:

- one source tree is safely unpacked once; five logical Skill sources do not
  create five copied content authorities;
- scenario sources come from the explicit `workflows/*.md` structure, never
  from natural-language guesses;
- importer output retains source scenario identity but does not manufacture a
  terminal `skill.*` ID from a filename;
- UTF-8 names and bytes remain exact; traversal and ambiguous names are
  rejected, not repaired;
- no destination content exists until complete metadata validation, bounded
  reads, CRC checks, and digest calculation finish.

## Security Coverage

| Boundary | Evidence | Result |
|---|---|---|
| Traversal and platform paths | relative escape, absolute, drive, UNC/backslash, Windows ADS/device/superscript-device/trailing-alias, extension-space device alias, Win32-forbidden character, control-character, dot segment, empty, and NUL raw-name tests | Pass |
| ZIP entry type | symlink, special Unix type with and without trailing slash, encrypted-flag raw ZIP, and data-bearing directory tests | Pass |
| Name ambiguity | exact duplicate, casefold, NFC, NFC-plus-casefold, implicit ancestor file/directory conflict, implicit directory alias, ambiguous package roots, and wrapper-outside member tests | Pass |
| Resource limits | archive bytes, pre-ZipFile central-directory entry count, path depth, member/total path bytes, entry bytes, total bytes, compression ratio, zero compressed bytes, boolean/floating limit confusion, and non-finite limit tests for every limit field | Pass |
| Corruption and encoding | bad/truncated ZIP, CRC corruption, unsupported compression, malformed deflate/LZMA, and invalid UTF-8 central-directory tests | Pass |
| Filesystem authority | descriptor-bound source symlink rejection, nonblocking source descriptor open, destination symlink rejection, existing destination preservation, destination-parent replacement detection, and lexical macOS temp alias canonicalization | Pass |
| Failure atomicity | same-parent staging removed after failures; formal destination absent or unchanged | Pass |
| Provenance | archive digest and ordered per-file digest/size inventory derived from one private archive snapshot; source-path mutation after hashing cannot change extracted bytes | Pass |
| Scenario split | explicit `workflows/*.md` declarations produce logical sources; UTF-8 source identities are retained; empty/dot/control/whitespace source identities are rejected before write | Pass |
| Official source | pinned SHA, exact 37-path inventory, three Chinese template paths, and five structural scenario sources | Pass |

The final directory installation uses same-parent staging and `os.rename` after
an immediate destination-existence check. Python/macOS does not expose a
portable no-replace directory rename primitive. Task 5 must serialize imports
through the store lock; Task 3 still refuses any destination observed before
installation and never overwrites one in normal operation.

## Fresh Verification

Worktree: `/Volumes/Media/codetalk-skill-first-agent-runtime`

Branch/base: `codex/skill-first-agent-runtime`, based on `main@9e1434d9`.

```text
PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_schemas.py \
  backend/tests/test_skill_source_inventory.py \
  backend/tests/test_skill_package_importer.py
=> 252 passed, 2 skipped

CODETALKS_V24_ARCHIVE=/Users/shepard/Downloads/codetalks-fused-v2.4-zh.zip \
  PYTHONPATH=backend uv run --python 3.12 \
  --with-requirements backend/requirements.txt pytest -q \
  backend/tests/test_skill_schemas.py \
  backend/tests/test_skill_source_inventory.py \
  backend/tests/test_skill_package_importer.py
=> 254 passed, 0 skipped, 0 warnings
```

Additional checks:

- `git diff --check`: pass.
- scoped Python compilation: pass.
- `extractall`/bulk extraction search: no use.
- scoped secret, Redis `6399`, F012/F013, and legacy staged-analysis scans: no
  matches.
- Source archive mutation between digest and ZIP parsing is covered by a
  regression test that preserves size and mtime while changing the source path;
  import still uses the original private snapshot.
- root media/design artifact scan: no matches.
- no frontend or `.pen` design applies to this slice.
- the repository has no `scripts/check-fallback-layers.mjs`; manual inspection
  found no fallback chain in the two new modules.

## Review Remediation

The first independent Task 3 review returned six P2 findings. Each was
converted into a failing regression test before implementation:

| Finding | Red evidence | Green evidence |
|---|---|---|
| Source validation was not bound to the opened descriptor | symlink replacement after lexical validation imported the target | archive opening now uses descriptor-level no-follow validation before copying the private snapshot |
| Path collision checks missed combined NFC/casefold aliases and implicit ancestors | `é.md`/`É.md` and `A`/`a/b.md` variants were accepted or failed after writes | full paths and implicit ancestors use an NFC-plus-casefold key before extraction |
| ZIP decoding exceptions escaped the importer contract | unsupported method `99` and malformed deflate raised internal exceptions | `NotImplementedError` and `zlib.error` normalize to `invalid_archive` |
| Data-bearing directory entries were silently dropped | a directory with hidden bytes imported without inventory accounting | non-empty directory entries fail with `directory_payload` before write |
| Integer resource limits accepted non-finite values | `float("inf")` disabled entry/count/size caps | integer limit fields now require positive non-bool `int` values |
| Workflow files could produce empty/dot source identities | `workflows/.md`, `..md`, and `...md` imported as unusable source IDs | direct workflow declarations must match the source identity pattern before extraction |

The review open question about compressed archive-byte authority is closed in
Task 3: `max_archive_bytes` is enforced while copying the private snapshot,
before ZIP metadata parsing or extraction.

The second independent re-review returned five additional P2 findings. Each was
also converted into a failing regression test before implementation:

| Finding | Red evidence | Green evidence |
|---|---|---|
| Implicit directory canonical aliases were accepted | `A/x.md` plus `a/y.md` imported with divergent inventory/filesystem spelling | every implicit path prefix now participates in canonical collision checks |
| LZMA decoder failures escaped | corrupted `ZIP_LZMA` raised an internal decoder exception | `lzma.LZMAError` normalizes to `invalid_archive` |
| Source open could block on FIFO replacement | source descriptor open lacked nonblocking flags | source open uses `O_NONBLOCK` where available before file-type validation |
| Special Unix type with trailing slash bypassed type checks | socket entry named with `/` imported as a directory | Unix type is validated before filename syntax can classify a directory |
| Scenario ID regex was not a full match and was ASCII-only | `foo\n.md` imported; `根因.md` was rejected | source identity validation rejects whitespace/control/dot-only identities and retains UTF-8 stems |

The final independent review returned one P1 and two P2 findings. Each was
converted into a failing regression test before implementation:

| Finding | Red evidence | Green evidence |
|---|---|---|
| Deep paths caused unbounded implicit-prefix expansion | a many-segment member path was accepted until prefix processing | `max_path_segments` bounds path depth before prefix expansion |
| Entry count was enforced only after `ZipFile` construction | a low `max_entries` test failed if `ZipFile` was constructed | EOCD entry count is checked before `ZipFile` object creation |
| Windows filesystem aliases and ADS names were accepted | `base:stream`, `CON`, `AUX`, trailing-dot, and trailing-space segments were valid | path validation rejects Windows alternate streams, device names, and trailing alias components |

The follow-up independent re-review returned one P1 and two P2 findings. Each
was converted into a failing regression test before implementation:

| Finding | Red evidence | Green evidence |
|---|---|---|
| Segment count alone did not bound retained prefix strings | long component paths stayed within depth limits while growing cumulative prefix memory | member and total path-byte limits reject oversized path material before prefix expansion |
| Forged EOCD entry counts bypassed pre-`ZipFile` entry limits | central directory contained more entries than forged EOCD counts declared | central directory records are counted independently before `ZipFile` construction |
| Windows reserved device set was incomplete | `COM¹`, `LPT³`, `CONIN$`, and `CONOUT$` variants were accepted | Windows reserved-name checks cover those device aliases and extension variants |

The recurring open question about destination-parent trust is narrowed in Task
3: the importer records the resolved parent directory signature and rechecks it
before staging and before final rename. Task 5 still owns cooperative store
serialization, but archive submitters cannot silently redirect this importer to
a replaced parent during the import.

The next independent review returned two P2 findings. Each was converted into a
failing regression test before implementation:

| Finding | Red evidence | Green evidence |
|---|---|---|
| Spaces before reserved-device extensions were accepted | `CON .txt`, `AUX  .md`, `COM1 .log`, and `LPT³ .bin` imported as regular names | reserved-device detection now trims spaces and dots from the extension stem before matching |
| Control and Win32-forbidden characters were accepted | newline, tab, ESC, `<`, `"`, `|`, `?`, and `*` names imported on POSIX | path validation now rejects control characters and Win32-forbidden characters before preflight writes |

## Gate Decision

Task 3 author/integrator self-check is ready for independent security review.
This report is not approval and does not authorize Task 4 by itself.
