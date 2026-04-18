---
feature_id: F001
name: "GitNexus Phase 5 — grep + route_map"
depends_on: [GITNEXUS Phase 3 (Impact Analysis — COMPLETE)]
parallel_safe: true
owner: sonnet
reviewer: gpt52
doc_kind: task
created: 2026-04-18
topics: [backend, frontend, gitnexus, proxy, grep, route-map]
---

# GitNexus Phase 5: grep + route_map

## Context

Phase 1–3 covered search, Cypher, process/community, and impact analysis.
Phase 4 bridges graph and chat. Phase 5 adds two remaining GitNexus HTTP
capabilities: **regex grep** and **API route mapping**.

These are independent of Phase 4 and can be implemented in parallel.

**Iron Law reminder**: Pure HTTP proxy. No analysis logic.

---

## Workstream A — Backend: grep + route_map Proxy

**File**: `backend/app/api/gitnexus_proxy.py` (currently 261 lines)

### A1. `GET /api/gitnexus/grep` — regex code search

```python
@router.get("/grep")
async def grep_code(
    pattern: str = Query(..., description="Regex pattern"),
    repo: str | None = Query(None),
    glob: str | None = Query(None, description="File glob filter, e.g. *.py"),
):
    """Proxy to GitNexus GET /api/grep — regex code search."""
    params: dict[str, str] = {"pattern": pattern}
    if repo:
        params["repo"] = repo
    if glob:
        params["glob"] = glob

    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=30
        ) as client:
            resp = await client.get("/api/grep", params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code, detail=exc.response.text
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
```

### A2. `GET /api/gitnexus/repos` — list indexed repos

```python
@router.get("/repos")
async def list_repos(repo: str | None = Query(None)):
    """Proxy to GitNexus GET /api/repos — list indexed repositories."""
    params: dict[str, str] = {}
    if repo:
        params["repo"] = repo

    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=30
        ) as client:
            resp = await client.get("/api/repos", params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code, detail=exc.response.text
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
```

### A3. Update module docstring

Add `- Regex code search (/grep)` and `- List indexed repos (/repos)` to the
module docstring's endpoint list.

### Acceptance criteria (backend)

- [ ] `GET /api/gitnexus/grep` proxies to GitNexus `/api/grep`
- [ ] `GET /api/gitnexus/repos` proxies to GitNexus `/api/repos`
- [ ] Error handling follows existing pattern
- [ ] File stays under 320 lines (~261 + ~50 new)
- [ ] Module docstring updated
- [ ] No analysis logic — pure proxy

---

## Workstream B — Frontend API Client

**File**: `frontend/src/lib/api.ts` — `gitnexus` namespace

### B1. grep client method

```typescript
grep: (pattern: string, repo?: string, glob?: string) => {
  const qs = new URLSearchParams({ pattern });
  if (repo) qs.set("repo", repo);
  if (glob) qs.set("glob", glob);
  return request<{
    matches: Array<{
      file: string;
      line: number;
      content: string;
      context?: string[];
    }>;
  }>(`/api/gitnexus/grep?${qs}`);
},
```

### B2. repos client method

```typescript
repos: (repo?: string) => {
  const qs = new URLSearchParams();
  if (repo) qs.set("repo", repo);
  const q = qs.toString();
  return request<{ repos: unknown[] }>(`/api/gitnexus/repos${q ? `?${q}` : ""}`);
},
```

### Acceptance criteria (API client)

- [ ] `api.gitnexus.grep()` and `api.gitnexus.repos()` added
- [ ] Type-safe request/response
- [ ] `tsc` passes

---

## Workstream C — Frontend: grep UI in Search Tab

**File**: `frontend/src/app/(app)/tasks/[id]/page.tsx`

The existing search tab has a search box that calls Zoekt. Add a **mode
toggle** to switch between Zoekt (full-text) and GitNexus grep (regex).

### C1. Search mode state

```typescript
const [searchMode, setSearchMode] = useState<"zoekt" | "grep">("zoekt");
```

### C2. Mode toggle UI

Add a small segmented control next to the search input:

```typescript
<div className="flex bg-surface-container-low/50 p-0.5 rounded-md border border-outline-variant/10">
  <button
    onClick={() => setSearchMode("zoekt")}
    className={`px-3 py-1 text-[10px] font-bold uppercase tracking-wider rounded ${
      searchMode === "zoekt"
        ? "bg-surface-container-high text-primary"
        : "text-on-surface-variant"
    }`}
  >
    全文
  </button>
  <button
    onClick={() => setSearchMode("grep")}
    className={`px-3 py-1 text-[10px] font-bold uppercase tracking-wider rounded ${
      searchMode === "grep"
        ? "bg-surface-container-high text-primary"
        : "text-on-surface-variant"
    }`}
  >
    正则
  </button>
</div>
```

### C3. grep search handler

When `searchMode === "grep"`, the search handler calls `api.gitnexus.grep()`
instead of `api.repos.search()`:

```typescript
const handleSearch = async (e?: React.FormEvent) => {
  e?.preventDefault();
  if (!customSearchQuery.trim()) return;
  setIsSearching(true);
  setSearchError("");
  const executed = customSearchQuery.trim();

  try {
    if (searchMode === "grep") {
      const resp = await api.gitnexus.grep(executed, repoName || undefined);
      // Map grep results to the same SearchFile shape for unified rendering
      const grouped = groupGrepResults(resp.matches);
      setLastExecutedQuery(executed);
      setInteractiveResults(grouped);
    } else {
      // Existing Zoekt path
      const resp = await api.repos.search(task!.repository_id, executed);
      setLastExecutedQuery(executed);
      setInteractiveResults(resp.results);
    }
  } catch (err) {
    setSearchError(err instanceof Error ? err.message : "搜索失败");
  } finally {
    setIsSearching(false);
  }
};
```

### C4. Group helper (inline, not extracted)

```typescript
function groupGrepResults(
  matches: Array<{ file: string; line: number; content: string }>
): SearchFile[] {
  const byFile = new Map<string, SearchMatch[]>();
  for (const m of matches) {
    const arr = byFile.get(m.file) ?? [];
    arr.push({ line_number: m.line, line_content: m.content });
    byFile.set(m.file, arr);
  }
  return Array.from(byFile, ([file, matches]) => ({
    file,
    repo: "",
    matches,
  }));
}
```

### C5. Visual indicator

When grep mode is active and results are shown, add a badge:

```typescript
{searchMode === "grep" && interactiveResults && (
  <span className="text-[10px] bg-primary/10 text-primary border border-primary/20 px-1.5 py-0.5 rounded ml-auto uppercase tracking-wider font-bold">
    正则匹配
  </span>
)}
```

### Acceptance criteria (grep UI)

- [ ] Search tab has a Zoekt/grep mode toggle
- [ ] grep mode calls `api.gitnexus.grep()`, results render in same format
- [ ] Switching modes preserves the query text
- [ ] Placeholder text updates ("输入关键词..." vs "输入正则表达式...")
- [ ] page.tsx total change < 60 lines
- [ ] `tsc` passes

---

## Workstream D — route_map (DEFERRED)

GitNexus's `route_map` is an MCP-only tool with **no HTTP endpoint** in bridge
mode. Unlike impact analysis (which we solved via Cypher composition), route_map
requires MCP protocol access.

**Decision**: Defer route_map until either:
1. GitNexus adds an HTTP endpoint for it, OR
2. We implement an MCP client in the backend

This is recorded but not scheduled.

---

## SOP Compliance Checklist

- [ ] All proxy endpoints are pure HTTP forwarding (Iron Law)
- [ ] No file exceeds 350 lines (except page.tsx)
- [ ] `tsc` clean
- [ ] No new dependencies added
- [ ] Module docstrings updated
- [ ] route_map deferred with clear rationale

## Files Modified (complete list)

1. `backend/app/api/gitnexus_proxy.py` — 2 new proxy endpoints (~50 lines)
2. `frontend/src/lib/api.ts` — 2 new client methods
3. `frontend/src/app/(app)/tasks/[id]/page.tsx` — grep mode toggle + handler
