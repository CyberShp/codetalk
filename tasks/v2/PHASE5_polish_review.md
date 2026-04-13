# Phase 5: 打磨 + Review

**前置依赖：Phase 4 完成（端到端跑通）**
**预估复杂度：中**

## 铁律提醒
> CodeTalks 绝不编写任何分析逻辑。Review 重点检查这条铁律。

## 目标

端到端跑通后的打磨：错误处理、UI 细节、review 门禁、视觉走查。

## 步骤

### 1. 错误处理加固

**后端：**
- deepwiki 连接失败 → 友好错误信息（非 500 traceback）
- Git 克隆失败 → 返回原因（网络/权限/仓库不存在）
- LLM API 调用失败 → 标记 AI 总结不可用，不影响工具结果
- 任务超时 → 可配置超时时间，超时标记 failed

**前端：**
- API 请求失败 → toast 通知
- WebSocket 断连 → 自动重连
- 空状态 → 引导用户创建第一个项目/任务

### 2. UI 细节打磨

按 Kinetic Shadow Framework 规范检查：
- [ ] 无 1px 实线边框（No-Line Rule）
- [ ] 正确的 surface 层级嵌套
- [ ] CTA 按钮使用渐变 + hover glow
- [ ] StatusBadge 发光效果
- [ ] 无 #FFFFFF 纯白色
- [ ] 日志终端彩色分级
- [ ] 毛玻璃效果生效

### 3. 缅因猫 Review — 零分析逻辑检查

**Review 范围：**
- `backend/app/adapters/deepwiki.py` — 逐行检查
- `backend/app/services/task_engine.py` — 确认只做编排
- `backend/app/services/ai_service.py` — 确认只调 LLM API

**检查项：**
- [ ] adapter.analyze() 中无正则匹配代码
- [ ] adapter.analyze() 中无 AST 遍历
- [ ] adapter.analyze() 中无文档生成逻辑
- [ ] adapter.analyze() 中无图谱构建
- [ ] 移除 deepwiki 容器 → adapter 报连接错误

### 4. 暹罗猫视觉走查

将前端截图发给暹罗猫，检查：
- Kinetic Shadow Framework 合规性
- 色彩体系一致性
- 字体层级正确性
- 毛玻璃/发光效果质量
- 整体视觉品质

### 5. 端到端冒烟测试清单

| 测试场景 | 预期结果 |
|---------|---------|
| 创建项目 | 项目出现在列表和侧边栏 |
| 添加 Git URL 仓库 | 克隆成功，仓库出现在项目下 |
| 添加本地路径仓库 | 验证路径后添加成功 |
| 创建 deepwiki 全量分析 | 任务运行并完成 |
| 查看分析结果 | Markdown 文档 + Mermaid 图表渲染 |
| AI 开关关闭 | deepwiki 不可选，无 AI 总结 |
| AI 开关开启 + 配置 LLM | 结果附带 AI 总结 |
| 删除项目 | 级联删除仓库和任务 |
| deepwiki 容器停止 | 工具状态显示 Offline，任务报错 |

## 验收标准

- [ ] 冒烟测试全部通过
- [ ] 缅因猫 review 通过（零分析逻辑确认）
- [ ] 暹罗猫视觉走查通过（或记录待改项）
- [ ] 无未处理的 500 错误
- [ ] MVP 可作为 demo 展示
