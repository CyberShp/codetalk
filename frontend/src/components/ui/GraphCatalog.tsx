"use client";

import { useEffect, useState, useMemo } from "react";
import { Search, Network, Layers, Activity, ChevronRight } from "lucide-react";
import GlassPanel from "./GlassPanel";
import { api } from "@/lib/api";
import type { GraphNode } from "@/lib/types";

interface Props {
  repo?: string;
  nodeMap: Map<string, GraphNode>;
  onNodeClick: (node: GraphNode) => void;
}

type ProcessItem = {
  id: string;
  label?: string;
  heuristicLabel?: string;
  processType?: string;
  stepCount?: number;
};

type ClusterItem = {
  id: string;
  label?: string;
  heuristicLabel?: string;
  symbolCount?: number;
  cohesion?: number;
  subCommunities?: number;
};

export default function GraphCatalog({ repo, nodeMap, onNodeClick }: Props) {
  const [activeTab, setActiveTab] = useState<"process" | "cluster">("process");
  const [searchQuery, setSearchQuery] = useState("");
  const [processes, setProcesses] = useState<ProcessItem[]>([]);
  const [clusters, setClusters] = useState<ClusterItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    Promise.allSettled([
      api.gitnexus.processes(repo),
      api.gitnexus.clusters(repo),
    ]).then(([procResult, clusterResult]) => {
      if (cancelled) return;
      if (procResult.status === "fulfilled") {
        const raw = procResult.value;
        setProcesses(Array.isArray(raw.processes) ? (raw.processes as ProcessItem[]) : []);
      }
      if (clusterResult.status === "fulfilled") {
        const raw = clusterResult.value;
        setClusters(Array.isArray(raw.clusters) ? (raw.clusters as ClusterItem[]) : []);
      }
      setLoading(false);
    });

    return () => { cancelled = true; };
  }, [repo]);

  const filteredItems = useMemo(() => {
    const q = searchQuery.toLowerCase();
    if (activeTab === "process") {
      return processes.filter(p => 
        (p.label || p.heuristicLabel || p.id).toLowerCase().includes(q)
      );
    } else {
      return clusters.filter(c => 
        (c.label || c.heuristicLabel || c.id).toLowerCase().includes(q)
      );
    }
  }, [activeTab, searchQuery, processes, clusters]);

  const handleClick = (id: string) => {
    const node = nodeMap.get(id);
    if (node) {
      onNodeClick(node);
    }
  };

  const renderLoading = () => (
    <div className="p-4 space-y-4">
      <div className="flex gap-2">
        <div className="h-8 w-1/2 bg-surface-container-high/30 rounded-full animate-pulse" />
        <div className="h-8 w-1/2 bg-surface-container-high/30 rounded-full animate-pulse" />
      </div>
      <div className="h-9 w-full bg-surface-container-high/20 rounded-lg animate-pulse" />
      <div className="space-y-2 mt-4">
        {[1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="h-14 bg-surface-container-high/20 rounded-xl animate-pulse" />
        ))}
      </div>
    </div>
  );

  return (
    <GlassPanel className="h-full flex flex-col p-0 overflow-hidden border-none shadow-none bg-transparent backdrop-blur-none">
      {/* Header & Tabs */}
      <div className="p-4 pb-2 shrink-0 space-y-3">
        <div className="flex bg-surface-container-low/40 p-1 rounded-full border border-white/5">
          <button
            onClick={() => setActiveTab("process")}
            className={`flex-1 flex items-center justify-center gap-2 py-1.5 text-[11px] font-bold tracking-wide transition-all rounded-full ${
              activeTab === "process"
                ? "bg-primary/20 text-primary shadow-sm shadow-primary/20"
                : "text-on-surface-variant/50 hover:text-on-surface-variant"
            }`}
          >
            <Activity className="w-3 h-3" />
            业务流程
          </button>
          <button
            onClick={() => setActiveTab("cluster")}
            className={`flex-1 flex items-center justify-center gap-2 py-1.5 text-[11px] font-bold tracking-wide transition-all rounded-full ${
              activeTab === "cluster"
                ? "bg-secondary/20 text-secondary shadow-sm shadow-secondary/20"
                : "text-on-surface-variant/50 hover:text-on-surface-variant"
            }`}
          >
            <Network className="w-3 h-3" />
            内聚社区
          </button>
        </div>

        {/* Search Bar */}
        <div className="relative group">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-on-surface-variant/40 group-focus-within:text-primary transition-colors" />
          <input
            type="text"
            placeholder={`搜索${activeTab === "process" ? "流程" : "社区"}...`}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-surface-container-high/30 border border-white/5 rounded-xl py-2 pl-9 pr-4 text-xs text-on-surface placeholder:text-on-surface-variant/30 focus:outline-none focus:ring-1 focus:ring-primary/30 transition-all"
          />
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-4 pb-4">
        {loading ? (
          renderLoading()
        ) : filteredItems.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 opacity-30">
            <Layers className="w-8 h-8 mb-2" />
            <p className="text-[11px]">未发现相关{activeTab === "process" ? "流程" : "社区"}</p>
          </div>
        ) : (
          <div className="space-y-1.5">
            {activeTab === "process" ? (
              (filteredItems as ProcessItem[]).map(p => (
                <ProcessCard 
                  key={p.id} 
                  item={p} 
                  inGraph={nodeMap.has(p.id)} 
                  onClick={() => handleClick(p.id)} 
                />
              ))
            ) : (
              (filteredItems as ClusterItem[]).map(c => (
                <ClusterCard 
                  key={c.id} 
                  item={c} 
                  inGraph={nodeMap.has(c.id)} 
                  onClick={() => handleClick(c.id)} 
                />
              ))
            )}
          </div>
        )}
      </div>
    </GlassPanel>
  );
}

function ProcessCard({ item, inGraph, onClick }: { item: ProcessItem; inGraph: boolean; onClick: () => void }) {
  const displayName = item.label || item.heuristicLabel || item.id;
  const isCross = item.processType === "cross_community";

  return (
    <div
      onClick={inGraph ? onClick : undefined}
      className={`relative p-3 rounded-xl border border-white/5 transition-all group ${
        inGraph 
          ? "cursor-pointer bg-surface-container-low/40 hover:bg-surface-container-high/60 hover:border-primary/20 hover:translate-x-1" 
          : "opacity-40 grayscale"
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="shrink-0 w-8 h-8 rounded-lg bg-[#EC4899]/10 border border-[#EC4899]/20 flex items-center justify-center text-[#EC4899]">
          <Activity className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-[13px] font-medium text-on-surface truncate group-hover:text-primary transition-colors">
              {displayName}
            </span>
            {isCross && (
              <span className="text-[8px] font-bold px-1.5 py-0.5 rounded-full bg-tertiary/20 text-tertiary border border-tertiary/30 uppercase tracking-tighter">
                CROSS
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-on-surface-variant/40 flex items-center gap-1 font-data">
              {item.stepCount || 0} 步骤
            </span>
          </div>
        </div>
        <ChevronRight className="w-3.5 h-3.5 text-on-surface-variant/20 group-hover:text-primary transition-colors self-center" />
      </div>
    </div>
  );
}

function ClusterCard({ item, inGraph, onClick }: { item: ClusterItem; inGraph: boolean; onClick: () => void }) {
  const displayName = item.label || item.heuristicLabel || item.id;

  return (
    <div
      onClick={inGraph ? onClick : undefined}
      className={`relative p-3 rounded-xl border border-white/5 transition-all group ${
        inGraph 
          ? "cursor-pointer bg-surface-container-low/40 hover:bg-surface-container-high/60 hover:border-secondary/20 hover:translate-x-1" 
          : "opacity-40 grayscale"
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="shrink-0 w-8 h-8 rounded-lg bg-[#6366F1]/10 border border-[#6366F1]/20 flex items-center justify-center text-[#6366F1]">
          <Network className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <span className="text-[13px] font-medium text-on-surface truncate block group-hover:text-secondary transition-colors mb-1">
            {displayName}
          </span>
          <div className="flex items-center gap-3">
            <span className="text-[10px] text-on-surface-variant/40 font-data">
              {item.symbolCount || 0} 符号
            </span>
            {item.cohesion != null && (
              <div className="flex items-center gap-1.5">
                <div className="w-8 h-1 bg-surface-container-high rounded-full overflow-hidden">
                  <div 
                    className={`h-full rounded-full ${item.cohesion >= 0.5 ? "bg-secondary/60" : "bg-tertiary/60"}`} 
                    style={{ width: `${item.cohesion * 100}%` }}
                  />
                </div>
                <span className={`text-[10px] font-data ${item.cohesion >= 0.5 ? "text-secondary/60" : "text-tertiary/60"}`}>
                  {(item.cohesion * 100).toFixed(0)}%
                </span>
              </div>
            )}
          </div>
        </div>
        <ChevronRight className="w-3.5 h-3.5 text-on-surface-variant/20 group-hover:text-secondary transition-colors self-center" />
      </div>
    </div>
  );
}
