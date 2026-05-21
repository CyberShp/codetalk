"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Plus,
  Trash2,
  Pencil,
  Loader2,
  Save,
  TestTube2,
  Eye,
  EyeOff,
  ChevronDown,
  ChevronUp,
  Globe,
  ShieldCheck,
  Bot,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  LLMConfig,
  LLMConfigCreate,
  GeneralSettings,
  ApiType,
} from "@/lib/types";

const EMPTY_LLM_FORM: LLMConfigCreate = {
  name: "",
  api_type: "openai_compat",
  base_url: "",
  api_key: "",
  model: "",
  max_tokens: 4096,
  temperature: 0.3,
  is_chat_model: true,
  is_embedding_model: false,
};

export default function SettingsPage() {
  const [configs, setConfigs] = useState<LLMConfig[]>([]);
  const [general, setGeneral] = useState<GeneralSettings>({
    proxy_mode: "none",
    proxy_url: "",
    ssl_cert_path: "",
    active_chat_model_id: "",
    active_embedding_model_id: "",
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<LLMConfigCreate>({ ...EMPTY_LLM_FORM });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [showApiKey, setShowApiKey] = useState(false);
  const [savingGeneral, setSavingGeneral] = useState(false);
  const [showGeneral, setShowGeneral] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [llmList, generalData] = await Promise.all([
        api.settings.listLLM(),
        api.settings.getGeneral().catch(
          () =>
            ({
              proxy_mode: "none",
              proxy_url: "",
              ssl_cert_path: "",
              active_chat_model_id: "",
              active_embedding_model_id: "",
            }) as GeneralSettings,
        ),
      ]);
      setConfigs(llmList);
      setGeneral(generalData);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "加载设置失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const updateForm = useCallback(
    <K extends keyof LLMConfigCreate>(key: K, value: LLMConfigCreate[K]) => {
      setForm((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const handleSaveLLM = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!form.name.trim() || !form.base_url.trim() || !form.model.trim()) {
        setError("请填写名称、接口地址和模型名称");
        return;
      }
      setSaving(true);
      setError(null);
      try {
        if (editingId) {
          const payload = { ...form };
          if (!payload.api_key) delete (payload as Record<string, unknown>).api_key;
          await api.settings.updateLLM(editingId, payload);
        } else {
          const newConfig = await api.settings.createLLM(form);
          if (!general.active_chat_model_id && form.is_chat_model) {
            const updated = { ...general, active_chat_model_id: newConfig.id };
            await api.settings.updateGeneral(updated);
            setGeneral(updated);
          }
        }
        setForm({ ...EMPTY_LLM_FORM });
        setEditingId(null);
        setShowForm(false);
        await loadData();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "保存配置失败");
      } finally {
        setSaving(false);
      }
    },
    [form, editingId, general, loadData],
  );

  const handleEditLLM = useCallback(
    (cfg: LLMConfig) => {
      setForm({
        name: cfg.name,
        api_type: cfg.api_type,
        base_url: cfg.base_url,
        api_key: "",
        model: cfg.model,
        max_tokens: cfg.max_tokens,
        temperature: cfg.temperature,
        is_chat_model: cfg.is_chat_model,
        is_embedding_model: cfg.is_embedding_model,
      });
      setEditingId(cfg.id);
      setShowForm(true);
      setTestResult(null);
    },
    [],
  );

  const handleTestLLM = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.settings.testLLM(form);
      setTestResult(result.message);
    } catch (err: unknown) {
      setTestResult(err instanceof Error ? err.message : "测试失败");
    } finally {
      setTesting(false);
    }
  }, [form]);

  const handleDeleteLLM = useCallback(
    async (id: string) => {
      if (!confirm("确定要删除此配置吗？")) return;
      try {
        await api.settings.deleteLLM(id);
        await loadData();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "删除失败");
      }
    },
    [loadData],
  );

  const handleSaveGeneral = useCallback(async () => {
    setSavingGeneral(true);
    setError(null);
    try {
      await api.settings.updateGeneral(general);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "保存通用设置失败");
    } finally {
      setSavingGeneral(false);
    }
  }, [general]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-on-surface-variant">
        <Loader2 size={20} className="animate-spin mr-2" />
        加载设置...
      </div>
    );
  }

  return (
    <div className="max-w-3xl">
      <h1 className="font-display text-2xl font-bold text-on-surface mb-1">
        设置
      </h1>
      <p className="text-sm text-on-surface-variant mb-6">
        管理 LLM 配置与通用设置
      </p>

      {error && (
        <div id="settings-error" role="alert" className="mb-6 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          {error}
        </div>
      )}

      <div className="mb-8">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-medium text-on-surface flex items-center gap-2">
            <Bot size={18} />
            LLM 配置
          </h2>
          <button
            onClick={() => {
              if (showForm) {
                setShowForm(false);
                setEditingId(null);
                setForm({ ...EMPTY_LLM_FORM });
              } else {
                setEditingId(null);
                setForm({ ...EMPTY_LLM_FORM });
                setShowForm(true);
              }
            }}
            className="flex items-center gap-2 px-3 py-1.5 text-sm bg-primary text-on-primary rounded-lg hover:opacity-90 transition-opacity"
          >
            <Plus size={14} />
            新增
          </button>
        </div>

        {showForm && (
          <form
            onSubmit={handleSaveLLM}
            aria-describedby={error ? "settings-error" : undefined}
            className="bg-surface-container rounded-xl border border-outline-variant/20 p-5 mb-4 space-y-4"
          >
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-on-surface-variant mb-1">
                  配置名称
                </label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => updateForm("name", e.target.value)}
                  placeholder="如：Claude / GPT-4o"
                  className="w-full px-3 py-2 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 transition-colors"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-on-surface-variant mb-1">
                  协议类型
                </label>
                <select
                  value={form.api_type}
                  onChange={(e) =>
                    updateForm("api_type", e.target.value as ApiType)
                  }
                  className="w-full px-3 py-2 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface focus:outline-none focus:border-primary/50 transition-colors"
                >
                  <option value="openai_compat">OpenAI 兼容</option>
                  <option value="anthropic">Anthropic</option>
                </select>
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-on-surface-variant mb-1">
                接口地址 (Base URL)
              </label>
              <input
                type="url"
                value={form.base_url}
                onChange={(e) => updateForm("base_url", e.target.value)}
                placeholder="https://api.openai.com/v1"
                className="w-full px-3 py-2 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 transition-colors font-data"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-on-surface-variant mb-1">
                API 密钥
              </label>
              <div className="relative">
                <input
                  type={showApiKey ? "text" : "password"}
                  value={form.api_key}
                  onChange={(e) => updateForm("api_key", e.target.value)}
                  placeholder={editingId ? "留空则保持原密钥不变" : "sk-...（Ollama 等本地模型可留空）"}
                  className="w-full px-3 py-2 pr-10 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 transition-colors font-data"
                />
                <button
                  type="button"
                  onClick={() => setShowApiKey(!showApiKey)}
                  aria-label={showApiKey ? "隐藏 API 密钥" : "显示 API 密钥"}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-on-surface-variant hover:text-on-surface"
                >
                  {showApiKey ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            <div className={`grid ${form.is_embedding_model && !form.is_chat_model ? "" : "grid-cols-3"} gap-4`}>
              <div>
                <label className="block text-xs font-medium text-on-surface-variant mb-1">
                  模型名称
                </label>
                <input
                  type="text"
                  value={form.model}
                  onChange={(e) => updateForm("model", e.target.value)}
                  placeholder={form.is_embedding_model && !form.is_chat_model ? "text-embedding-3-small" : "gpt-4o"}
                  className="w-full px-3 py-2 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 transition-colors font-data"
                />
              </div>
              {!(form.is_embedding_model && !form.is_chat_model) && (
                <>
                  <div>
                    <label className="block text-xs font-medium text-on-surface-variant mb-1">
                      最大 Tokens
                    </label>
                    <input
                      type="number"
                      value={form.max_tokens}
                      onChange={(e) =>
                        updateForm("max_tokens", parseInt(e.target.value, 10) || 4096)
                      }
                      className="w-full px-3 py-2 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface focus:outline-none focus:border-primary/50 transition-colors font-data"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-on-surface-variant mb-1">
                      温度
                    </label>
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      max="2"
                      value={form.temperature}
                      onChange={(e) =>
                        updateForm("temperature", parseFloat(e.target.value) || 0.3)
                      }
                      className="w-full px-3 py-2 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface focus:outline-none focus:border-primary/50 transition-colors font-data"
                    />
                  </div>
                </>
              )}
            </div>

            <div className="flex items-center gap-6">
              <label className="flex items-center gap-2 text-sm text-on-surface-variant cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.is_chat_model}
                  onChange={(e) => updateForm("is_chat_model", e.target.checked)}
                  className="rounded border-outline-variant/30"
                />
                对话模型
              </label>
              <label className="flex items-center gap-2 text-sm text-on-surface-variant cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.is_embedding_model}
                  onChange={(e) =>
                    updateForm("is_embedding_model", e.target.checked)
                  }
                  className="rounded border-outline-variant/30"
                />
                嵌入模型
              </label>
            </div>

            {testResult && (
              <div className="px-3 py-2 bg-primary/10 border border-primary/20 rounded-lg text-xs text-primary">
                {testResult}
              </div>
            )}

            <div className="flex gap-3 pt-1">
              <button
                type="button"
                onClick={handleTestLLM}
                disabled={testing}
                className="flex items-center gap-2 px-4 py-2 text-sm bg-surface-container-high text-on-surface rounded-lg border border-outline-variant/30 hover:bg-surface-container transition-colors disabled:opacity-50"
              >
                {testing ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <TestTube2 size={14} />
                )}
                测试连接
              </button>
              <button
                type="submit"
                disabled={saving}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2 text-sm bg-primary text-on-primary font-medium rounded-lg hover:opacity-90 transition-opacity disabled:opacity-50"
              >
                {saving ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Save size={14} />
                )}
                {editingId ? "更新配置" : "保存配置"}
              </button>
            </div>
          </form>
        )}

        {configs.length === 0 ? (
          <div className="text-center py-10 bg-surface-container rounded-xl border border-outline-variant/20">
            <Bot size={24} className="mx-auto text-on-surface-variant/40 mb-2" />
            <p className="text-sm text-on-surface-variant">
              还没有配置 LLM，点击上方「新增」添加
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {configs.map((cfg) => (
              <div
                key={cfg.id}
                className="flex items-center gap-4 bg-surface-container rounded-xl border border-outline-variant/20 px-5 py-4"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-medium text-on-surface truncate">
                      {cfg.name}
                    </p>
                    <span className="text-[10px] px-1.5 py-0.5 bg-surface-container-high rounded text-on-surface-variant">
                      {cfg.api_type === "anthropic" ? "Anthropic" : "OpenAI"}
                    </span>
                    {cfg.is_chat_model && (
                      <span className="text-[10px] px-1.5 py-0.5 bg-blue-400/10 rounded text-blue-400">
                        对话
                      </span>
                    )}
                    {cfg.is_embedding_model && (
                      <span className="text-[10px] px-1.5 py-0.5 bg-green-400/10 rounded text-green-400">
                        嵌入
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-on-surface-variant mt-0.5 font-data truncate">
                    {cfg.model} · {cfg.base_url}
                  </p>
                </div>
                <button
                  onClick={() => handleEditLLM(cfg)}
                  className="p-2 rounded-lg text-on-surface-variant hover:bg-primary/10 hover:text-primary transition-colors"
                  title="编辑"
                >
                  <Pencil size={14} />
                </button>
                <button
                  onClick={() => handleDeleteLLM(cfg.id)}
                  className="p-2 rounded-lg text-red-400 hover:bg-red-400/10 transition-colors"
                  title="删除"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Active model selector — always visible */}
      {configs.filter((c) => c.is_chat_model).length > 0 && (
        <div className="mb-8 bg-surface-container rounded-xl border border-outline-variant/20 px-5 py-4">
          <label className="block text-xs font-medium text-on-surface-variant mb-2">
            活跃聊天模型
          </label>
          <div className="flex items-center gap-3">
            <select
              value={general.active_chat_model_id}
              onChange={async (e) => {
                const prev = general;
                const updated = { ...general, active_chat_model_id: e.target.value };
                setGeneral(updated);
                try {
                  await api.settings.updateGeneral(updated);
                } catch {
                  setGeneral(prev);
                  setError("保存活跃模型失败");
                }
              }}
              className="flex-1 px-3 py-2 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface focus:outline-none focus:border-primary/50 transition-colors"
            >
              <option value="">请选择活跃的聊天模型</option>
              {configs
                .filter((c) => c.is_chat_model)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.model})
                  </option>
                ))}
            </select>
          </div>
        </div>
      )}

      <div>
        <button
          onClick={() => setShowGeneral(!showGeneral)}
          className="flex items-center justify-between w-full text-left mb-4"
        >
          <h2 className="text-base font-medium text-on-surface flex items-center gap-2">
            <Globe size={18} />
            通用设置
          </h2>
          {showGeneral ? (
            <ChevronUp size={16} className="text-on-surface-variant" />
          ) : (
            <ChevronDown size={16} className="text-on-surface-variant" />
          )}
        </button>

        {showGeneral && (
          <div className="bg-surface-container rounded-xl border border-outline-variant/20 p-5 space-y-4">
            <div>
              <label className="block text-xs font-medium text-on-surface-variant mb-1">
                代理模式
              </label>
              <select
                value={general.proxy_mode}
                onChange={(e) =>
                  setGeneral((prev) => ({
                    ...prev,
                    proxy_mode: e.target.value as GeneralSettings["proxy_mode"],
                  }))
                }
                className="w-full px-3 py-2 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface focus:outline-none focus:border-primary/50 transition-colors"
              >
                <option value="none">不使用代理</option>
                <option value="system">系统代理</option>
                <option value="custom">自定义代理</option>
              </select>
            </div>

            {general.proxy_mode === "custom" && (
              <div>
                <label className="block text-xs font-medium text-on-surface-variant mb-1">
                  代理地址
                </label>
                <input
                  type="url"
                  value={general.proxy_url}
                  onChange={(e) =>
                    setGeneral((prev) => ({ ...prev, proxy_url: e.target.value }))
                  }
                  placeholder="http://127.0.0.1:7890"
                  className="w-full px-3 py-2 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 transition-colors font-data"
                />
              </div>
            )}

            <div>
              <label className="flex items-center gap-2 text-xs font-medium text-on-surface-variant mb-1">
                <ShieldCheck size={12} />
                SSL 证书路径 (可选)
              </label>
              <input
                type="text"
                value={general.ssl_cert_path}
                onChange={(e) =>
                  setGeneral((prev) => ({
                    ...prev,
                    ssl_cert_path: e.target.value,
                  }))
                }
                placeholder="/path/to/cert.pem"
                className="w-full px-3 py-2 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:border-primary/50 transition-colors font-data"
              />
            </div>

            <div className="pt-1">
              <button
                onClick={handleSaveGeneral}
                disabled={savingGeneral}
                className="flex items-center gap-2 px-4 py-2 text-sm bg-primary text-on-primary font-medium rounded-lg hover:opacity-90 transition-opacity disabled:opacity-50"
              >
                {savingGeneral ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Save size={14} />
                )}
                保存通用设置
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
