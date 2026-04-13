"use client";

import { useState, useEffect } from "react";
import ToolCard from "@/components/ui/ToolCard";
import { api } from "@/lib/api";
import type { ToolInfo } from "@/lib/types";

const toolDescriptions: Record<string, string> = {
  deepwiki:
    "AI-powered repository documentation and knowledge graph generation using RAG.",
  zoekt:
    "Fast trigram-based code search engine for large codebases.",
  joern:
    "Code property graph analysis for C/C++ \u2014 call graphs, taint analysis, security scanning.",
  codecompass:
    "Code comprehension tool for C/C++ \u2014 call graphs, dependency analysis, pointer analysis.",
  gitnexus:
    "Git-native code search and cross-repository dependency tracking.",
};

const comingSoonTools: ToolInfo[] = [
  { name: "zoekt", capabilities: ["code_search"], healthy: false, message: "Not configured" },
  { name: "joern", capabilities: ["call_graph", "taint_analysis", "security_scan", "ast_analysis"], healthy: false, message: "Not configured" },
  { name: "codecompass", capabilities: ["call_graph", "dependency_graph", "pointer_analysis"], healthy: false, message: "Not configured" },
  { name: "gitnexus", capabilities: ["code_search", "dependency_graph"], healthy: false, message: "Not configured" },
];

export default function ToolsPage() {
  const [tools, setTools] = useState<ToolInfo[]>([]);

  useEffect(() => {
    api.tools.list().then(setTools).catch(() => {});
  }, []);

  const registeredNames = new Set(tools.map((t) => t.name));
  const upcoming = comingSoonTools.filter((t) => !registeredNames.has(t.name));

  return (
    <div className="space-y-6">
      <h2 className="font-display text-lg font-semibold text-on-surface">
        Analysis Tools
      </h2>
      <p className="text-sm text-on-surface-variant">
        CodeTalks orchestrates external analysis tools. All analysis is
        performed by the tools themselves \u2014 CodeTalks only manages execution and
        displays results.
      </p>

      <div className="grid grid-cols-3 gap-4">
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
    </div>
  );
}
