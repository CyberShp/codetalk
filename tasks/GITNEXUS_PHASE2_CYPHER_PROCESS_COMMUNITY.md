---
feature_id: F001
name: "GitNexus Phase 2 — Cypher Query + Process/Community APIs"
depends_on: [GITNEXUS Phase 1 (search + embed — COMPLETE)]
parallel_safe: true
owner: sonnet
reviewer: gpt52
doc_kind: task
created: 2026-04-18
topics: [backend, frontend, gitnexus, proxy, intelligence-panel]
---

# GitNexus Phase 2: Cypher Query + Process/Community APIs

## Context

Phase 1 shipped search + embed proxy endpoints and the GraphSearch UI component.
Phase 2 adds **Cypher query proxy** and **Process/Community detail APIs** so the
frontend can fetch richer data when a user clicks a Process or Community node in
the graph viewer.

**Iron Law reminder**: Every endpoint is a pure HTTP proxy to GitNexus (:7100).
Zero analysis logic. Forward request → return response.

---

## Workstream A — Backend Proxy Endpoints

**File**: `backend/app/api/gitnexus_proxy.py` (currently 85 lines)

Add 5 new endpoints. All follow the same pattern as the existing `/search` and
`/file` endpoints — `httpx.AsyncClient`, forward params, raise `HTTPException`
on error.

### A1. `POST /api/gitnexus/query` — Cypher query proxy

```
Upstream: POST /api/query?repo=xxx  body: { "cypher": "MATCH ..." }
```

- Pydantic model `CypherRequest`: `cypher: str`, `repo: str | None = None`
- Forward `repo` as query param (if present), body as JSON
- Return upstream JSON as-is

### A2. `GET /api/gitnexus/processes` — List all processes

```
Upstream: GET /api/processes?repo=xxx
```

- Query param: `repo: str | None = None`
- Return upstream JSON as-is

### A3. `GET /api/gitnexus/process` — Single process detail

```
Upstream: GET /api/process?name=xxx&repo=yyy
```

- Query params: `name: str` (required), `repo: str | None = None`
- Return upstream JSON as-is

### A4. `GET /api/gitnexus/clusters` — List all clusters/communities

```
Upstream: GET /api/clusters?repo=xxx
```

- Query param: `repo: str | None = None`
- Return upstream JSON as-is

### A5. `GET /api/gitnexus/cluster` — Single cluster detail

```
Upstream: GET /api/cluster?name=xxx&repo=yyy
```

- Query params: `name: str` (required), `repo: str | None = None`
- Return upstream JSON as-is

### Error handling pattern (same as existing endpoints)

```python
try:
    async with httpx.AsyncClient(
        base_url=settings.gitnexus_base_url, timeout=30
    ) as client:
        resp = await client.get("/api/...", params=params)
        resp.raise_for_status()
        return resp.json()
except httpx.HTTPStatusError as exc:
    raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
except httpx.HTTPError as exc:
    raise HTTPException(status_code=502, detail=str(exc))
```

### Acceptance criteria (backend)

- [ ] File stays under 200 lines (currently 85 + ~80 new = ~165)
- [ ] Module docstring updated to list new endpoints
- [ ] All 5 endpoints are pure proxy — no data transformation
- [ ] Each endpoint has a one-line docstring
- [ ] `tsc` not affected (Python only)

---

## Workstream B — Frontend API Client

**File**: `frontend/src/lib/api.ts` — `gitnexus` namespace (lines 244-272)

Add 5 new methods under `api.gitnexus`:

```typescript
// B1: Cypher query
query: (cypher: string, repo?: string) =>
  request<{ results: unknown[] }>("/api/gitnexus/query", {
    method: "POST",
    body: JSON.stringify({ cypher, repo }),
  }),

// B2: List processes
processes: (repo?: string) => {
  const qs = new URLSearchParams();
  if (repo) qs.set("repo", repo);
  const q = qs.toString();
  return request<{ processes: unknown[] }>(`/api/gitnexus/processes${q ? `?${q}` : ""}`);
},

// B3: Single process detail
process: (name: string, repo?: string) => {
  const qs = new URLSearchParams({ name });
  if (repo) qs.set("repo", repo);
  return request<{ name: string; steps: unknown[]; [key: string]: unknown }>(
    `/api/gitnexus/process?${qs}`
  );
},

// B4: List clusters
clusters: (repo?: string) => {
  const qs = new URLSearchParams();
  if (repo) qs.set("repo", repo);
  const q = qs.toString();
  return request<{ clusters: unknown[] }>(`/api/gitnexus/clusters${q ? `?${q}` : ""}`);
},

// B5: Single cluster detail
cluster: (name: string, repo?: string) => {
  const qs = new URLSearchParams({ name });
  if (repo) qs.set("repo", repo);
  return request<{ name: string; members: unknown[]; cohesion?: number; [key: string]: unknown }>(
    `/api/gitnexus/cluster?${qs}`
  );
},
```

### Notes

- Response types use `unknown[]` because GitNexus response shapes may evolve.
  The IntelligencePanel will cast/extract what it needs.
- Follow existing patterns exactly (`request<T>`, `URLSearchParams` for GET).

### Acceptance criteria (API client)

- [ ] 5 new methods added under `api.gitnexus`
- [ ] No new type imports needed (uses inline types)
- [ ] `tsc` passes cleanly

---

## Workstream C — IntelligencePanel Enhancement

**File**: `frontend/src/components/ui/IntelligencePanel.tsx` (currently 253 lines)

### C1. ProcessView — call `/api/gitnexus/process`

Current behavior: Resolves steps from `node.steps` + `nodeMap` (graph data only).

New behavior:
1. On mount (or when `node.id` changes), call `api.gitnexus.process(node.properties.name, repo)`.
2. If the API returns richer step data, use it instead of `node.steps`.
3. Fallback: if API call fails (404, 502, network), silently fall back to
   existing graph-extracted data. No error shown to user.
4. Show a subtle loading state while fetching (pulse on step area, not full skeleton).

Implementation approach:
- Add `useEffect` + `useState` for API data
- Merge API response into existing view
- `repo` prop needs to be threaded through from parent (see below)

### C2. CommunityView — call `/api/gitnexus/cluster`

Current behavior: Finds members by filtering `MEMBER_OF` edges.

New behavior:
1. On mount, call `api.gitnexus.cluster(node.properties.name, repo)`.
2. If API returns members list, use it (may include members not in the current
   graph viewport).
3. Fallback: same as ProcessView — silently use edge-extracted members.
4. API may return extra fields (cohesion, description, member roles) — render
   any additional data that comes back.

### C3. Props change — add `repo`

The parent component (`page.tsx`) needs to pass `repo` (repository name) down to
IntelligencePanel so it can make GitNexus API calls.

Current props: `{ node, nodeMap, edges, onNodeClick }`  
New props: `{ node, nodeMap, edges, onNodeClick, repo?: string }`

Update the call site in `page.tsx` to pass the repo name.

### Acceptance criteria (IntelligencePanel)

- [ ] ProcessView calls `api.gitnexus.process()` on node selection
- [ ] CommunityView calls `api.gitnexus.cluster()` on node selection
- [ ] Silent fallback to graph data if API fails
- [ ] `repo` prop added and threaded from page.tsx
- [ ] File stays under 350 lines (SOP gate)
- [ ] `tsc` passes cleanly
- [ ] No visual regression — panel looks identical when API returns same data

---

## SOP Compliance Checklist

- [ ] All proxy endpoints are pure HTTP forwarding (Iron Law)
- [ ] No file exceeds 350 lines (except page.tsx)
- [ ] `tsc` clean
- [ ] No new dependencies added
- [ ] Module docstrings updated

## Files Modified (complete list)

1. `backend/app/api/gitnexus_proxy.py` — 5 new endpoints
2. `frontend/src/lib/api.ts` — 5 new client methods
3. `frontend/src/components/ui/IntelligencePanel.tsx` — API calls + repo prop
4. `frontend/src/app/(app)/tasks/[id]/page.tsx` — pass repo to IntelligencePanel
