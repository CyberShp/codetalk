"use client";

import { useState, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  FolderSearch,
  Loader2,
  Upload,
  Save,
  FilePlus,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { TaskCreate, PromptTemplate } from "@/lib/types";

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
  const [deepwikiDepth, setDeepwikiDepth] = useState<"fast" | "balanced" | "deep">("balanced");
  const [analysisFocus, setAnalysisFocus] = useState("");
  const [requirementsFile, setRequirementsFile] = useState("");
  const [designFile, setDesignFile] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [editedContent, setEditedContent] = useState("");
  const [showSaveAs, setShowSaveAs] = useState(false);
  const [saveAsName, setSaveAsName] = useState("");
  const [saving, setSaving] = useState(false);
  const [docsExpanded, setDocsExpanded] = useState(false);

  useEffect(() => {
    api.prompts.list().then((list) => {
      setTemplates(list);
      const system = list.find((t) => t.is_system);
      if (system) {
        setSelectedTemplateId(system.id);
        setEditedContent(system.content);
      } else if (list.length > 0) {
        setSelectedTemplateId(list[0].id);
        setEditedContent(list[0].content);
      }
    }).catch(() => {
      setError("提示词模板加载失败，请刷新页面重试");
    });
  }, []);

  const handleTemplateChange = useCallback(
    (id: string) => {
      setSelectedTemplateId(id);
      const tpl = templates.find((t) => t.id === id);
      if (tpl) setEditedContent(tpl.content);
    },
    [templates],
  );

  const selectedTemplate = templates.find((t) => t.id === selectedTemplateId);
  const isSystem = selectedTemplate?.is_system ?? false;

  const handleSaveTemplate = useCallback(async () => {
    if (isSystem || !selectedTemplateId) return;
    setSaving(true);
    try {
      const updated = await api.prompts.update(selectedTemplateId, {
        content: editedContent,
      });
      setTemplates((prev) =>
        prev.map((t) => (t.id === updated.id ? updated : t)),
      );
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "保存模板失败");
    } finally {
      setSaving(false);
    }
  }, [isSystem, selectedTemplateId, editedContent]);

  const handleSaveAsNew = useCallback(async () => {
    if (!saveAsName.trim()) return;
    setSaving(true);
    try {
      const created = await api.prompts.create({
        name: saveAsName.trim(),
        content: editedContent,
      });
      setTemplates((prev) => [...prev, created]);
      setSelectedTemplateId(created.id);
      setShowSaveAs(false);
      setSaveAsName("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "保存模板失败");
    } finally {
      setSaving(false);
    }
  }, [saveAsName, editedContent]);

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
    (file: File, setter: (value: string) => void) => {
      const reader = new FileReader();
      reader.onload = (e) => {
        const text = e.target?.result;
        if (typeof text === "string") setter(text);
      };
      reader.onerror = () => {
        setError(`文件「${file.name}」读取失败，请重试`);
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
      if (!analysisFocus.trim()) {
        setError("请输入分析内容");
        return;
      }
      if (form.tools.length === 0) {
        setError("请至少选择一个分析工具");
        return;
      }

      setSubmitting(true);
      setError(null);

      try {
        let rendered = editedContent.replace(
          /\{analysis_focus\}/g,
          analysisFocus.trim(),
        );
        if (!rendered.includes(analysisFocus.trim())) {
          rendered = `## 分析目标\n${analysisFocus.trim()}\n\n${rendered}`;
        }

        const payload: TaskCreate = {
          ...form,
          name: form.name.trim(),
          repo_path: form.repo_path.trim(),
          analysis_focus: analysisFocus.trim(),
          prompt_content: rendered,
          deepwiki_depth: deepwikiDepth,
        };
        if (requirementsFile) payload.requirements_doc = requirementsFile;
        if (designFile) payload.design_doc = designFile;

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
    [form, analysisFocus, editedContent, requirementsFile, designFile, deepwikiDepth, router],
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

        {/* Analysis Focus (required) */}
        <div>
          <label className="block text-sm font-medium text-on-surface mb-1.5">
            分析内容
            <span className="text-red-400 ml-0.5">*</span>
          </label>
          <textarea
            value={analysisFocus}
            onChange={(e) => setAnalysisFocus(e.target.value)}
            placeholder="描述你的分析目标，例如：针对 NVMe TCP TLS 模块进行安全性和性能分析"
            rows={3}
            className="w-full px-4 py-2.5 bg-surface-container border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors resize-y"
          />
          <p className="text-xs text-on-surface-variant/60 mt-1">
            此内容将替换模板中的 {"{analysis_focus}"} 占位符
          </p>
        </div>

        {/* Prompt Template */}
        <div>
          <label className="block text-sm font-medium text-on-surface mb-1.5">
            提示词模板
          </label>

          {/* Template Selector */}
          <select
            value={selectedTemplateId}
            onChange={(e) => handleTemplateChange(e.target.value)}
            className="w-full px-4 py-2.5 bg-surface-container border border-outline-variant/30 rounded-lg text-sm text-on-surface focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors"
          >
            {templates.map((tpl) => (
              <option key={tpl.id} value={tpl.id}>
                {tpl.name}
                {tpl.is_system ? "（系统默认）" : ""}
              </option>
            ))}
          </select>

          {/* Editable Template Content */}
          <textarea
            value={editedContent}
            onChange={(e) => setEditedContent(e.target.value)}
            rows={10}
            className="w-full mt-2 px-4 py-2.5 bg-surface-container border border-outline-variant/30 rounded-lg text-xs text-on-surface font-data focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors resize-y leading-relaxed"
          />

          {/* Template Save Actions */}
          <div className="flex items-center gap-2 mt-2">
            {!isSystem && selectedTemplateId && (
              <button
                type="button"
                onClick={handleSaveTemplate}
                disabled={saving}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-on-surface-variant hover:text-on-surface bg-surface-container-high rounded-lg border border-outline-variant/30 hover:border-outline-variant/60 transition-colors disabled:opacity-50"
              >
                <Save size={12} />
                保存模板
              </button>
            )}
            <button
              type="button"
              onClick={() => setShowSaveAs(!showSaveAs)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-on-surface-variant hover:text-on-surface bg-surface-container-high rounded-lg border border-outline-variant/30 hover:border-outline-variant/60 transition-colors"
            >
              <FilePlus size={12} />
              保存为新模板
            </button>
          </div>

          {/* Save-As Dialog */}
          {showSaveAs && (
            <div className="flex items-center gap-2 mt-2">
              <input
                type="text"
                value={saveAsName}
                onChange={(e) => setSaveAsName(e.target.value)}
                placeholder="输入新模板名称"
                className="flex-1 px-3 py-1.5 bg-surface-container border border-outline-variant/30 rounded-lg text-xs text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors"
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleSaveAsNew();
                  }
                }}
              />
              <button
                type="button"
                onClick={handleSaveAsNew}
                disabled={saving || !saveAsName.trim()}
                className="px-3 py-1.5 text-xs font-medium bg-primary text-on-primary rounded-lg hover:opacity-90 transition-opacity disabled:opacity-50"
              >
                {saving ? "保存中..." : "确定"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowSaveAs(false);
                  setSaveAsName("");
                }}
                className="px-3 py-1.5 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
              >
                取消
              </button>
            </div>
          )}
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

        {/* DeepWiki Analysis Depth */}
        {form.tools.includes("deepwiki") && (
          <div>
            <label className="block text-sm font-medium text-on-surface mb-2">
              DeepWiki 分析深度
            </label>
            <div className="grid grid-cols-3 gap-3">
              {(
                [
                  { value: "fast", label: "快速", desc: "核心架构概览" },
                  { value: "balanced", label: "均衡", desc: "架构 + 组件 + 数据流" },
                  { value: "deep", label: "深度", desc: "全面详尽文档" },
                ] as const
              ).map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  aria-pressed={deepwikiDepth === opt.value}
                  onClick={() => setDeepwikiDepth(opt.value)}
                  className={`flex flex-col items-start px-4 py-3 rounded-lg border text-left transition-colors ${
                    deepwikiDepth === opt.value
                      ? "bg-primary/10 border-primary/30 text-primary"
                      : "bg-surface-container border-outline-variant/30 text-on-surface-variant hover:border-outline-variant/60"
                  }`}
                >
                  <span className="text-sm font-medium">{opt.label}</span>
                  <span className="text-xs mt-0.5 opacity-70">{opt.desc}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Collapsible File Uploads */}
        <div>
          <button
            type="button"
            onClick={() => setDocsExpanded(!docsExpanded)}
            className="flex items-center gap-2 text-sm font-medium text-on-surface-variant hover:text-on-surface transition-colors"
          >
            {docsExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            附加文档（可选）
          </button>
          {docsExpanded && (
            <div className="grid grid-cols-2 gap-4 mt-3">
              <div>
                <label className="block text-sm font-medium text-on-surface mb-1.5">
                  需求文档
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
          )}
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
