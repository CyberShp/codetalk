"use client";

import { useState, useEffect } from "react";
import type { GraphNode, FileSlice } from "@/lib/types";
import { api } from "@/lib/api";
import GlassPanel from "./GlassPanel";

interface Props {
  node: GraphNode;
  repoName: string;
}

export default function CodePanel({ node, repoName }: Props) {
  const [code, setCode] = useState<FileSlice | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const { filePath, startLine, endLine } = node.properties;
  const hasCodeRange = filePath && startLine !== undefined && endLine !== undefined;

  useEffect(() => {
    if (!hasCodeRange || !repoName) return;

    setLoading(true);
    setError("");
    api.gitnexus
      .getFile(repoName, filePath!, startLine, endLine)
      .then(setCode)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [node.id, repoName, filePath, startLine, endLine, hasCodeRange]);

  return (
    <GlassPanel>
      {/* Node header */}
      <div className="flex items-center gap-2 mb-3">
        <span className="px-2 py-0.5 rounded text-[10px] font-data bg-primary/10 text-primary">
          {node.label}
        </span>
        <h4 className="text-sm font-medium text-on-surface truncate">
          {node.properties.name}
        </h4>
      </div>

      {/* Location */}
      {filePath && (
        <p className="text-xs text-on-surface-variant font-data mb-3 truncate">
          {filePath}
          {startLine !== undefined && `:${startLine}`}
          {endLine !== undefined && `-${endLine}`}
        </p>
      )}

      {/* Code content */}
      {hasCodeRange ? (
        loading ? (
          <div className="text-xs text-on-surface-variant/50 py-4">
            Loading function code...
          </div>
        ) : error ? (
          <div className="text-xs text-tertiary py-2">{error}</div>
        ) : code ? (
          <div className="bg-surface-container-lowest/60 rounded-md overflow-auto max-h-[400px]">
            <pre className="p-3 text-[11px] leading-relaxed font-data text-on-surface/80">
              <code>
                {code.content.split("\n").map((line, i) => (
                  <div key={i} className="flex">
                    <span className="inline-block w-8 text-right mr-3 text-on-surface-variant/30 select-none">
                      {code.startLine + i}
                    </span>
                    <span>{line}</span>
                  </div>
                ))}
              </code>
            </pre>
          </div>
        ) : null
      ) : (
        <p className="text-xs text-on-surface-variant/50 py-2">
          No function-level context for this node type.
        </p>
      )}
    </GlassPanel>
  );
}
