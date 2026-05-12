"use client";

import { useState, useEffect, useCallback } from "react";
import GlassPanel from "@/components/ui/GlassPanel";
import CyberInput from "@/components/ui/CyberInput";
import ComponentConfigPanel from "@/components/ComponentConfigPanel";
import { usePageRestoreRefresh } from "@/hooks/usePageRestoreRefresh";
import { api } from "@/lib/api";
import type { LLMConfig, ProxyMode, ConfigDomain } from "@/lib/types";

const LLM_PROVIDERS = [
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
  { value: "ollama", label: "Ollama" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "bedrock", label: "AWS Bedrock" },
  { value: "custom", label: "Custom (兼容 OpenAI)" },
] as const;

export default function SettingsPage() {
  const [aiEnabled, setAiEnabled] = useState(true);
  const [configs, setConfigs] = useState<LLMConfig[]>([]);
  const [showApiKey, setShowApiKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ id: string; success: boolean; message: string } | null>(null);

  // Embedding config state
  const [embeddingDomain, setEmbeddingDomain] = useState<ConfigDomain | null>(null);
  const [embeddingValues, setEmbeddingValues] = useState<Record<string, string>>({});
  const [embeddingOriginalValues, setEmbeddingOriginalValues] = useState<Record<string, string>>({});
  const [embeddingDirty, setEmbeddingDirty] = useState(false);
  const [embeddingSaving, setEmbeddingSaving] = useState(false);
  const [embeddingApplying, setEmbeddingApplying] = useState(false);
  const [embeddingFeedback, setEmbeddingFeedback] = useState<{ ok: boolean; msg: string } | null>(null);

  // Form state (shared by create and edit)
  const [editingId, setEditingId] = useState<string | null>(null);
  const [modelName, setModelName] = useState("");
  const [provider, setProvider] = useState("custom");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [proxyMode, setProxyMode] = useState<ProxyMode>("system");

  const loadConfigs = useCallback(async () => {
    try {
      const data = await api.settings.listLLM();
      setConfigs(data);
    } catch (e) {
      console.error("Failed to load LLM configs:", e);
    }
  }, []);

  const loadEmbedding = useCallback(async () => {
    try {
      const [contracts, statuses] = await Promise.all([
        api.components.contracts(),
        api.components.list(),
      ]);
      const deepwiki = contracts.find((c) => c.component === "deepwiki");
      const embDomain = deepwiki?.domains.find((d) => d.domain === "embedding");
      if (!embDomain) return;
      setEmbeddingDomain(embDomain);
      const status = statuses.find((s) => s.component === "deepwiki");
      const saved = status?.domains.find((d) => d.domain === "embedding");
      if (saved?.config) {
        setEmbeddingValues({ ...saved.config });
        setEmbeddingOriginalValues({ ...saved.config });
      }
    } catch {
      // embedding section is optional — ignore errors silently
    }
  }, []);

  const pollEmbeddingHealth = useCallback(() => {
    let attempt = 0;
    const tick = async () => {
      attempt++;
      try {
        const s = await api.components.health("deepwiki");
        if (s.healthy) {
          setEmbeddingFeedback({ ok: true, msg: "重启成功，服务已恢复在线" });
          return;
        }
      } catch { /* keep polling */ }
      if (attempt >= 8) {
        setEmbeddingFeedback({ ok: false, msg: "健康检查超时，服务可能仍在启动中" });
        return;
      }
      setTimeout(tick, 3000);
    };
    setTimeout(tick, 3000);
  }, []);

  useEffect(() => {
    void loadConfigs();
    void loadEmbedding();
    const stored = localStorage.getItem("codetalks_ai_enabled");
    if (stored !== null) setAiEnabled(stored === "true");
  }, [loadConfigs, loadEmbedding]);
  usePageRestoreRefresh(() => {
    void loadConfigs();
    void loadEmbedding();
  });

  const toggleAI = () => {
    const next = !aiEnabled;
    setAiEnabled(next);
    localStorage.setItem("codetalks_ai_enabled", String(next));
  };

  const resetForm = () => {
    setEditingId(null);
    setModelName("");
    setProvider("custom");
    setApiKey("");
    setBaseUrl("");
    setProxyMode("system");
    setShowApiKey(false);
  };

  const startEdit = (cfg: LLMConfig) => {
    setEditingId(cfg.id);
    setModelName(cfg.model_name);
    setProvider(cfg.provider || "custom");
    setApiKey("");
    setBaseUrl(cfg.base_url ?? "");
    setProxyMode(cfg.proxy_mode as ProxyMode);
    setShowApiKey(false);
  };

  const handleSave = async () => {
    if (!modelName.trim()) return;
    setSaving(true);
    try {
      if (editingId) {
        await api.settings.updateLLM(editingId, {
          provider,
          model_name: modelName.trim(),
          api_key: apiKey.trim() || undefined,
          base_url: baseUrl.trim() || undefined,
          proxy_mode: proxyMode,
        });
      } else {
        await api.settings.saveLLM({
          provider,
          model_name: modelName.trim(),
          api_key: apiKey.trim() || undefined,
          base_url: baseUrl.trim() || undefined,
          proxy_mode: proxyMode,
          is_default: true,
        });
      }
      resetForm();
      await loadConfigs();
    } catch (e) {
      console.error("Failed to save config:", e);
    } finally {
      setSaving(false);
    }
  };

  const handleSetDefault = async (id: string) => {
    try {
      await api.settings.setDefaultLLM(id);
      await loadConfigs();
    } catch (e) {
      console.error("Failed to set default:", e);
    }
  };

  const handleTest = async (id: string) => {
    setTestingId(id);
    setTestResult(null);
    try {
      const result = await api.settings.testLLM(id);
      setTestResult({ id, ...result });
    } catch (e) {
      setTestResult({ id, success: false, message: e instanceof Error ? e.message : "请求失败" });
    } finally {
      setTestingId(null);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.settings.deleteLLM(id);
      if (editingId === id) resetForm();
      await loadConfigs();
    } catch (e) {
      console.error("Failed to delete config:", e);
    }
  };

  const handleEmbeddingSave = async () => {
    if (!embeddingDomain) return;
    setEmbeddingSaving(true);
    setEmbeddingFeedback(null);
    try {
      const secretFieldNames = new Set(
        embeddingDomain.fields.filter((f) => f.field_type === "secret").map((f) => f.name)
      );
      const payload: Record<string, string> = {};
      for (const [key, value] of Object.entries(embeddingValues)) {
        if (secretFieldNames.has(key) && value === embeddingOriginalValues[key]) continue;
        payload[key] = value;
      }
      await api.components.saveConfig("deepwiki", "embedding", payload);
      await loadEmbedding();
      setEmbeddingDirty(false);
      setEmbeddingFeedback({ ok: true, msg: "配置已保存" });
      setTimeout(() => setEmbeddingFeedback(null), 3000);
    } catch (e) {
      setEmbeddingFeedback({ ok: false, msg: e instanceof Error ? e.message : "保存失败" });
    } finally {
      setEmbeddingSaving(false);
    }
  };

  const handleEmbeddingApplyRestart = async () => {
    setEmbeddingApplying(true);
    setEmbeddingFeedback(null);
    try {
      const result = await api.components.applyRestart("deepwiki");
      setEmbeddingFeedback({
        ok: result.success,
        msg: result.success ? `${result.message}，等待健康检查...` : result.message,
      });
      if (result.success) pollEmbeddingHealth();
    } catch (e) {
      setEmbeddingFeedback({ ok: false, msg: e instanceof Error ? e.message : "操作失败" });
    } finally {
      setEmbeddingApplying(false);
    }
  };

  return (
    <div className="space-y-6 max-w-3xl">
      <h2 className="font-display text-lg font-semibold text-on-surface">
        设置
      </h2>

      {/* AI Toggle */}
      <GlassPanel>
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium text-on-surface">
              AI 摘要生成
            </h3>
            <p className="text-xs text-on-surface-variant mt-1">
              启用 AI 驱动的分析结果摘要（通过 LLM 提供商）。
            </p>
          </div>
          <button
            onClick={toggleAI}
            className={`relative w-11 h-6 rounded-full transition-colors duration-200 ${
              aiEnabled
                ? "bg-primary-container"
                : "bg-surface-container-high ring-1 ring-outline-variant"
            }`}
          >
            <span
              className={`absolute top-1 left-1 w-4 h-4 rounded-full transition-all duration-200 ${
                aiEnabled
                  ? "translate-x-5 bg-primary"
                  : "bg-on-surface-variant"
              }`}
            />
          </button>
        </div>
      </GlassPanel>

      {/* AI 助手 Configuration */}
      <GlassPanel>
        <h3 className="text-sm font-medium text-on-surface mb-4">
          AI 助手
        </h3>

        {/* LLM Provider sub-section */}
        <div className="mb-1">
          <h4 className="text-[10px] font-medium text-on-surface-variant uppercase tracking-wider mb-1">
            LLM Provider
          </h4>
          <p className="text-xs text-on-surface-variant/60 mb-4">
            配置供 DeepWiki Chat 服务使用的 LLM 提供商
          </p>
        </div>

        {configs.map((cfg) => (
          <div
            key={cfg.id}
            className="bg-surface-container-lowest/50 rounded-lg px-4 py-3 mb-3"
          >
            <div className="flex items-center justify-between">
              <div className="min-w-0 flex-1">
                <p className="text-sm text-on-surface font-medium">
                  {cfg.model_name}
                </p>
                <div className="flex items-center gap-3 mt-1 flex-wrap">
                  <span className="font-data text-[10px] text-on-surface-variant/60 uppercase">
                    {cfg.provider}
                  </span>
                  {cfg.base_url && (
                    <span className="font-data text-[10px] text-primary-fixed-dim truncate max-w-[240px]">
                      {cfg.base_url}
                    </span>
                  )}
                  <span className={`text-[10px] ${cfg.has_api_key ? "text-secondary-fixed-dim" : "text-on-surface-variant/40"}`}>
                    API Key {cfg.has_api_key ? "已设置" : "未设置"}
                  </span>
                  <span className="text-[10px] text-on-surface-variant/60">
                    {cfg.proxy_mode === "system" ? "系统代理" : "直连"}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2 ml-3 shrink-0">
                {cfg.is_default ? (
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-primary/10 text-primary-fixed-dim">
                    默认
                  </span>
                ) : (
                  <button
                    onClick={() => handleSetDefault(cfg.id)}
                    className="text-[10px] px-2 py-0.5 rounded-full text-on-surface-variant hover:bg-primary/10 hover:text-primary-fixed-dim transition-colors"
                  >
                    设为默认
                  </button>
                )}
                <button
                  onClick={() => handleTest(cfg.id)}
                  disabled={testingId === cfg.id}
                  className="text-xs text-secondary-fixed-dim hover:text-secondary disabled:opacity-40"
                >
                  {testingId === cfg.id ? "测试中..." : "测试"}
                </button>
                <button
                  onClick={() => startEdit(cfg)}
                  className="text-xs text-primary-fixed-dim hover:text-primary"
                >
                  编辑
                </button>
                <button
                  onClick={() => handleDelete(cfg.id)}
                  className="text-xs text-tertiary hover:text-tertiary/80"
                >
                  删除
                </button>
              </div>
            </div>
            {testResult?.id === cfg.id && (
              <p className={`text-[11px] mt-2 ${testResult.success ? "text-secondary-fixed-dim" : "text-tertiary"}`}>
                {testResult.success ? "测试成功" : "测试失败"}：{testResult.message}
              </p>
            )}
          </div>
        ))}

        {/* Form: create or edit */}
        <div className="space-y-3 mt-4">
          {editingId && (
            <div className="flex items-center justify-between mb-1">
              <p className="text-xs text-primary-fixed-dim">
                编辑配置（留空 API Key 表示保持原值）
              </p>
              <button
                onClick={resetForm}
                className="text-xs text-on-surface-variant hover:text-on-surface"
              >
                取消编辑
              </button>
            </div>
          )}
          <div>
            <label className="block text-xs text-on-surface-variant mb-1.5 tracking-wide uppercase">
              Provider
            </label>
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              className="w-full bg-surface-container-lowest/50 text-on-surface font-data text-sm px-4 py-2 rounded-md outline-none focus:ring-1 focus:ring-primary-container"
            >
              {LLM_PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
          </div>
          <CyberInput
            label="Base URL"
            placeholder="http://10.0.0.1:8080/v1"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
          <div className="grid grid-cols-2 gap-4">
            <CyberInput
              label="模型名称"
              placeholder="gpt-4o / deepseek-v3 / qwen-72b"
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
            />
            <div>
              <label className="block text-xs text-on-surface-variant mb-1.5 tracking-wide uppercase">
                API Key
              </label>
              <div className="relative">
                <input
                  type={showApiKey ? "text" : "password"}
                  placeholder={editingId ? "留空保持原值" : "sk-..."}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  className="w-full bg-surface-container-lowest/50 text-on-surface font-data text-sm px-4 py-2 pr-12 rounded-md outline-none placeholder:text-on-surface-variant/40 focus:ring-1 focus:ring-primary-container"
                />
                <button
                  type="button"
                  onClick={() => setShowApiKey(!showApiKey)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-on-surface-variant hover:text-on-surface px-1.5 py-0.5 rounded"
                >
                  {showApiKey ? "隐藏" : "显示"}
                </button>
              </div>
            </div>
          </div>
          <div>
            <label className="block text-xs text-on-surface-variant mb-1.5">
              代理
            </label>
            <div className="flex gap-2">
              {([
                { value: "system" as const, label: "使用系统代理" },
                { value: "direct" as const, label: "直连（无代理）" },
              ]).map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setProxyMode(opt.value)}
                  className={`px-3 py-1.5 text-xs rounded-md transition-colors ${
                    proxyMode === opt.value
                      ? "bg-surface-container-high text-on-surface"
                      : "text-on-surface-variant hover:text-on-surface"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        </div>
        <button
          onClick={handleSave}
          disabled={saving || !modelName.trim()}
          className="mt-4 px-4 py-2 text-sm font-medium rounded-md bg-primary-container text-primary hover:shadow-[0_0_12px_rgba(164,230,255,0.2)] transition-shadow disabled:opacity-40"
        >
          {saving ? "保存中..." : editingId ? "更新配置" : "新增配置"}
        </button>

        {/* Embedding 配置 sub-section */}
        {embeddingDomain && (
          <div className="mt-6 pt-5 border-t border-outline-variant/20 space-y-3">
            <h4 className="text-[10px] font-medium text-on-surface-variant uppercase tracking-wider">
              Embedding 配置
            </h4>
            {embeddingDomain.fields.map((field) => {
              if (field.field_type === "select") {
                return (
                  <div key={field.name}>
                    <label className="block text-xs text-on-surface-variant mb-1.5">
                      {field.label}
                    </label>
                    <select
                      value={embeddingValues[field.name] ?? ""}
                      onChange={(e) => {
                        setEmbeddingValues((prev) => ({ ...prev, [field.name]: e.target.value }));
                        setEmbeddingDirty(true);
                      }}
                      className="w-full bg-surface-container-lowest/50 text-on-surface font-data text-sm px-4 py-2 rounded-md outline-none focus:ring-1 focus:ring-primary-container"
                    >
                      <option value="">选择...</option>
                      {field.options?.map((opt) => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                  </div>
                );
              }
              return (
                <div key={field.name}>
                  <label className="block text-xs text-on-surface-variant mb-1.5 tracking-wide uppercase">
                    {field.label}
                  </label>
                  <input
                    type={field.field_type === "secret" ? "password" : "text"}
                    placeholder={field.placeholder ?? ""}
                    value={embeddingValues[field.name] ?? ""}
                    onChange={(e) => {
                      setEmbeddingValues((prev) => ({ ...prev, [field.name]: e.target.value }));
                      setEmbeddingDirty(true);
                    }}
                    className="w-full bg-surface-container-lowest/50 text-on-surface font-data text-sm px-4 py-2 rounded-md outline-none placeholder:text-on-surface-variant/40 focus:ring-1 focus:ring-primary-container"
                  />
                </div>
              );
            })}
            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={handleEmbeddingSave}
                disabled={embeddingSaving || !embeddingDirty}
                className="px-3 py-1.5 text-xs font-medium rounded-md bg-surface-container-high text-on-surface hover:text-primary transition-colors disabled:opacity-40"
              >
                {embeddingSaving ? "保存中..." : "保存"}
              </button>
              <button
                onClick={handleEmbeddingApplyRestart}
                disabled={embeddingApplying}
                className="px-3 py-1.5 text-xs font-medium rounded-md bg-primary-container/80 text-primary hover:bg-primary-container transition-colors disabled:opacity-40"
              >
                {embeddingApplying ? "应用中..." : "应用并重启"}
              </button>
              {embeddingFeedback && (
                <span className={`text-[10px] ${embeddingFeedback.ok ? "text-secondary-fixed-dim" : "text-tertiary"}`}>
                  {embeddingFeedback.msg}
                </span>
              )}
            </div>
          </div>
        )}
      </GlassPanel>

      {/* Component Config */}
      <ComponentConfigPanel />
    </div>
  );
}
