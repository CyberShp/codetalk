---
feature_id: F001
name: "GitNexus Phase 4 — Graph ↔ Chat Bridge"
depends_on: [GITNEXUS Phase 3 (Impact Analysis — COMPLETE)]
parallel_safe: true
owner: sonnet
reviewer: gpt52
visual_design: gemini (PENDING — see Design Requirements below)
doc_kind: task
created: 2026-04-18
topics: [backend, frontend, gitnexus, chat, deepwiki, intelligence-panel]
---

# GitNexus Phase 4: Graph ↔ Chat Bridge

## Context

Phase 1–3 shipped search, Cypher proxy, process/community enrichment, and impact
analysis. The graph and chat systems are fully functional but **completely
isolated** — the user cannot flow between them. Phase 4 bridges this gap.

**Iron Law reminder**: Chat proxies to DeepWiki. GitNexus queries go through
existing proxy endpoints. No analysis logic. Context injection is HTTP calls to
existing services, not code analysis.

---

## Workstream A — Graph → Chat: "追问" Action (Frontend)

**Goal**: User selects a graph node → clicks a button → FloatingChat opens with
context about that node (file path scoped, pre-filled question).

### A1. "在 Chat 中追问" button in sidebar

**File**: `frontend/src/app/(app)/tasks/[id]/page.tsx`

Add a small action button below the IntelligencePanel / CodePanel in the sidebar.
Visible when `selectedNode` is set.

```typescript
// After the IntelligencePanel/CodePanel render in the sidebar:
{selectedNode && (
  <button
    onClick={() => handleAskAboutNode(selectedNode)}
    className="..." // Gemini to design — see Design Requirements
  >
    <MessageSquareText size={14} />
    在 Chat 中追问
  </button>
)}
```

### A2. `handleAskAboutNode` callback

**File**: `frontend/src/app/(app)/tasks/[id]/page.tsx`

```typescript
const handleAskAboutNode = useCallback((node: GraphNode) => {
  // 1. Build scoped file paths
  const filePaths = node.properties.filePath ? [node.properties.filePath] : [];

  // 2. Build contextual question
  const question = `请解释 ${node.properties.name} (${node.label}) 的实现逻辑、调用关系和设计意图。`;

  // 3. Open FloatingChat with context
  setGraphChatContext({ filePaths, initialQuestion: question });
  setGraphChatOpen(true);
}, []);
```

### A3. Thread graph context to FloatingChat

**File**: `frontend/src/app/(app)/tasks/[id]/page.tsx`

Add state:

```typescript
const [graphChatOpen, setGraphChatOpen] = useState(false);
const [graphChatContext, setGraphChatContext] = useState<{
  filePaths: string[];
  initialQuestion: string;
} | null>(null);
```

Pass to FloatingChat:

```typescript
<FloatingChat
  repoId={task.repository_id}
  currentPageFilePaths={
    tab === "documentation" ? wikiPageFilePaths
    : tab === "graph" && graphChatContext ? graphChatContext.filePaths
    : undefined
  }
  hidden={tab === "documentation" && showDocChat}
  forceOpen={graphChatOpen}
  initialMessage={graphChatContext?.initialQuestion}
  onClose={() => { setGraphChatOpen(false); setGraphChatContext(null); }}
/>
```

### A4. FloatingChat enhancements

**File**: `frontend/src/components/ui/FloatingChat.tsx` (80 lines)

Add props:

```typescript
interface Props {
  repoId: string;
  currentPageFilePaths?: string[];
  hidden?: boolean;
  forceOpen?: boolean;            // NEW: open from external trigger
  initialMessage?: string;        // NEW: pre-filled question
  onClose?: () => void;           // NEW: notify parent on close
}
```

Behavior:
- When `forceOpen` transitions to `true`, open the chat panel
- When `initialMessage` is provided and changes, set it as the input value
  (do NOT auto-send — let user review and modify)
- When user closes, call `onClose`

### A5. useChatEngine: accept initial message

**File**: `frontend/src/hooks/useChatEngine.ts` (344 lines)

No changes needed — the initial message is set via `engine.setInput()` from
FloatingChat after opening. The existing `handleSend` flow handles the rest.

### Acceptance criteria (Graph → Chat)

- [ ] "在 Chat 中追问" button appears in sidebar when a node is selected
- [ ] Clicking opens FloatingChat with the node's filePath as context
- [ ] Input pre-filled with a contextual question (user can edit before sending)
- [ ] FloatingChat.tsx stays under 120 lines
- [ ] page.tsx change is minimal (~20 lines of state + callback)
- [ ] `tsc` passes

---

## Workstream B — GitNexus Context Injection into Chat (Backend)

**Goal**: When user sends a chat message about a repo, automatically search
GitNexus for related symbols and inject results as additional context for
DeepWiki.

### B1. GitNexus context helper

**File**: `backend/app/api/repo_chat.py`

Add a helper function (similar to existing Zoekt context injection in chat.py):

```python
async def _gitnexus_search_context(query: str, repo_name: str) -> str:
    """Search GitNexus for symbols related to the user's question.

    IRON LAW: pure HTTP call to GitNexus /api/search. No analysis.
    Returns formatted string for injection into chat context.
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=10
        ) as client:
            params = {"repo": repo_name} if repo_name else {}
            resp = await client.post(
                "/api/search",
                params=params,
                json={"query": query, "mode": "hybrid", "limit": 5, "enrich": True},
            )
            if resp.status_code != 200:
                return ""
            results = resp.json().get("results", [])
            if not results:
                return ""

            lines = ["[知识图谱相关符号]:"]
            for r in results[:5]:
                name = r.get("name", "")
                label = r.get("label", "")
                path = r.get("filePath", "")
                cluster = r.get("cluster", "")
                processes = r.get("processes", [])
                line = f"- {name} ({label}) @ {path}"
                if cluster:
                    line += f" [社区: {cluster}]"
                if processes:
                    line += f" [流程: {', '.join(processes[:3])}]"
                lines.append(line)
            return "\n".join(lines)
    except Exception:
        return ""  # Non-fatal — chat works without graph context
```

### B2. Inject into repo chat stream

**File**: `backend/app/api/repo_chat.py`

In `repo_chat_stream()`, before building the DeepWiki payload, call
`_gitnexus_search_context()` and append the result to the system message or
user query context. Follow the same pattern as Zoekt context injection.

```python
# After resolving repo and before calling deepwiki:
gitnexus_ctx = await _gitnexus_search_context(
    body.messages[-1].content,  # User's latest question
    repo.name,
)
# Append to context_parts (similar to Zoekt context)
if gitnexus_ctx:
    context_parts.append(gitnexus_ctx)
```

**Important**: This must be non-blocking and fail-safe. If GitNexus is down
(as we learned — it can go offline), chat must still work normally.

### B3. Guard: skip if GitNexus not indexed

Check if the repo has been analyzed by GitNexus before searching. If not,
skip silently. Don't block chat on GitNexus availability.

### Acceptance criteria (Context Injection)

- [ ] `_gitnexus_search_context()` calls GitNexus `/api/search` — no analysis
- [ ] Context injection is non-fatal (try/except, returns "" on failure)
- [ ] GitNexus context appears in DeepWiki answers when available
- [ ] Chat still works when GitNexus is offline
- [ ] repo_chat.py stays under 300 lines
- [ ] No new dependencies

---

## Workstream C — Chat → Graph: Symbol Links (Frontend)

**Goal**: Code symbols mentioned in AI responses become clickable links that
navigate to the graph node.

**BLOCKED**: This workstream depends on Gemini's design spec for how linked
symbols appear in chat. **Do not implement until design is provided.**

### C1. Design requirements (for Gemini)

Need design spec for:
- How clickable code symbols appear in chat messages (color, underline, icon?)
- Click interaction: does it switch tabs? Show a preview tooltip? Both?
- What happens when the symbol isn't in the current graph?

### C2. Implementation sketch (for after design)

**File**: `frontend/src/components/ui/ChatPanel.tsx`

The markdown renderer already handles backtick code spans. Enhancement:
- Post-process rendered markdown to find `<code>` elements
- Check if the code text matches a node name in `nodeMap`
- If yes, wrap in a clickable element that calls `onSymbolClick(nodeName)`

This requires `nodeMap` to be accessible from ChatPanel, which means threading
it through props or using a context.

### Acceptance criteria (Chat → Graph)

- [ ] BLOCKED on Gemini design spec
- [ ] When implemented: code symbols in AI responses are clickable
- [ ] Clicking navigates to the graph tab and selects the node
- [ ] Non-matching symbols render normally (no broken links)
- [ ] tsc passes

---

## Design Requirements (for @gemini)

Phase 4 needs visual design specs for **3 elements**:

### D1. "在 Chat 中追问" button
- Placement: below IntelligencePanel/CodePanel in the graph sidebar
- Context: appears when any graph node is selected
- Requirements: must feel like a natural extension of the sidebar, not a
  bolted-on afterthought. Should suggest "this node connects to chat."
- Design deliverable: CSS classes, icon choice, hover/active states

### D2. Graph context indicator in chat
- When chat is opened from a graph node, how do we show which node is
  providing context? (e.g., a small card at the top of chat showing
  "Discussing: validateUser (Function)")
- Design deliverable: layout, colors, dismiss interaction

### D3. Clickable symbol links in chat responses
- How do code symbols that exist in the graph appear differently in AI
  responses? (e.g., colored, underlined, icon prefix?)
- What happens on hover? On click?
- Design deliverable: inline element styles, tooltip spec if applicable

---

## SOP Compliance Checklist

- [ ] All endpoints are pure HTTP proxy / HTTP call (Iron Law)
- [ ] No file exceeds 350 lines (except page.tsx)
- [ ] `tsc` clean
- [ ] No new dependencies added
- [ ] Non-fatal on GitNexus offline
- [ ] Gemini design spec received before implementing Workstream C

## Files Modified (complete list)

### Workstream A (frontend):
1. `frontend/src/app/(app)/tasks/[id]/page.tsx` — button + state + callback
2. `frontend/src/components/ui/FloatingChat.tsx` — forceOpen + initialMessage props

### Workstream B (backend):
3. `backend/app/api/repo_chat.py` — `_gitnexus_search_context()` + injection

### Workstream C (frontend, BLOCKED):
4. `frontend/src/components/ui/ChatPanel.tsx` — symbol link rendering (after design)
