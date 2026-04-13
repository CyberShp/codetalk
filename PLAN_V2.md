# CodeTalks V2 Plan — deepwiki-first Vertical Slice

> Plan author: 宪宪/Opus-46 | Date: 2026-04-14
> Strategy: 垂直切片，先跑通 deepwiki-open 全链路，再逐个加工具

## 失败教训 (v1 post-mortem)

| 问题 | 根因 | V2 对策 |
|------|------|---------|
| AI 偷写分析逻辑 | 约束在长上下文中丢失 | 每个任务文件重复铁律 + 缅因猫逐行 review |
| 工具跑不起来 | 先写骨架后验证工具 | Phase 0 先验证 Docker + API，再写代码 |
| 水平分层浪费 | Phase3 工具失败导致 Phase2 白费 | 垂直切片：一个工具跑通再开下一个 |

## 团队分工

| 角色 | 猫 | 职责 |
|------|-----|------|
| 架构师/Lead | 宪宪 (Opus 4.6) | 计划制定、架构决策、复杂集成 |
| Reviewer | 缅因猫 | 逐行 review adapter，执行「零分析逻辑」检查 |
| 视觉审查 | 暹罗猫 | 前端完成后做 Kinetic Shadow Framework 合规检查 |
| CVO | 铲屎官 | 里程碑验收 |

## Phase 依赖图

```
Phase 0 (验证 deepwiki)
    │
    ├── Phase 1A (后端基础) ──┐
    │                         ├── Phase 2 (deepwiki adapter)
    ├── Phase 1B (前端基础) ──┤        │
    │                         │        v
    │                         ├── Phase 3 (项目/仓库 CRUD)
    │                         │        │
    │                         v        v
    │                     Phase 4 (deepwiki 端到端集成)
    │                              │
    │                              v
    │                         Phase 5 (打磨 + review)
    │
    └── [FUTURE] F1-Zoekt → F2-Joern → F3-CodeCompass → F4-GitNexus
```

**并行关系：**
- Phase 1A 和 1B 可并行（两个终端）
- Phase 2 依赖 1A
- Phase 3 依赖 1A + 1B
- Phase 4 依赖 2 + 3
- Phase 5 依赖 4

## 当前执行范围 (MVP)

**做：** Phase 0 → 1A → 1B → 2 → 3 → 4 → 5
**不做（已规划）：** Zoekt, Joern, CodeCompass, GitNexus, MR Diff 分析

## 任务文件索引

| 文件 | 阶段 | 状态 |
|------|------|------|
| `v2/PHASE0_validate_deepwiki.md` | 工具验证 | TODO |
| `v2/PHASE1A_backend_foundation.md` | 后端基础 | TODO |
| `v2/PHASE1B_frontend_foundation.md` | 前端基础 | TODO |
| `v2/PHASE2_deepwiki_adapter.md` | deepwiki 适配器 | TODO |
| `v2/PHASE3_project_repo_crud.md` | 项目/仓库管理 | TODO |
| `v2/PHASE4_deepwiki_e2e.md` | 端到端集成 | TODO |
| `v2/PHASE5_polish_review.md` | 打磨 + review | TODO |
| `v2/FUTURE_tools_roadmap.md` | 后续工具规划 | DEFERRED |

## 铁律（每个任务文件开头重复）

1. CodeTalks 是纯编排+可视化层，**绝不编写任何分析逻辑**
2. Adapter 的 `analyze()` 只允许：(a) HTTP/RPC 调用工具 (b) 响应格式转换
3. 移除工具 Docker 容器后，adapter 应报连接错误，而非静默产出结果
4. 旧任务文件在 `tasks/` 根目录保留，不修改
