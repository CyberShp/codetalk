"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import GlassPanel from "./GlassPanel";
import CyberInput from "./CyberInput";
import { api } from "@/lib/api";
import type { Repository, TaskType } from "@/lib/types";

const analysisTypes: {
  value: TaskType;
  label: string;
  description: string;
  disabled?: boolean;
}[] = [
  {
    value: "full_repo",
    label: "全量分析",
    description: "分析整个仓库的代码结构和架构",
  },
  {
    value: "file_paths",
    label: "指定文件夹",
    description: "仅分析指定路径下的文件（deepwiki 限定范围，GitNexus 仍为全量）",
  },
  {
    value: "mr_diff",
    label: "MR 分析（即将支持）",
    description: "MR 差异分析 adapter 开发中，当前暂不可用",
    disabled: true,
  },
];

interface Props {
  onClose: () => void;
  /** Pre-selected repo (from assets page). If null, show repo selector. */
  repositoryId?: string | null;
  /** Available repos. If not provided, loads from all projects. */
  repositories?: Repository[];
}

export default function NewAnalysisModal({
  onClose,
  repositoryId: initialRepoId = null,
  repositories: externalRepos,
}: Props) {
  const router = useRouter();
  const [repos, setRepos] = useState<Repository[]>(externalRepos ?? []);
  const [selectedRepo, setSelectedRepo] = useState(initialRepoId ?? "");
  const [taskType, setTaskType] = useState<TaskType>("full_repo");
  const [folderPath, setFolderPath] = useState("");
  const [aiEnabled, setAiEnabled] = useState(true);
  const [zoektEnabled, setZoektEnabled] = useState(false);
  const [zoektQuery, setZoektQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [llmAvailable, setLlmAvailable] = useState<boolean | null>(null);

  const loadRepos = useCallback(async () => {
    if (externalRepos) return;
    try {
      const projects = await api.projects.list();
      const allRepos: Repository[] = [];
      for (const p of projects) {
        const r = await api.projects.repos(p.id);
        allRepos.push(...r);
      }
      setRepos(allRepos);
      if (!selectedRepo && allRepos.length > 0) {
        setSelectedRepo(allRepos[0].id);
      }
    } catch {
      setError("无法加载仓库列表");
    }
  }, [externalRepos, selectedRepo]);

  useEffect(() => {
    loadRepos();
    const stored = localStorage.getItem("codetalks_ai_enabled");
    if (stored !== null) setAiEnabled(stored !== "false");
    api.settings.listLLM().then((configs) => {
      const hasUsable = configs.some((c) => c.has_api_key && c.base_url);
      setLlmAvailable(hasUsable);
      if (!hasUsable) setAiEnabled(false);
    }).catch(() => setLlmAvailable(false));
  }, [loadRepos]);

  const missingInput = taskType === "file_paths" && !folderPath.trim();

  const handleSubmit = async () => {
    if (!selectedRepo || missingInput) return;
    setSubmitting(true);
    setError("");

    const targetSpec: Record<string, unknown> = {};
    if (taskType === "file_paths" && folderPath.trim()) {
      targetSpec.files = [folderPath.trim()];
    }

    try {
      // deepwiki uses its own Ollama embedding — always include it,
      // independent of LLM config. ai_enabled only controls summary generation.
      const tools = ["deepwiki", "gitnexus"];
      if (zoektEnabled) {
        tools.push("zoekt");
        if (zoektQuery.trim()) {
          targetSpec.options = { ...(targetSpec.options as Record<string, unknown> ?? {}), query: zoektQuery.trim() };
        }
      }
      const task = await api.tasks.create({
        repository_id: selectedRepo,
        task_type: taskType,
        tools,
        ai_enabled: aiEnabled,
        target_spec: targetSpec,
      });
      router.push(`/tasks/${task.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建分析任务失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-surface/60 backdrop-blur-sm">
      <GlassPanel className="w-full max-w-lg">
        <h3 className="font-display text-sm font-semibold text-on-surface mb-5">
          新建分析
        </h3>

        {/* Repo Selector (only if no pre-selected repo) */}
        {!initialRepoId && (
          <div className="mb-4">
            <label className="block text-xs text-on-surface-variant mb-1.5">
              目标仓库
            </label>
            <select
              value={selectedRepo}
              onChange={(e) => setSelectedRepo(e.target.value)}
              className="w-full bg-surface-container-lowest/50 text-on-surface font-data text-sm px-4 py-2 rounded-md outline-none focus:ring-1 focus:ring-primary-container"
            >
              {repos.length === 0 && (
                <option value="">无可用仓库</option>
              )}
              {repos.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} ({r.source_type})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Analysis Type */}
        <div className="mb-4">
          <label className="block text-xs text-on-surface-variant mb-1.5">
            分析类型
          </label>
          <div className="flex gap-1 bg-surface-container-low rounded-lg p-1">
            {analysisTypes.map((t) => (
              <button
                key={t.value}
                onClick={() => !t.disabled && setTaskType(t.value)}
                disabled={t.disabled}
                className={`flex-1 px-3 py-1.5 text-xs rounded-md transition-colors ${
                  t.disabled
                    ? "text-on-surface-variant/30 cursor-not-allowed"
                    : taskType === t.value
                      ? "bg-surface-container-high text-on-surface"
                      : "text-on-surface-variant hover:text-on-surface"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <p className="text-[10px] text-on-surface-variant/60 mt-1.5">
            {analysisTypes.find((t) => t.value === taskType)?.description}
          </p>
        </div>

        {/* Type-specific input */}
        {taskType === "file_paths" && (
          <div className="mb-4">
            <CyberInput
              label="文件夹路径"
              placeholder="src/components"
              value={folderPath}
              onChange={(e) => setFolderPath(e.target.value)}
            />
          </div>
        )}

        {/* AI Toggle */}
        <div className="flex items-center justify-between mb-5 py-2">
          <div>
            <p className="text-sm text-on-surface">AI 摘要生成</p>
            <p className="text-[10px] text-on-surface-variant/60 mt-0.5">
              {llmAvailable === false
                ? "未检测到 LLM 配置，AI 摘要不可用。文档生成（deepwiki）和图谱分析不受影响。"
                : aiEnabled
                  ? "启用 AI 摘要。deepwiki 文档和 GitNexus 图谱始终运行。"
                  : "仅关闭 AI 摘要。deepwiki 文档和 GitNexus 图谱仍然运行。"}
            </p>
          </div>
          <button
            onClick={() => llmAvailable && setAiEnabled(!aiEnabled)}
            disabled={!llmAvailable}
            className={`relative w-11 h-6 rounded-full transition-colors duration-200 ${
              !llmAvailable
                ? "bg-surface-container-high opacity-40 cursor-not-allowed"
                : aiEnabled
                  ? "bg-primary-container"
                  : "bg-surface-container-high ring-1 ring-outline-variant"
            }`}
          >
            <span
              className={`absolute top-1 left-1 w-4 h-4 rounded-full transition-all duration-200 ${
                aiEnabled && llmAvailable
                  ? "translate-x-5 bg-primary"
                  : "bg-on-surface-variant"
              }`}
            />
          </button>
        </div>

        {/* Zoekt Code Search */}
        <div className="mb-5 border-t border-outline-variant/20 pt-4">
          <div className="flex items-center justify-between mb-2">
            <div>
              <p className="text-sm text-on-surface">代码搜索 (Zoekt)</p>
              <p className="text-[10px] text-on-surface-variant/60 mt-0.5">
                在仓库中精确搜索关键词、函数名或模式
              </p>
            </div>
            <button
              onClick={() => setZoektEnabled(!zoektEnabled)}
              className={`relative w-11 h-6 rounded-full transition-colors duration-200 ${
                zoektEnabled
                  ? "bg-secondary-container"
                  : "bg-surface-container-high ring-1 ring-outline-variant"
              }`}
            >
              <span
                className={`absolute top-1 left-1 w-4 h-4 rounded-full transition-all duration-200 ${
                  zoektEnabled ? "translate-x-5 bg-secondary" : "bg-on-surface-variant"
                }`}
              />
            </button>
          </div>
          {zoektEnabled && (
            <CyberInput
              label="搜索线索（可选）"
              placeholder="函数名、报错信息、路径片段……留空则仅建索引"
              value={zoektQuery}
              onChange={(e) => setZoektQuery(e.target.value)}
            />
          )}
        </div>

        {/* Error */}
        {error && (
          <p className="text-xs text-tertiary mb-3">{error}</p>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs text-on-surface-variant hover:text-on-surface"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || !selectedRepo || missingInput}
            className="px-4 py-2 text-sm font-medium rounded-md bg-primary-container text-primary hover:shadow-[0_0_12px_rgba(164,230,255,0.2)] transition-shadow disabled:opacity-40"
          >
            {submitting ? "创建中..." : "开始分析"}
          </button>
        </div>
      </GlassPanel>
    </div>
  );
}
