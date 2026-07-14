---
feature_ids:
  - workflow-v2-designer
topics:
  - canvas-pan
  - pointer-events
  - browser-e2e
doc_kind: bug-report
created: 2026-07-14
---

# 工作流画布无法按住左键拖动

## 报告人

用户在正式工作流设计器中发现：鼠标左键按住画布空白区域并移动时，视口没有平移。

## 复现与证据

1. 打开带 V2 草稿的工作流设计页。
2. 在节点之外的画布背景按住鼠标左键。
3. 移动鼠标后松开。
4. 预期 `canvas-world` 的 translate 随拖动变化；实际始终保持 `translate(36px, 42px)`。

真实浏览器红灯用例等待 5 秒后仍未观察到 transform 变化。

## 根因

画布只在 `pointerdown` 的 `target` 与 board 自身完全相同时启动平移。覆盖背景的 `canvas-world` 和 SVG 边层都是 board 的子元素，因此用户在视觉空白区域按下时，事件目标几乎总不是 board，平移入口直接返回。

## 修复方案

- 将可平移区域定义为 board 内除节点和连线命中区之外的所有背景后代。
- 继续由节点头部处理节点拖动，由连线命中层处理连线选择，避免交互冲突。
- 使用 pointer capture 跟踪本次指针，并在 `pointerup` 或 `pointercancel` 时统一清理监听。
- 背景平移和节点拖动共用同一套 pointer lifecycle；忽略非主指针，并在 capture 丢失时同样清理，防止取消手势后节点跳动。
- 禁止画布背景文本选择与浏览器触控滚动抢占拖动手势。

## 验证

- 新增真实 Chromium E2E：从 `canvas-world` 空白位置按下左键并拖动，断言 transform 发生变化。
- 同一用例验证节点拖动不会带动画布，并模拟 `pointercancel`，确认后续指针移动不会继续移动节点。
- 回归端口连线、连线选择和属性面板交互。
- 运行 ESLint、TypeScript 与 Next.js 生产构建。
