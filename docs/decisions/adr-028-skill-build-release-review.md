---
feature_ids: [F014]
topics: [adr, skill-build, release, review, digest, immutability]
doc_kind: architecture-decision-record
created: 2026-08-04
status: accepted
---

# ADR-028: Deterministic Skill Build, Review, and Release

## Context

Skill source files need ordinary local editing, but an execution and delivery
must remain reproducible.  A timestamped review result is evidence about a
build, not source content itself.  Combining review output with content identity
would make identical source bytes yield different Version identities; allowing
review to edit or publish silently would make the release boundary un-auditable.

## Decision

Skill source and release authority are deliberately separate.

- Draft content is mutable filesystem content under the Skill Project.  The
  filesystem is authoritative; UI and database records may index/rescan it but
  may not become a second content authority.
- A candidate build validates Draft content deterministically, compiles a
  terminal Skill IR, emits a deterministic ZIP and file-digest map, and derives
  a **content digest** from canonical release content.  Identical valid source
  bytes must produce the same IR, package, and content digest.
- Deterministic structural validation failures block review completion and
  publication.  Validation includes IDs, references, dependencies, artifact
  producers/consumers, paths, scripts, Judge declaration, and archive safety.
- A full Review is required before publication.  Review stores findings,
  provenance, optional proposed patches, and an immutable **review evidence
  digest**.  Review output never mutates a Draft and never publishes a Version.
- Applying a proposed patch is an explicit human decision against the Draft,
  followed by rescan, a new deterministic candidate build, and a new required
  full Review.
- Publication is a separate explicit command.  It atomically creates an
  immutable Skill Version containing the source package, unpacked files, IR,
  validation report, review records, content digest, review evidence digest,
  and a manifest linking both digests.

High-risk AI findings may be acknowledged under a recorded policy, but remain
visible.  They are not silently converted into deterministic structural errors
or erased from release evidence.

## Consequences

- Draft rescan, build, review, patch decision, and publish are distinct API and
  audit events with bounded responsibilities.
- No object-storage abstraction is introduced before a real second backend is
  selected.  The local filesystem plus existing metadata storage is sufficient
  for V1.
- A released path is immutable.  Any post-publication content change creates a
  new candidate and Version rather than altering historical Task or Attempt
  meaning.
- AI Review/product LLM evidence stores provider/model/session and requested,
  effective, and response-model provenance without credentials.  Review data
  contributes to the review evidence digest, never to the content digest.

## Non-Goals

- A remote object-storage backend or a second mutable content authority.
- Automatic review patch application, automatic publication, or review output
  that changes content identity.

## Alternatives Considered

- Include review evidence in the content digest: rejected because identical
  source would receive time- and provider-dependent identities.
- Publish on successful build or review: rejected because explicit human
  release control and immutable evidence would be lost.

## Affected Scope

Skill Project draft storage, deterministic build/compiler, Review records,
publication APIs, release manifests, and Task Version selection use this
boundary.  Existing metadata storage indexes filesystem content but does not
own it.

## Rollback

Disable new build, review, and publication entry points while retaining Drafts,
candidate packages, and immutable Versions as read-only evidence.  Restore the
previous `main` behavior from the migration backup; never alter released
packages or their manifests.

## Validation

Acceptance requires repeat-build digest equality, Draft external-edit/rescan
coverage, structural failure rejection, no-auto-apply/no-auto-publish tests,
immutable released paths, and a manifest that independently verifies content
and review evidence digests.
