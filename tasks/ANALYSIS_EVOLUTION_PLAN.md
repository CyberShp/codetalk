# Analysis Evolution Plan — 静态分析能力演进总规划

**作者**: 宪宪/Opus-46  
**角色分工**: Opus 规划架构 → Sonnet 编码 → GPT-5.2 审查门禁 → Gemini 视觉调整  
**核心约束**: 页面不挤、重点突出、渐进披露

---

## UI 信息架构重构

### 问题
当前 5 个平行 tab（概览/分支/测试点/数据追踪/复杂度），每个 tab 独立展示一个工具的结果。用户看不到全局风险图景，需要自己在 tab 间穿梭拼凑。新功能如果继续加 tab，页面只会更散。

### 设计原则
1. **入口聚焦**: 首屏只展示"哪些函数最危险"，不展示工具细节
2. **渐进披露**: 点击热点函数 → 展开该函数的所有分析维度（不切 tab）
3. **按需深入**: 高级工具（Zoekt 搜索、自定义 CPGQL）折叠在底部，需要时展开
4. **不加 tab**: 现有 5 个侧栏导航 → 收敛为 3 个

### 导航重构
```
现在:  概览 | 分支分析 | 测试点 | 数据追踪 | 复杂度   (5 个平行 tab)
目标:  风险总览 | 深度分析 | 测试计划                    (3 个层次)
```

- **风险总览** (Dashboard): 风险矩阵热力表，一眼看到 top-N 危险函数
- **深度分析** (Investigate): 选中函数后的全维度分析（合并原分支+数据追踪+复杂度）
- **测试计划** (Test Plan): 测试点生成 + 批量导出

"深度分析"不是独立入口，而是从风险总览点击函数后的自然跳转。

---

## 分阶段实施

### Phase A: 风险矩阵 + Dashboard (P0)

**目标**: 替换当前空洞的"概览"tab，给用户一个"从哪里开始"的入口。

**后端**:
- 新 API: `GET /api/repos/{id}/analysis/risk-matrix`
- 聚合三个已有数据源（纯编排，不做分析）:
  - Joern method_list → complexity per function
  - Joern taint (co-occur) → taint exposure count per function
  - Git log → change frequency per file (可选，git_service 已有基础)
- 返回: `[{method, file, complexity, taint_count, change_freq, risk_score}]`
- risk_score = normalize(complexity_density) × normalize(taint_count) × normalize(change_freq)
- 归一化用百分位排名，不是绝对值

**前端**:
- 替换 OverviewView 为 RiskDashboardView
- 热力表: 行=函数, 列=复杂度/污点暴露/变更频率/综合风险
- 颜色编码: 综合风险 HIGH/MED/LOW
- 点击函数行 → setActiveNav("branches") 并预填方法名（复用现有深度分析）
- 顶部 4 个统计卡: 总函数数 / 高风险函数数 / 污点路径总数 / 平均复杂度

**验收**: 打开分析页 → 首屏看到风险排名 → 点击函数 → 跳转到该函数的分支分析

---

### Phase B: 深度分析合并视图 (P0)

**目标**: 点击风险函数后，在一个页面内看到该函数的所有分析维度。

**前端**:
- 当从 Dashboard 点击函数进入分支分析时，在现有 BranchesView 的方法详情区域增加：
  - 该函数的 taint 路径（从 TaintView 的数据复用，按方法名过滤）
  - 该函数的复杂度密度指标（从 ComplexityView 的数据复用）
- 用折叠面板（Accordion）组织: CFG | 调用上下文 | 分支结构 | 污点路径 | 边界值 | 错误路径
- 默认只展开 CFG + 调用上下文，其余折叠
- 保留原有独立 tab 作为"全量浏览"入口（高级用户仍可直接看全部复杂度分布）

**后端**: 无新 API，前端复用已有接口

**验收**: 从 Dashboard 点击函数 → 一页内看到 CFG + 污点 + 复杂度 + 分支，不用切 tab

---

### Phase C: 分片真实数据流 (P1)

**目标**: 用 Joern reachableByFlows 做真实污点追踪，但限制作用域避免超时。

**后端**:
- 新 adapter 方法: `scoped_taint_analysis(method_name, source, sink)`
- CPGQL 策略:
  ```scala
  cpg.method.name("target_method").ast.isCall.name(srcPat)
    .reachableByFlows(cpg.method.name("target_method").ast.isCall.name(sinkPat))
  ```
- 超时 30s，超时则返回 `{verified: false, fallback: "cooccur"}`
- 新 API: `POST /api/repos/{id}/analysis/joern/taint-verify`
  - 输入: `{method, source, sink}`
  - 输出: `{verified: bool, flow_path?: [...], fallback?: string}`

**前端**:
- 在 TaintView 的每条共现路径上加"验证"按钮
- 点击后异步调用 verify API
- 结果: ✓ 已验证真实流 / ✗ 未能验证 / ⏳ 验证中
- Dashboard 的风险评分可以加权已验证路径

**验收**: 选一条共现路径 → 点击验证 → 看到真实数据流路径或"无法验证"

---

### Phase D: 结果持久化 + 趋势 (P1)

**目标**: 分析结果存库，支持历史对比。

**后端**:
- 新表: `analysis_snapshots` (id, repo_id, created_at, risk_matrix_json, summary_json)
- 每次打开分析页或手动触发，存一份快照
- 新 API: `GET /api/repos/{id}/analysis/snapshots` — 历史列表
- 新 API: `GET /api/repos/{id}/analysis/diff?from={id}&to={id}` — 两次快照对比

**前端**:
- Dashboard 顶部统计卡旁加趋势箭头 (↑2 / ↓3 / →)
- 可选: 历史对比抽屉，显示两次快照的 diff

**验收**: 运行两次分析 → 看到"高风险函数 +2 ↑"趋势标注

---

### Phase E: Zoekt 模式搜索 (P2)

**目标**: 在分析页内按代码模式搜索，找到特定风险模式。

**后端**: Zoekt adapter 已存在，只需新增分析页路由转发

**前端**:
- "深度分析" tab 底部增加折叠式"模式搜索"区域
- 预设模式:
  - "未检查的 malloc" → `malloc.*(?!.*if.*==.*NULL)`
  - "未关闭的文件描述符" → `open\(` (配合 absence 结果)
  - "硬编码密钥" → `(password|secret|key)\s*=\s*["']`
- 结果直接嵌入分析上下文（不跳转到独立搜索页）

---

### Phase F: 跨工具融合 (P2)

**目标**: GitNexus 依赖图 + Joern 调用图叠加，计算影响半径。

**后端**:
- 新 API: `GET /api/repos/{id}/analysis/impact-radius?method={name}`
- 编排: Joern callers(method) → GitNexus 找对应文件的模块级依赖 → 返回影响链

**前端**:
- 在深度分析的"调用上下文"区块中，增加"模块影响面"展开项
- 显示: 该函数被哪些模块间接依赖

---

### Phase G: 批量导出 (P2)

**目标**: 分析结果可导出为 CSV/JSON。

**前端**:
- Dashboard 右上角"导出"按钮
- 导出内容: 风险矩阵全表 / 当前视图的测试点 / 污点路径列表
- 格式: CSV (默认) / JSON

---

## 执行节奏

```
Phase A (风险矩阵)     ████████░░  ← 先做，给用户"首屏焦点"
Phase B (合并视图)      ░░████████  ← 紧接，减少 tab 跳转
  ↕ Gemini 视觉审查
Phase C (真实数据流)    ░░░░████░░  ← 独立后端，可与 B 并行
Phase D (持久化)        ░░░░░░████  ← 依赖 A 的数据结构稳定后
Phase E-G              ░░░░░░░░██  ← 按需排入
```

Phase A 和 B 是用户感知最大的改变，建议一起交付。
Phase C 是精度基础，但对前端改动小，可以后端先行。

---

## 给 Sonnet 的执行说明

每个 Phase 会拆成独立的 task 文件（如 `ANALYSIS_PHASE_A_RISK_MATRIX.md`），包含：
- 具体步骤和代码位置
- 验收标准
- 需要遵守的约束（CLAUDE.md 铁律）

等铲屎官确认优先级后，我开始出具体 task 文件。
