"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  FolderSearch,
  Loader2,
  Upload,
} from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { TaskCreate } from "@/lib/types";

const AVAILABLE_TOOLS = [
  { id: "gitnexus", label: "GitNexus", desc: "知识图谱与代码搜索" },
  { id: "deepwiki", label: "DeepWiki", desc: "AI 文档生成" },
];

export default function NewTaskPage() {
  const router = useRouter();

  const [form, setForm] = useState<TaskCreate>({
    name: "",
    repo_path: "",
    tools: ["gitnexus", "deepwiki"],
  });
  const [requirementsFile, setRequirementsFile] = useState<string>("");
  const [designFile, setDesignFile] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateField = useCallback(
    <K extends keyof TaskCreate>(key: K, value: TaskCreate[K]) => {
      setForm((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const toggleTool = useCallback((toolId: string) => {
    setForm((prev) => {
      const has = prev.tools.includes(toolId);
      return {
        ...prev,
        tools: has
          ? prev.tools.filter((t) => t !== toolId)
          : [...prev.tools, toolId],
      };
    });
  }, []);

  const handleFileRead = useCallback(
    (
      file: File,
      setter: (value: string) => void,
    ) => {
      const reader = new FileReader();
      reader.onload = (e) => {
        const text = e.target?.result;
        if (typeof text === "string") {
          setter(text);
        }
      };
      reader.readAsText(file);
    },
    [],
  );

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!form.name.trim()) {
        setError("请输入任务名称");
        return;
      }
      if (!form.repo_path.trim()) {
        setError("请输入代码仓库路径");
        return;
      }
      if (form.tools.length === 0) {
        setError("请至少选择一个分析工具");
        return;
      }

      setSubmitting(true);
      setError(null);

      try {
        const payload: TaskCreate = {
          ...form,
          name: form.name.trim(),
          repo_path: form.repo_path.trim(),
        };
        if (requirementsFile) {
          payload.requirements_doc = requirementsFile;
        }
        if (designFile) {
          payload.design_doc = designFile;
        }

        const task = await api.tasks.create(payload);
        await api.tasks.run(task.id);
        router.push(`/tasks/${task.id}`);
      } catch (err: unknown) {
        const message =
          err instanceof Error ? err.message : "创建任务失败";
        setError(message);
      } finally {
        setSubmitting(false);
      }
    },
    [form, requirementsFile, designFile, router],
  );

  return (
    <div className="max-w-2xl">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Link
          href="/"
          className="p-1.5 rounded-lg hover:bg-surface-container text-on-surface-variant hover:text-on-surface transition-colors"
        >
          <ArrowLeft size={18} />
        </Link>
        <div>
          <h1 className="font-display text-2xl font-bold text-on-surface">
            新建分析
          </h1>
          <p className="text-sm text-on-surface-variant mt-0.5">
            配置代码分析任务参数
          </p>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-6 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Form */}
      <form onSubmit={handleSubmit} className="space-y-5">
        {/* Task Name */}
        <div>
          <label className="block text-sm font-medium text-on-surface mb-1.5">
            任务名称
          </label>
          <input
            type="text"
            value={form.name}
            onChange={(e) => updateField("name", e.target.value)}
            placeholder="例如：项目 A 安全分析"
            className="w-full px-4 py-2.5 bg-surface-container border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors"
          />
        </div>

        {/* Repo Path */}
        <div>
          <label className="block text-sm font-medium text-on-surface mb-1.5">
            代码仓库路径
          </label>
          <div className="relative">
            <FolderSearch
              size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant/50"
            />
            <input
              type="text"
              value={form.repo_path}
              onChange={(e) => updateField("repo_path", e.target.value)}
              placeholder="本地文件夹路径，如 /home/user/project"
              className="w-full pl-10 pr-4 py-2.5 bg-surface-container border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors font-data"
            />
          </div>
        </div>

        {/* Tool Selection */}
        <div>
          <label className="block text-sm font-medium text-on-surface mb-2">
            分析工具
          </label>
          <div className="grid grid-cols-2 gap-3">
            {AVAILABLE_TOOLS.map((tool) => {
              const selected = form.tools.includes(tool.id);
              return (
                <button
                  key={tool.id}
                  type="button"
                  onClick={() => toggleTool(tool.id)}
                  className={`flex flex-col items-start px-4 py-3 rounded-lg border text-left transition-colors ${
                    selected
                      ? "bg-primary/10 border-primary/30 text-primary"
                      : "bg-surface-container border-outline-variant/30 text-on-surface-variant hover:border-outline-variant/60"
                  }`}
                >
                  <span className="text-sm font-medium">{tool.label}</span>
                  <span className="text-xs mt-0.5 opacity-70">
                    {tool.desc}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* File Uploads */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-on-surface mb-1.5">
              需求文档
              <span className="text-on-surface-variant font-normal ml-1">
                (可选)
              </span>
            </label>
            <label className="flex items-center gap-2 px-4 py-2.5 bg-surface-container border border-outline-variant/30 rounded-lg cursor-pointer hover:border-outline-variant/60 transition-colors">
              <Upload size={14} className="text-on-surface-variant" />
              <span className="text-sm text-on-surface-variant truncate">
                {requirementsFile ? "已上传" : "点击选择文件"}
              </span>
              <input
                type="file"
                accept=".txt,.md,.doc,.docx"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) handleFileRead(file, setRequirementsFile);
                }}
              />
            </label>
          </div>
          <div>
            <label className="block text-sm font-medium text-on-surface mb-1.5">
              设计文档
              <span className="text-on-surface-variant font-normal ml-1">
                (可选)
              </span>
            </label>
            <label className="flex items-center gap-2 px-4 py-2.5 bg-surface-container border border-outline-variant/30 rounded-lg cursor-pointer hover:border-outline-variant/60 transition-colors">
              <Upload size={14} className="text-on-surface-variant" />
              <span className="text-sm text-on-surface-variant truncate">
                {designFile ? "已上传" : "点击选择文件"}
              </span>
              <input
                type="file"
                accept=".txt,.md,.doc,.docx"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) handleFileRead(file, setDesignFile);
                }}
              />
            </label>
          </div>
        </div>

        {/* Submit */}
        <div className="pt-2">
          <button
            type="submit"
            disabled={submitting}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-primary text-on-primary font-medium rounded-lg hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                创建中...
              </>
            ) : (
              "开始分析"
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
