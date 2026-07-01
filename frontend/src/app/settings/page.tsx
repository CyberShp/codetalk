"use client";

import { useEffect, useState, useCallback, useRef } from "react";
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
  Terminal,
} from "lucide-react";
import { api, apiBaseInfo, probeApiHealth } from "@/lib/api";
import type {
  LLMConfig,
  LLMConfigCreate,
  GeneralSettings,
  AgentRuntime,
  AgentRuntimeCreate,
  AgentProviderSettings,
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

const DEFAULT_AGENT_PROVIDER_SETTINGS: AgentProviderSettings = {
  claude_code_command: "ccr code",
  claude_code_config_path: "",
  claude_code_fallback_commands: [],
  claude_code_mcp_profiles: [],
  opencode_command: "opencode",
  opencode_fallback_commands: [],
  opencode_mcp_profiles: [],
  external_agent_custom_providers: [],
};

const EMPTY_AGENT_RUNTIME_FORM: AgentRuntimeCreate = {
  name: "",
  command: "",
  args: [],
  prompt_transport: "stdin",
  output_mode: "plain",
  working_dir_mode: "project",
  fixed_working_dir: "",
  env: {},
  health_command: "",
  timeout_seconds: 120,
  completion_mode: "process_exit",
  idle_complete_seconds: 5,
  sentinel_text: "",
  session_persistence: "none",
  resume_args: [],
  enabled: true,
};

const AGENT_RUNTIME_PRESETS = [
  {
    id: "claude-code-router",
    label: "Claude Code Router",
    description: "你平时输入 ccr code 打开 Claude Code，就选这个。",
    commandPreview: "ccr code",
    form: {
      ...EMPTY_AGENT_RUNTIME_FORM,
      name: "Claude Code",
      command: "ccr",
      args: ["code"],
      prompt_transport: "stdin",
      output_mode: "plain",
      completion_mode: "idle_after_output",
      idle_complete_seconds: 5,
    },
  },
  {
    id: "opencode",
    label: "OpenCode",
    description: "你平时输入 opencode run 执行任务，就选这个。",
    commandPreview: "opencode run",
    form: {
      ...EMPTY_AGENT_RUNTIME_FORM,
      name: "OpenCode",
      command: "opencode",
      args: ["run"],
      prompt_transport: "argv_last",
      output_mode: "auto",
    },
  },
  {
    id: "nga",
    label: "NGA / CodeAgent",
    description: "你平时输入 nga 打开内部 CodeAgent，就选这个。",
    commandPreview: "nga",
    form: {
      ...EMPTY_AGENT_RUNTIME_FORM,
      name: "NGA CodeAgent",
      command: "nga",
      args: [],
      prompt_transport: "stdin",
      output_mode: "plain",
      completion_mode: "idle_after_output",
      idle_complete_seconds: 5,
    },
  },
  {
    id: "custom",
    label: "自定义命令",
    description: "公司内还有别的 Agent 命令时，从这里开始填。",
    commandPreview: "your-agent run",
    form: {
      ...EMPTY_AGENT_RUNTIME_FORM,
      name: "自定义 Agent",
      command: "",
      args: [],
      prompt_transport: "stdin",
      output_mode: "plain",
    },
  },
] satisfies Array<{
  id: string;
  label: string;
  description: string;
  commandPreview: string;
  form: AgentRuntimeCreate;
}>;

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
  const [savingActiveModel, setSavingActiveModel] = useState(false);
  const [agentProviders, setAgentProviders] = useState<AgentProviderSettings>({
    ...DEFAULT_AGENT_PROVIDER_SETTINGS,
  });
  const [agentRuntimes, setAgentRuntimes] = useState<AgentRuntime[]>([]);
  const [agentRuntimeForm, setAgentRuntimeForm] = useState<AgentRuntimeCreate>({
    ...EMPTY_AGENT_RUNTIME_FORM,
  });
  const [agentRuntimeArgsText, setAgentRuntimeArgsText] = useState("");
  const [agentRuntimeResumeArgsText, setAgentRuntimeResumeArgsText] = useState("");
  const [agentRuntimeEnvJson, setAgentRuntimeEnvJson] = useState("{}");
  const [savingAgentRuntime, setSavingAgentRuntime] = useState(false);
  const [deletingAgentRuntimeIds, setDeletingAgentRuntimeIds] = useState<string[]>([]);
  const [agentRuntimeProbe, setAgentRuntimeProbe] = useState<Record<string, string>>({});
  const [apiHealthResult, setApiHealthResult] = useState<string | null>(null);
  const [testingApiHealth, setTestingApiHealth] = useState(false);
  const [showAgentAdvanced, setShowAgentAdvanced] = useState(false);
  const [showWorkbenchCliSettings, setShowWorkbenchCliSettings] = useState(false);
  const [showLlmSettings, setShowLlmSettings] = useState(false);
  const [customProvidersJson, setCustomProvidersJson] = useState("[]");
  const [savingAgentProviders, setSavingAgentProviders] = useState(false);
  const deletingAgentRuntimeRef = useRef<Set<string>>(new Set());

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [llmList, generalData, agentProviderData, runtimeData] = await Promise.all([
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
        api.settings.getAgentProviders().catch(
          () => ({ ...DEFAULT_AGENT_PROVIDER_SETTINGS }) as AgentProviderSettings,
        ),
        api.settings.listAgentRuntimes().catch(() => ({ items: [] as AgentRuntime[] })),
      ]);
      setConfigs(llmList);
      setGeneral(generalData);
      setAgentProviders(agentProviderData);
      setAgentRuntimes(runtimeData.items);
      setCustomProvidersJson(JSON.stringify(agentProviderData.external_agent_custom_providers, null, 2));
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
        setShowApiKey(false);
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
          let autoUpdated = { ...general };
          let needsSave = false;
          if (!general.active_chat_model_id && form.is_chat_model) {
            autoUpdated = { ...autoUpdated, active_chat_model_id: newConfig.id };
            needsSave = true;
          }
          if (!general.active_embedding_model_id && form.is_embedding_model) {
            autoUpdated = { ...autoUpdated, active_embedding_model_id: newConfig.id };
            needsSave = true;
          }
          if (needsSave) {
            await api.settings.updateGeneral(autoUpdated);
            setGeneral(autoUpdated);
          }
        }
        setForm({ ...EMPTY_LLM_FORM });
        setEditingId(null);
        setShowForm(false);
        setShowApiKey(false);
        await loadData();
      } catch (err: unknown) {
        setShowApiKey(false);
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
      setShowApiKey(false);
      setShowForm(true);
      setTestResult(null);
    },
    [],
  );

  const closeLLMForm = useCallback(() => {
    setShowForm(false);
    setEditingId(null);
    setForm({ ...EMPTY_LLM_FORM });
    setShowApiKey(false);
    setTestResult(null);
  }, []);

  const handleSaveActiveModel = useCallback(
    async (modelId: string) => {
      setSavingActiveModel(true);
      const updated = { ...general, active_chat_model_id: modelId };
      setGeneral(updated);
      try {
        await api.settings.updateGeneral(updated);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "保存活跃模型失败");
      } finally {
        setSavingActiveModel(false);
      }
    },
    [general],
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

  const updateAgentProviders = useCallback(
    <K extends keyof AgentProviderSettings>(key: K, value: AgentProviderSettings[K]) => {
      setAgentProviders((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const handleSaveAgentProviders = useCallback(async () => {
    setSavingAgentProviders(true);
    setError(null);
    try {
      const customProviders = JSON.parse(customProvidersJson || "[]");
      if (!Array.isArray(customProviders)) {
        throw new Error("Custom providers JSON must be an array");
      }
      const saved = await api.settings.updateAgentProviders({
        ...agentProviders,
        external_agent_custom_providers: customProviders,
      });
      setAgentProviders(saved);
      setCustomProvidersJson(JSON.stringify(saved.external_agent_custom_providers, null, 2));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "保存 Workbench CLI 设置失败");
    } finally {
      setSavingAgentProviders(false);
    }
  }, [agentProviders, customProvidersJson]);

  const updateAgentRuntimeForm = useCallback(
    <K extends keyof AgentRuntimeCreate>(key: K, value: AgentRuntimeCreate[K]) => {
      setAgentRuntimeForm((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const applyAgentRuntimePreset = useCallback((preset: (typeof AGENT_RUNTIME_PRESETS)[number]) => {
    setAgentRuntimeForm({ ...preset.form });
    setAgentRuntimeArgsText(preset.form.args.join(" "));
    setAgentRuntimeResumeArgsText(preset.form.resume_args.join(" "));
    setAgentRuntimeEnvJson(JSON.stringify(preset.form.env ?? {}, null, 2));
    setShowAgentAdvanced(false);
  }, []);

  const handleCreateAgentRuntime = useCallback(async () => {
    if (!agentRuntimeForm.name.trim() || !agentRuntimeForm.command.trim()) {
      setError("请填写执行器名称和命令");
      return;
    }
    setSavingAgentRuntime(true);
    setError(null);
    try {
      let parsedEnv: unknown;
      try {
        parsedEnv = JSON.parse(agentRuntimeEnvJson || "{}");
      } catch {
        throw new Error('环境变量 JSON 格式错误：请填写 JSON 对象，例如 {"HTTPS_PROXY":"http://127.0.0.1:7890"}');
      }
      if (!parsedEnv || typeof parsedEnv !== "object" || Array.isArray(parsedEnv)) {
        throw new Error("环境变量 JSON 必须是对象");
      }
      const env = Object.fromEntries(
        Object.entries(parsedEnv).map(([key, value]) => [key, String(value)]),
      );
      await api.settings.createAgentRuntime({
        ...agentRuntimeForm,
        args: agentRuntimeArgsText.split(/\s+/).map((item) => item.trim()).filter(Boolean),
        resume_args: agentRuntimeResumeArgsText.split(/\s+/).map((item) => item.trim()).filter(Boolean),
        env,
      });
      setAgentRuntimeForm({ ...EMPTY_AGENT_RUNTIME_FORM });
      setAgentRuntimeArgsText("");
      setAgentRuntimeResumeArgsText("");
      setAgentRuntimeEnvJson("{}");
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "保存 Agent 执行器失败");
    } finally {
      setSavingAgentRuntime(false);
    }
  }, [agentRuntimeArgsText, agentRuntimeEnvJson, agentRuntimeForm, agentRuntimeResumeArgsText, loadData]);

  const handleDeleteAgentRuntime = useCallback(
    async (id: string) => {
      if (deletingAgentRuntimeRef.current.has(id)) return;
      if (!confirm("确定要删除这个 AI 线程执行器吗？")) return;
      deletingAgentRuntimeRef.current.add(id);
      setDeletingAgentRuntimeIds((current) => (
        current.includes(id) ? current : [...current, id]
      ));
      try {
        await api.settings.deleteAgentRuntime(id);
        await loadData();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "删除 Agent 执行器失败");
      } finally {
        deletingAgentRuntimeRef.current.delete(id);
        setDeletingAgentRuntimeIds((current) => current.filter((item) => item !== id));
      }
    },
    [loadData],
  );

  const handleProbeAgentRuntime = useCallback(async (runtime: AgentRuntime) => {
    setAgentRuntimeProbe((prev) => ({ ...prev, [runtime.id]: "探测中..." }));
    try {
      const result = await api.settings.probeAgentRuntime(runtime.id);
      setAgentRuntimeProbe((prev) => ({
        ...prev,
        [runtime.id]: `${result.success ? "可用" : "不可用"}：${result.message}`,
      }));
    } catch (err: unknown) {
      setAgentRuntimeProbe((prev) => ({
        ...prev,
        [runtime.id]: err instanceof Error ? err.message : "探测失败",
      }));
    }
  }, []);

  const handleProbeApiHealth = useCallback(async () => {
    setTestingApiHealth(true);
    try {
      const result = await probeApiHealth();
      setApiHealthResult(`${result.ok ? "可用" : "不可用"}：${result.message}`);
    } finally {
      setTestingApiHealth(false);
    }
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-on-surface-variant">
        <Loader2 size={20} className="animate-spin mr-2" />
        加载设置...
      </div>
    );
  }

  return (
    <div className="max-w-5xl">
      <h1 className="font-display text-2xl font-bold text-on-surface mb-1">
        设置
      </h1>
      <p className="text-sm text-on-surface-variant mb-6">
        先配置本机 Agent。LLM、Workbench 探测和代理属于可选高级配置。
      </p>

      {error && (
        <div id="settings-error" role="alert" className="mb-6 px-4 py-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          {error}
        </div>
      )}

      <div className="mb-6 rounded-xl border border-outline-variant/20 bg-surface-container p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-on-surface">后端连接</h2>
            <p className="mt-1 break-all font-data text-xs text-on-surface-variant">
              API Base: {apiBaseInfo().base}
            </p>
            {apiBaseInfo().override && (
              <p className="mt-1 text-xs text-amber-600">
                当前 API 地址被浏览器本地覆盖：{apiBaseInfo().override}。如端口配置已修改，请清除 localStorage 中的 codetalk.apiBaseOverride。
              </p>
            )}
            <p className="mt-1 text-xs text-on-surface-variant">
              来源：{apiBaseInfo().source}
            </p>
          </div>
          <button
            type="button"
            onClick={handleProbeApiHealth}
            disabled={testingApiHealth}
            className="inline-flex items-center gap-2 rounded-lg border border-outline-variant/30 px-3 py-1.5 text-sm text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
          >
            {testingApiHealth ? <Loader2 size={14} className="animate-spin" /> : <TestTube2 size={14} />}
            检查后端连接
          </button>
        </div>
        {apiHealthResult && (
          <p className="mt-3 text-xs text-on-surface-variant">{apiHealthResult}</p>
        )}
      </div>

      {/* AI thread runtime settings */}
      <div className="mb-6 overflow-hidden rounded-2xl border border-outline-variant/20 bg-surface-container shadow-[0_18px_60px_rgba(15,23,42,0.08)]">
        <div className="border-b border-outline-variant/15 bg-[linear-gradient(135deg,rgba(255,255,255,0.92),rgba(248,250,252,0.74))] p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="flex items-center gap-2 text-base font-semibold text-on-surface">
                <Terminal size={18} />
                先配置你平时用的 Agent
              </h2>
              <p className="mt-1 max-w-2xl text-sm text-on-surface-variant">
                选择你在终端里常用的启动方式。AI 线程会直接调用这些本机 Agent；只有选择“内置模型”时，才需要下面的 LLM 配置。
              </p>
              <p className="mt-2 max-w-2xl text-xs leading-5 text-on-surface-variant">
                Command 只填可执行文件，例如 <code className="font-data">ccr</code>、<code className="font-data">nga</code>、<code className="font-data">python</code>；
                参数放到 Args，例如 <code className="font-data">code</code>。不要把 <code className="font-data">ccr code</code> 整体填进 Command。
              </p>
            </div>
            <span className="rounded-full border border-outline-variant/20 bg-surface px-3 py-1 text-xs font-medium text-on-surface-variant">
              推荐先完成这一步
            </span>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {AGENT_RUNTIME_PRESETS.map((preset) => {
              const selected =
                agentRuntimeForm.name === preset.form.name &&
                agentRuntimeForm.command === preset.form.command &&
                agentRuntimeArgsText === preset.form.args.join(" ");
              return (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => applyAgentRuntimePreset(preset)}
                  className={`group rounded-xl border p-4 text-left transition-[transform,box-shadow,border-color,background] duration-200 hover:-translate-y-0.5 hover:shadow-[0_16px_36px_rgba(15,23,42,0.10)] ${
                    selected
                      ? "border-primary/45 bg-primary/10 shadow-[0_14px_34px_rgba(15,23,42,0.10)]"
                      : "border-outline-variant/20 bg-surface/86 hover:border-outline-variant/45"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <strong className="text-sm font-semibold text-on-surface">{preset.label}</strong>
                    <span className="h-2 w-2 rounded-full bg-primary opacity-0 transition-opacity group-hover:opacity-100" />
                  </div>
                  <p className="mt-2 min-h-10 text-xs leading-5 text-on-surface-variant">
                    {preset.description}
                  </p>
                  <code className="mt-3 block truncate rounded-lg bg-surface-container-high px-2 py-1.5 font-data text-[11px] text-on-surface">
                    {preset.commandPreview}
                  </code>
                </button>
              );
            })}
          </div>
        </div>

        <div className="p-5">
          <div className="rounded-xl border border-outline-variant/18 bg-surface/80 p-4">
            <div className="grid gap-3 lg:grid-cols-[1fr_1fr_1.25fr_auto]">
              <div>
                <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                  显示名称
                </label>
                <input
                  value={agentRuntimeForm.name}
                  onChange={(event) => updateAgentRuntimeForm("name", event.target.value)}
                  placeholder="例如 Claude Code"
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                  Command
                </label>
                <input
                  value={agentRuntimeForm.command}
                  onChange={(event) => updateAgentRuntimeForm("command", event.target.value)}
                  placeholder="ccr / opencode / nga"
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                  Args
                </label>
                <input
                  value={agentRuntimeArgsText}
                  onChange={(event) => setAgentRuntimeArgsText(event.target.value)}
                  placeholder="code 或 run"
                  className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
                />
              </div>
              <button
                type="button"
                onClick={handleCreateAgentRuntime}
                disabled={savingAgentRuntime}
                className="mt-5 flex min-w-28 items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50 lg:mt-6"
              >
                {savingAgentRuntime ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                保存
              </button>
            </div>

            <button
              type="button"
              onClick={() => setShowAgentAdvanced((value) => !value)}
              className="mt-3 flex items-center gap-2 rounded-lg px-1 py-1 text-xs font-medium text-on-surface-variant hover:text-on-surface"
            >
              {showAgentAdvanced ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              高级选项
            </button>

            {showAgentAdvanced && (
              <div className="mt-3 grid gap-3 rounded-xl border border-outline-variant/15 bg-surface-container/60 p-3 lg:grid-cols-4">
                <div>
                  <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                    问题发送方式
                  </label>
                  <select
                    value={agentRuntimeForm.prompt_transport}
                    onChange={(event) => updateAgentRuntimeForm("prompt_transport", event.target.value as AgentRuntimeCreate["prompt_transport"])}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary/50 focus:outline-none"
                  >
                    <option value="stdin">通过 stdin 发送</option>
                    <option value="argv_last">作为最后一个参数</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                    输出解析
                  </label>
                  <select
                    value={agentRuntimeForm.output_mode}
                    onChange={(event) => updateAgentRuntimeForm("output_mode", event.target.value as AgentRuntimeCreate["output_mode"])}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary/50 focus:outline-none"
                  >
                    <option value="plain">普通文本</option>
                    <option value="auto">自动识别</option>
                    <option value="ndjson">NDJSON</option>
                    <option value="stream_json">stream-json</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                    工作目录
                  </label>
                  <select
                    value={agentRuntimeForm.working_dir_mode}
                    onChange={(event) => updateAgentRuntimeForm("working_dir_mode", event.target.value as AgentRuntimeCreate["working_dir_mode"])}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary/50 focus:outline-none"
                  >
                    <option value="project">当前项目目录</option>
                    <option value="fixed">固定目录</option>
                    <option value="none">不设置</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                    超时秒数
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={3600}
                    value={agentRuntimeForm.timeout_seconds}
                    onChange={(event) => updateAgentRuntimeForm("timeout_seconds", Number(event.target.value) || 120)}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface focus:border-primary/50 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                    完成判据
                  </label>
                  <select
                    value={agentRuntimeForm.completion_mode}
                    onChange={(event) => updateAgentRuntimeForm("completion_mode", event.target.value as AgentRuntimeCreate["completion_mode"])}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary/50 focus:outline-none"
                  >
                    <option value="process_exit">进程退出</option>
                    <option value="idle_after_output">输出空闲后结束</option>
                    <option value="sentinel">看到结束标记</option>
                  </select>
                </div>
                {agentRuntimeForm.completion_mode === "idle_after_output" && (
                  <div>
                    <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                      空闲秒数
                    </label>
                    <input
                      type="number"
                      min={1}
                      max={300}
                      value={agentRuntimeForm.idle_complete_seconds}
                      onChange={(event) => updateAgentRuntimeForm("idle_complete_seconds", Number(event.target.value) || 5)}
                      className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface focus:border-primary/50 focus:outline-none"
                    />
                  </div>
                )}
                {agentRuntimeForm.completion_mode === "sentinel" && (
                  <div className="lg:col-span-2">
                    <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                      结束标记
                    </label>
                    <input
                      value={agentRuntimeForm.sentinel_text}
                      onChange={(event) => updateAgentRuntimeForm("sentinel_text", event.target.value)}
                      placeholder="__CODETALK_DONE__"
                      className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
                    />
                  </div>
                )}
                <div>
                  <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                    会话续接
                  </label>
                  <select
                    value={agentRuntimeForm.session_persistence}
                    onChange={(event) => updateAgentRuntimeForm("session_persistence", event.target.value as AgentRuntimeCreate["session_persistence"])}
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary/50 focus:outline-none"
                  >
                    <option value="none">不续接</option>
                    <option value="resume_args">使用 resume 参数</option>
                  </select>
                </div>
                {agentRuntimeForm.session_persistence === "resume_args" && (
                  <div className="lg:col-span-3">
                    <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                      Resume 参数
                    </label>
                    <input
                      value={agentRuntimeResumeArgsText}
                      onChange={(event) => setAgentRuntimeResumeArgsText(event.target.value)}
                      placeholder="例如 exec resume {session_id} --json"
                      className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
                    />
                  </div>
                )}
                {agentRuntimeForm.working_dir_mode === "fixed" && (
                  <div className="lg:col-span-4">
                    <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                      固定工作目录
                    </label>
                    <input
                      value={agentRuntimeForm.fixed_working_dir}
                      onChange={(event) => updateAgentRuntimeForm("fixed_working_dir", event.target.value)}
                      placeholder="例如 D:\\repo\\project"
                      className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
                    />
                  </div>
                )}
                <div className="lg:col-span-4">
                  <label className="mb-1 block text-xs font-medium text-on-surface-variant">
                    环境变量 JSON
                  </label>
                  <textarea
                    value={agentRuntimeEnvJson}
                    onChange={(event) => setAgentRuntimeEnvJson(event.target.value)}
                    rows={3}
                    placeholder='例如 {"HTTPS_PROXY":"http://127.0.0.1:7890"}'
                    className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-xs text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
                  />
                </div>
              </div>
            )}
          </div>

          <div className="mt-4 grid gap-3">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-on-surface">已配置执行器</h3>
              <span className="rounded-full bg-surface-container-high px-2 py-1 text-xs text-on-surface-variant">
                {agentRuntimes.length} 个
              </span>
            </div>
            {agentRuntimes.length === 0 ? (
              <div className="rounded-xl border border-dashed border-outline-variant/40 bg-surface/60 px-4 py-5 text-sm text-on-surface-variant">
                还没有执行器。先点上面的 Claude Code Router、OpenCode 或 NGA，再点保存。
              </div>
            ) : (
              agentRuntimes.map((runtime) => {
                const deletingRuntime = deletingAgentRuntimeIds.includes(runtime.id);
                return (
                <div key={runtime.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-outline-variant/20 bg-surface px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <strong className="text-sm text-on-surface">{runtime.name}</strong>
                      <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[11px] text-on-surface-variant">
                        {runtime.prompt_transport === "stdin" ? "stdin 发送" : "参数发送"}
                      </span>
                      <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[11px] text-on-surface-variant">
                        {runtime.output_mode}
                      </span>
                      {runtime.session_persistence === "resume_args" && (
                        <span className="rounded-full bg-primary-container px-2 py-0.5 text-[11px] text-on-primary-container">
                          resume
                        </span>
                      )}
                    </div>
                    <p className="mt-1 break-all font-data text-xs text-on-surface-variant">
                      {runtime.command} {runtime.args.join(" ")}
                    </p>
                    {agentRuntimeProbe[runtime.id] && (
                      <p className="mt-1 text-xs text-on-surface-variant">{agentRuntimeProbe[runtime.id]}</p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => handleProbeAgentRuntime(runtime)}
                    disabled={deletingRuntime}
                    className="rounded-lg border border-outline-variant/30 px-3 py-1.5 text-sm text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                  >
                    测试
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDeleteAgentRuntime(runtime.id)}
                    disabled={deletingRuntime}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-red-500/20 px-3 py-1.5 text-sm text-red-500 transition-colors hover:bg-red-500/10 disabled:opacity-50"
                  >
                    {deletingRuntime && <Loader2 size={13} className="animate-spin" />}
                    {deletingRuntime ? "删除中..." : "删除"}
                  </button>
                </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* Agent CLI settings */}
      <div className="mb-6 rounded-xl border border-outline-variant/20 bg-surface-container p-4">
        <button
          type="button"
          onClick={() => setShowWorkbenchCliSettings((value) => !value)}
          className="flex w-full items-center justify-between gap-3 text-left"
        >
          <span>
            <span className="flex items-center gap-2 text-sm font-semibold text-on-surface">
              <ShieldCheck size={17} />
              高级：Workbench 探测配置
            </span>
            <span className="mt-1 block text-xs text-on-surface-variant">
              只有需要调整智能体编排的健康探测、备用命令或 MCP 配置时才打开。
            </span>
          </span>
          {showWorkbenchCliSettings ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
        {showWorkbenchCliSettings && (
          <div className="mt-4 border-t border-outline-variant/15 pt-4">
            <div className="mb-4 flex justify-end">
              <button
                type="button"
                onClick={handleSaveAgentProviders}
                disabled={savingAgentProviders}
                className="flex items-center gap-2 rounded-lg bg-primary px-3 py-1.5 text-sm text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {savingAgentProviders ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                保存探测配置
              </button>
            </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs font-medium text-on-surface-variant">
              Claude / CCR 命令
            </label>
            <input
              type="text"
              aria-label="Claude Code command"
              value={agentProviders.claude_code_command}
              onChange={(event) => updateAgentProviders("claude_code_command", event.target.value)}
              placeholder="ccr code"
              className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-on-surface-variant">
              CCR 配置路径
            </label>
            <input
              type="text"
              aria-label="CCR config path"
              value={agentProviders.claude_code_config_path}
              onChange={(event) => updateAgentProviders("claude_code_config_path", event.target.value)}
              placeholder="C:/innernet/ccr/config-router.json"
              className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-on-surface-variant">
              Claude 备用命令
            </label>
            <input
              type="text"
              aria-label="Claude fallback commands"
              value={agentProviders.claude_code_fallback_commands.join(", ")}
              onChange={(event) =>
                updateAgentProviders(
                  "claude_code_fallback_commands",
                  event.target.value.split(",").map((item) => item.trim()).filter(Boolean),
                )
              }
              placeholder="claude"
              className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-on-surface-variant">
              Claude MCP 配置
            </label>
            <input
              type="text"
              aria-label="Claude MCP profiles"
              value={agentProviders.claude_code_mcp_profiles.join(", ")}
              onChange={(event) =>
                updateAgentProviders(
                  "claude_code_mcp_profiles",
                  event.target.value.split(",").map((item) => item.trim()).filter(Boolean),
                )
              }
              placeholder="codehub-readonly"
              className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-on-surface-variant">
              OpenCode 命令
            </label>
            <input
              type="text"
              aria-label="OpenCode command"
              value={agentProviders.opencode_command}
              onChange={(event) => updateAgentProviders("opencode_command", event.target.value)}
              placeholder="opencode"
              className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-on-surface-variant">
              OpenCode MCP 配置
            </label>
            <input
              type="text"
              aria-label="OpenCode MCP profiles"
              value={agentProviders.opencode_mcp_profiles.join(", ")}
              onChange={(event) =>
                updateAgentProviders(
                  "opencode_mcp_profiles",
                  event.target.value.split(",").map((item) => item.trim()).filter(Boolean),
                )
              }
              placeholder="codehub-mcp"
              className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
            />
          </div>
        </div>
        <div className="mt-4">
          <label className="mb-1 block text-xs font-medium text-on-surface-variant">
            自定义 Agent Provider JSON
          </label>
          <textarea
            aria-label="Custom Agent providers JSON"
            value={customProvidersJson}
            onChange={(event) => setCustomProvidersJson(event.target.value)}
            rows={7}
            className="w-full rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 font-data text-xs text-on-surface placeholder:text-on-surface-variant/50 focus:border-primary/50 focus:outline-none"
          />
          <p className="mt-1 break-words text-[11px] text-on-surface-variant">
            Example:{" "}
            <code className="font-data">
              {`[{"id":"corp-agent","command":"corp-agent run --json","prompt_transport":"stdin","env_hints":{"CORP_AGENT_PROFILE":"innernet"}}]`}
            </code>
          </p>
        </div>
          </div>
        )}
      </div>

      <div className="mb-6 rounded-xl border border-outline-variant/20 bg-surface-container p-4">
        <button
          type="button"
          onClick={() => setShowLlmSettings((value) => !value)}
          className="flex w-full items-center justify-between gap-3 text-left"
        >
          <span>
            <span className="flex items-center gap-2 text-sm font-semibold text-on-surface">
              <Bot size={17} />
              可选：内置模型与 RAG 检索
            </span>
            <span className="mt-1 block text-xs text-on-surface-variant">
              只有不通过本机 Agent，或需要材料检索嵌入时才需要配置。
            </span>
          </span>
          {showLlmSettings ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>

        {showLlmSettings && (
          <div className="mt-4 border-t border-outline-variant/15 pt-4">
      {/* Active Model — visible inside optional LLM settings */}
      <div className="mb-6 bg-surface rounded-xl border border-outline-variant/20 p-4 flex items-center gap-4">
        <Bot size={16} className="text-primary shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium text-on-surface-variant mb-1.5">
            活跃聊天模型
          </p>
          <select
            value={general.active_chat_model_id}
            onChange={(e) => handleSaveActiveModel(e.target.value)}
            disabled={savingActiveModel || configs.filter((c) => c.is_chat_model).length === 0}
            className="w-full px-3 py-1.5 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface focus:outline-none focus:border-primary/50 transition-colors disabled:opacity-50"
          >
            <option value="">
              {configs.filter((c) => c.is_chat_model).length === 0
                ? "暂无聊天模型，请先添加"
                : "请选择活跃的聊天模型"}
            </option>
            {configs
              .filter((c) => c.is_chat_model)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.model})
                </option>
              ))}
          </select>
        </div>
        {savingActiveModel && (
          <Loader2 size={14} className="animate-spin text-on-surface-variant shrink-0" />
        )}
      </div>

      <div className="mb-8">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-medium text-on-surface flex items-center gap-2">
            <Bot size={18} />
            LLM 配置
          </h2>
          <button
            onClick={() => {
              if (showForm) {
                closeLLMForm();
              } else {
                setEditingId(null);
                setForm({ ...EMPTY_LLM_FORM });
                setShowApiKey(false);
                setTestResult(null);
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
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                closeLLMForm();
              }
            }}
            aria-describedby={error ? "settings-error" : undefined}
            className="bg-surface-container rounded-xl border border-outline-variant/20 p-5 mb-4 space-y-4"
          >
            <p className="text-sm font-medium text-on-surface">
              {editingId ? "编辑 LLM 配置" : "新增 LLM 配置"}
            </p>
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
                    {general.active_chat_model_id === cfg.id && (
                      <span className="text-[10px] px-1.5 py-0.5 bg-primary/10 rounded text-primary">
                        活跃
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

      {configs.filter((c) => c.is_embedding_model).length > 0 && (
        <div className="mb-8 bg-surface-container rounded-xl border border-outline-variant/20 px-5 py-4">
          <label className="block text-xs font-medium text-on-surface-variant mb-2">
            活跃嵌入模型
          </label>
          <div className="flex items-center gap-3">
            <select
              value={general.active_embedding_model_id}
              onChange={async (e) => {
                const prev = general;
                const updated = { ...general, active_embedding_model_id: e.target.value };
                setGeneral(updated);
                try {
                  await api.settings.updateGeneral(updated);
                } catch {
                  setGeneral(prev);
                  setError("保存活跃嵌入模型失败");
                }
              }}
              className="flex-1 px-3 py-2 bg-surface border border-outline-variant/30 rounded-lg text-sm text-on-surface focus:outline-none focus:border-primary/50 transition-colors"
            >
              <option value="">请选择活跃的嵌入模型</option>
              {configs
                .filter((c) => c.is_embedding_model)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.model})
                  </option>
                ))}
            </select>
          </div>
          <p className="text-[11px] text-on-surface-variant/60 mt-2">
            用于工作空间材料 RAG 检索，选择后新上传的材料将自动分块嵌入
          </p>
        </div>
      )}
          </div>
        )}
      </div>

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
