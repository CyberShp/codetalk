"use client";

import { useState, useEffect } from "react";
import ToolCard from "@/components/ui/ToolCard";
import { api } from "@/lib/api";
import type { ToolInfo } from "@/lib/types";

const toolDescriptions: Record<string, string> = {
  deepwiki:
    "基于 RAG 的 AI 驱动仓库文档与知识图谱生成。",
  zoekt:
    "面向大型代码库的快速三元组代码搜索引擎。",
  joern:
    "C/C++ 代码属性图分析 — 调用图、污点分析、安全扫描。",
  codecompass:
    "C/C++ 代码理解工具 — 调用图、依赖分析、指针分析。",
  gitnexus:
    "Git 原生代码搜索与跨仓库依赖追踪。",
};

const comingSoonTools: ToolInfo[] = [
  { name: "zoekt", capabilities: ["code_search"], healthy: false, message: "未配置" },
  { name: "joern", capabilities: ["call_graph", "taint_analysis", "security_scan", "ast_analysis"], healthy: false, message: "未配置" },
  { name: "codecompass", capabilities: ["call_graph", "dependency_graph", "pointer_analysis"], healthy: false, message: "未配置" },
  { name: "gitnexus", capabilities: ["code_search", "dependency_graph"], healthy: false, message: "未配置" },
];

export default function ToolsPage() {
  const [tools, setTools] = useState<ToolInfo[]>([]);

  useEffect(() => {
    api.tools.list().then(setTools).catch(() => {});
  }, []);

  const registeredNames = new Set(tools.map((t) => t.name));
  const upcoming = comingSoonTools.filter((t) => !registeredNames.has(t.name));

  return (
    <div className="max-w-6xl mx-auto space-y-10 py-4">
      <header className="space-y-3">
        <h2 className="font-display text-2xl font-bold text-on-surface tracking-tight">
          分析工具 <span className="text-primary/40 text-sm font-normal ml-2">Analysis Arsenal</span>
        </h2>
        <p className="text-sm text-on-surface-variant max-w-2xl leading-relaxed">
          CodeTalks 编排外部分析工具。所有分析由工具本身执行 — CodeTalks
          仅管理执行流程并展示结果。
        </p>
      </header>

      <section className="space-y-6">
        <div className="flex items-center gap-4">
          <div className="h-px flex-1 bg-gradient-to-r from-outline-variant/50 to-transparent" />
          <span className="text-[10px] font-bold text-on-surface-variant">
            工具列表
          </span>
        </div>
        
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {tools.map((tool) => (
            <ToolCard
              key={tool.name}
              name={tool.name}
              description={toolDescriptions[tool.name] ?? ""}
              capabilities={tool.capabilities}
              healthy={tool.healthy}
            />
          ))}
          {upcoming.map((tool) => (
            <ToolCard
              key={tool.name}
              name={tool.name}
              description={toolDescriptions[tool.name] ?? ""}
              capabilities={tool.capabilities}
              healthy={false}
              comingSoon
            />
          ))}
        </div>
      </section>
    </div>
  );
}
