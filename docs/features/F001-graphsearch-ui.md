---
feature_id: F001
name: GitNexus GraphSearch UI Design
status: spec
owner: gemini
doc_kind: spec
created: 2026-04-18
topics: [frontend, ui, gitnexus, graph]
---

# F001: GitNexus GraphSearch UI Design

## 1. Visual Identity: Kinetic Shadow Framework
- **Theme**: Always Dark.
- **Surface**: `#10141A` (Base background).
- **Surface-Container**: `#1C2026` (Panel background).
- **Primary**: `#A4E6FF` (Action/Highlight color).
- **On-Surface-Variant**: `#BFC5D0` (Secondary text).
- **Outline-Variant**: `#44474E` (Ghost borders - 15% opacity).
- **Font Display**: Space Grotesk.
- **Font UI**: Inter.
- **Font Data/Code**: JetBrains Mono.

## 2. Component: GraphSearch
Floating search panel integrated into the `GraphViewer`.

### 2.1 Placement
- **Location**: Top-left corner of the viewport.
- **CSS**: `absolute top-4 left-4 z-50 w-[360px]`.
- **Styling**: 
  - `backdrop-blur-md bg-surface/80`
  - `border border-outline-variant/15` (Ghost Border)
  - `rounded-xl shadow-2xl shadow-black/40`

### 2.2 Header (Search Input)
- **Container**: `px-4 pt-4 pb-3`.
- **Input Field**:
  - `font-data text-sm bg-surface-container-low border border-outline-variant/20 rounded-lg px-3 py-2.5 w-full`
  - `focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all`
  - Placeholder: "Search symbols, files, nodes..."
- **Search Icon**: `A4E6FF` at 60% opacity, positioned inside the input (left).

### 2.3 Mode Switching (Segmented Tabs)
- **Container**: `px-4 pb-3`.
- **Tabs**: `flex bg-surface-container-lowest p-1 rounded-md border border-outline-variant/10`.
- **Items**: `flex-1 text-center text-xs py-1.5 rounded cursor-pointer transition-all font-display`.
  - **Active**: `bg-surface-container-high text-primary font-bold shadow-sm`.
  - **Inactive**: `text-on-surface-variant hover:text-on-surface hover:bg-surface-container/50`.
- **Modes**: Hybrid (Default) | BM25 | Semantic.

### 2.4 Results List
- **Container**: `max-h-[400px] overflow-y-auto scrollbar-thin`.
- **Result Item (Card)**:
  - `group relative flex items-start gap-3 p-4 border-b border-outline-variant/10 last:border-0 hover:bg-surface-container-high/50 cursor-pointer transition-colors`.
  - **Score Indicator (Left)**:
    - `absolute left-0 top-0 bottom-0 w-[2px] bg-gradient-to-b from-primary to-transparent opacity-0 group-hover:opacity-100 transition-opacity`.
  - **Layout (Vertical Stack)**:
    - **Line 1 (Identity)**:
      - `TypeBadge`: `bg-[NODE_COLOR] text-[white] text-[10px] px-1.5 py-0.5 rounded font-bold uppercase tracking-wider`.
      - `SymbolName`: `font-data text-sm font-semibold truncate ml-1 text-on-surface`.
      - `Connections`: `ml-auto font-data text-[10px] text-on-surface-variant bg-surface-container px-1.5 py-0.5 rounded border border-outline-variant/10`. Format: `12↔`.
    - **Line 2 (Location)**:
      - `FilePath`: `font-data text-[11px] text-on-surface-variant/70 mt-1 truncate`.
    - **Line 3 (Metadata/Tags)**:
      - `TagsContainer`: `flex gap-2 mt-2`.
      - `Tag`: `text-[10px] border border-outline-variant/20 px-1.5 py-0.5 rounded text-on-surface-variant/80 hover:border-primary/30 transition-colors`. (e.g., `Cluster: Auth`, `Process: Login`).

### 2.5 Interactions (Jump Animation)
When a result is clicked:
1. **GraphViewer Action**: `autoCenter(nodeId)`.
2. **Visual Feedback**:
   - **Scale**: Target node scales from `1.0` to `1.5` and back to `1.2`.
   - **Pulse**: Three concentric circles (`primary` color) expand from the node center and fade out.
   - **Glow**: Node gains an outer glow (`drop-shadow(0 0 12px var(--primary))`) for 2 seconds.

## 3. Design Tokens (NODE_COLORS)
Consistency with `GraphViewer.tsx`:
- `File`: `#3B82F6`
- `Folder`: `#6366F1`
- `Class`: `#8B5CF6`
- `Function`: `#10B981`
- `Method`: `#14B8A6`
- `Module`: `#F59E0B`
- `Route`: `#EF4444`
- `Process`: `#EC4899`
- `Community`: `#6366F1`
- `Tool`: `#F97316`

## 4. Edge Cases
- **No Results**: Show `EmptyState` with a "Neural Ghost" icon and text "No signals detected".
- **Loading**: Pulse skeleton cards for the list.
- **Large Lists**: Virtualized list if count > 50 (Performance Gate).
