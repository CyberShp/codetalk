---
feature_id: F001
name: "GitNexus Phase 3 — Impact Analysis (Cypher-based blast radius)"
depends_on: [GITNEXUS Phase 2 (Cypher + Process/Community — COMPLETE)]
parallel_safe: true
owner: sonnet
reviewer: gpt52
doc_kind: task
created: 2026-04-18
topics: [backend, frontend, gitnexus, proxy, impact-analysis, intelligence-panel]
---

# GitNexus Phase 3: Impact Analysis (Cypher-based blast radius)

## Context

Phase 2 shipped Cypher query proxy, Process/Community detail APIs, and
IntelligencePanel enrichment. Phase 3 adds **impact analysis** — when a user
clicks a Function, Method, or Class node in the graph viewer, show its upstream
callers and downstream callees (blast radius).

**Architecture decision**: GitNexus's `impact` / `detect_changes` / `api_impact`
tools are MCP-only — they have **no HTTP endpoint** in bridge/serve mode. Phase 3
solves this by **composing Cypher queries** and sending them through the existing
`POST /api/query` proxy. This is iron-law compliant: Cypher composition is query
construction (like SQL), not analysis logic. The graph traversal runs inside
GitNexus's graph engine.

**Iron Law reminder**: The backend composes a Cypher string and proxies it to
GitNexus. Zero graph traversal, zero analysis logic in our code.

---

## Workstream A — Backend: Impact Endpoint

**File**: `backend/app/api/gitnexus_proxy.py` (currently 201 lines)

Add 1 new endpoint that composes Cypher and proxies via the existing httpx
pattern. This is NOT a simple pass-through — it builds a Cypher query from
structured parameters, then forwards it to GitNexus `/api/query`. This is
equivalent to an ORM building SQL: query construction, not analysis.

### A1. `POST /api/gitnexus/impact` — Blast radius via Cypher

Request model:

```python
class ImpactRequest(BaseModel):
    target: str                           # Symbol name (function/class/method)
    direction: str = "both"               # "upstream" | "downstream" | "both"
    depth: int = 3                        # Traversal depth (clamped 1-5)
    repo: str | None = None
```

Implementation approach — compose Cypher based on `direction`:

```python
@router.post("/impact")
async def analyze_impact(body: ImpactRequest):
    """Compose Cypher blast-radius query and proxy to GitNexus /api/query."""
    depth = max(1, min(body.depth, 5))  # Clamp to [1, 5]
    target_name = body.target

    queries: dict[str, str] = {}

    if body.direction in ("upstream", "both"):
        queries["upstream"] = (
            f"MATCH path=(caller)-[:CALLS*1..{depth}]->"
            f"(target {{name: '{target_name}'}}) "
            f"RETURN nodes(path) AS nodes, relationships(path) AS rels"
        )

    if body.direction in ("downstream", "both"):
        queries["downstream"] = (
            f"MATCH path=(source {{name: '{target_name}'}})"
            f"-[:CALLS*1..{depth}]->(callee) "
            f"RETURN nodes(path) AS nodes, relationships(path) AS rels"
        )

    params: dict[str, str] = {}
    if body.repo:
        params["repo"] = body.repo

    results: dict[str, list] = {}
    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=30
        ) as client:
            for direction, cypher in queries.items():
                resp = await client.post(
                    "/api/query", params=params, json={"cypher": cypher}
                )
                resp.raise_for_status()
                results[direction] = resp.json().get("results", [])
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code, detail=exc.response.text
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {"target": target_name, "depth": depth, **results}
```

**Important implementation notes**:

1. The Cypher uses `{name: '...'}` property match which works across Function,
   Method, and Class labels — no need to specify the label.
2. Each direction is a separate Cypher query. When `direction="both"`, fire
   both queries sequentially (not parallel — same httpx client, simple code).
3. Response shape: `{ target, depth, upstream?: [...], downstream?: [...] }`.
   Each list contains path results with `nodes` and `rels` arrays.
4. Depth clamped to [1, 5] to prevent expensive unbounded traversals.

### A2. Update module docstring

Add `- Analyze impact/blast radius via Cypher composition (/impact)` to the
module docstring's endpoint list.

### Acceptance criteria (backend)

- [ ] `POST /api/gitnexus/impact` endpoint works with all 3 direction values
- [ ] Depth clamped to [1, 5]
- [ ] Cypher composed as string, forwarded to GitNexus `/api/query`
- [ ] Error handling follows existing pattern (HTTPStatusError → status code,
      HTTPError → 502)
- [ ] File stays under 280 lines (~201 current + ~60 new)
- [ ] Module docstring updated
- [ ] No analysis logic — just Cypher string construction + proxy

---

## Workstream B — Frontend API Client

**File**: `frontend/src/lib/api.ts` — `gitnexus` namespace (lines 244-308)

Add 1 new method under `api.gitnexus`:

```typescript
// B1: Impact analysis (blast radius)
impact: (
  target: string,
  direction: "upstream" | "downstream" | "both" = "both",
  depth = 3,
  repo?: string,
) =>
  request<{
    target: string;
    depth: number;
    upstream?: Array<{ nodes: unknown[]; rels: unknown[] }>;
    downstream?: Array<{ nodes: unknown[]; rels: unknown[] }>;
  }>("/api/gitnexus/impact", {
    method: "POST",
    body: JSON.stringify({ target, direction, depth, repo }),
  }),
```

### Acceptance criteria (API client)

- [ ] `api.gitnexus.impact()` method added
- [ ] Type-safe request/response (typed `direction` union, typed response)
- [ ] `tsc` passes cleanly

---

## Workstream C — IntelligencePanel: ImpactView

**Files**:
- `frontend/src/components/ui/IntelligencePanel.tsx` (currently 324 lines)
- **NEW** `frontend/src/components/ui/ImpactView.tsx` (if needed for 350-line cap)

IntelligencePanel is at 324 lines. An ImpactView component would add ~80-100
lines, exceeding the 350-line SOP cap. **Extract ImpactView to a separate file**
and import it in IntelligencePanel.

### C1. Routing in IntelligencePanel

Add a condition for Function/Method/Class nodes in the main `IntelligencePanel`
component:

```typescript
// In IntelligencePanel (after existing Process/Community checks):
if (node.label === "Function" || node.label === "Method" || node.label === "Class") {
  return <ImpactView key={node.id} node={node} nodeMap={nodeMap} onNodeClick={onNodeClick} repo={repo} />;
}
```

This adds ~4 lines to IntelligencePanel.tsx — stays well under 350.

### C2. ImpactView component (`ImpactView.tsx`)

New file. Props:

```typescript
interface ImpactViewProps {
  node: GraphNode;
  nodeMap: Map<string, GraphNode>;
  onNodeClick: (node: GraphNode) => void;
  repo?: string;
}
```

Behavior:
1. On mount (keyed by `node.id`), call `api.gitnexus.impact(node.properties.name, "both", 3, repo)`.
2. Show loading skeleton while fetching.
3. On success, render two sections: **Upstream Callers** and **Downstream Callees**.
4. On failure, show "影响面数据不可用" (silent — no error toast).
5. Each listed symbol is clickable if it exists in `nodeMap` (same pattern as
   ProcessView step items).

UI layout (follows existing Kinetic Shadow design):

```
┌──────────────────────────────────────┐
│  [Function]  [跨社区] (if cross)     │  ← Label badge
│  functionName                        │  ← Title
│  "description..."                    │  ← If available
├──────────────────────────────────────┤
│  文件  src/foo.ts:42                 │  ← File location
│  参数  2                             │  ← Stats row (optional)
├──────────────────────────────────────┤
│  上游调用者 (3)                       │  ← Upstream section
│  ├─ handleRequest   Function         │
│  ├─ processOrder    Method           │
│  └─ main            Function         │
├──────────────────────────────────────┤
│  下游被调用 (2)                       │  ← Downstream section
│  ├─ validateInput   Function         │
│  └─ saveToDb        Function         │
└──────────────────────────────────────┘
```

Implementation specifics:

- **Header**: Label badge (color based on label — Function=#10B981,
  Method=#F59E0B, Class=#8B5CF6), name, description if present.
- **File location**: Show `filePath:startLine` if available.
- **Impact lists**: Each item shows name + label badge. Clickable items have
  `hover:bg-surface-container-high/50` transition, same as ProcessView steps.
- **Deduplication**: The Cypher path query may return the same node multiple
  times across different paths. Deduplicate by node `id` before rendering.
- **Empty state**: If no upstream/downstream found, show
  "未发现上游调用者" / "未发现下游依赖".

Response data extraction — the Cypher returns path results. Each result has
`nodes` (array of node objects) and `rels` (array of relationship objects).
Extract unique caller/callee nodes by collecting all nodes from all paths,
excluding the target node itself.

```typescript
// Pseudocode for extracting unique nodes from path results
function extractNodes(
  pathResults: Array<{ nodes: unknown[]; rels: unknown[] }>,
  targetName: string,
): GraphNode[] {
  const seen = new Set<string>();
  const out: GraphNode[] = [];
  for (const path of pathResults) {
    for (const raw of path.nodes) {
      const n = raw as { id: string; name?: string; label?: string; [k: string]: unknown };
      if (n.name === targetName || seen.has(n.id)) continue;
      seen.add(n.id);
      out.push(/* map to GraphNode or use nodeMap.get(n.id) */);
    }
  }
  return out;
}
```

### C3. Direction toggle (stretch — implement only if simple)

Add a small toggle to switch between "upstream" / "downstream" / "both".
Default: "both". Use a simple button group with `text-[10px]` styling.
If this pushes ImpactView over 150 lines, skip it — "both" only is fine.

### Acceptance criteria (ImpactView)

- [ ] ImpactView renders for Function, Method, and Class nodes
- [ ] Calls `api.gitnexus.impact()` on mount with `key={node.id}` remounting
- [ ] Shows loading skeleton during fetch
- [ ] Silent fallback on API error (no crash, shows empty state)
- [ ] Upstream/downstream lists show deduplicated nodes
- [ ] Clickable nodes navigate via `onNodeClick`
- [ ] Follows Kinetic Shadow design (GlassPanel, font-data, color tokens)
- [ ] ImpactView.tsx stays under 200 lines
- [ ] IntelligencePanel.tsx stays under 350 lines
- [ ] `tsc` passes cleanly

---

## SOP Compliance Checklist

- [ ] Impact endpoint composes Cypher only — no graph traversal (Iron Law)
- [ ] No file exceeds 350 lines (except page.tsx)
- [ ] `tsc` clean
- [ ] No new dependencies added
- [ ] Module docstrings updated
- [ ] New component follows existing patterns (GlassPanel, key-based remount,
      silent fallback, font-data/text-on-surface tokens)

## Files Modified (complete list)

1. `backend/app/api/gitnexus_proxy.py` — new `/impact` endpoint (~60 lines)
2. `frontend/src/lib/api.ts` — new `impact()` client method
3. `frontend/src/components/ui/ImpactView.tsx` — **NEW** blast radius view
4. `frontend/src/components/ui/IntelligencePanel.tsx` — import + route to ImpactView (~4 lines)

## Cypher Reference (for implementer)

Graph schema (from `backend/app/adapters/gitnexus.py`):

| Edge Type         | Meaning                            |
|-------------------|------------------------------------|
| CALLS             | Function/Method → Function/Method  |
| IMPORTS           | Module → Module                    |
| CONTAINS          | File/Class → Function/Method       |
| MEMBER_OF         | Symbol → Community                 |
| STEP_IN_PROCESS   | Symbol → Process (with `step` ord) |

Node labels: `File`, `Folder`, `Function`, `Method`, `Class`, `Module`,
`Process`, `Community`.

Node properties (relevant): `name`, `filePath`, `startLine`, `endLine`,
`description`, `parameters`, `returnType`.

Upstream query pattern:
```cypher
MATCH path=(caller)-[:CALLS*1..3]->(target {name: 'targetName'})
RETURN nodes(path) AS nodes, relationships(path) AS rels
```

Downstream query pattern:
```cypher
MATCH path=(source {name: 'targetName'})-[:CALLS*1..3]->(callee)
RETURN nodes(path) AS nodes, relationships(path) AS rels
```
