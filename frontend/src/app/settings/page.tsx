"use client";

import { useState, useEffect, useCallback } from "react";
import GlassPanel from "@/components/ui/GlassPanel";
import CyberInput from "@/components/ui/CyberInput";
import StatusBadge from "@/components/ui/StatusBadge";
import { api } from "@/lib/api";
import type { LLMConfig, ToolInfo, ProxyMode } from "@/lib/types";

export default function SettingsPage() {
  const [aiEnabled, setAiEnabled] = useState(true);
  const [configs, setConfigs] = useState<LLMConfig[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [modelName, setModelName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [proxyMode, setProxyMode] = useState<ProxyMode>("system");
  const [showApiKey, setShowApiKey] = useState(false);
  const [saving, setSaving] = useState(false);

  const loadConfigs = useCallback(async () => {
    try {
      const data = await api.settings.listLLM();
      setConfigs(data);
    } catch (e) {
      console.error("Failed to load LLM configs:", e);
    }
  }, []);

  useEffect(() => {
    loadConfigs();
    api.tools.list().then(setTools).catch(() => {});
    const stored = localStorage.getItem("codetalks_ai_enabled");
    if (stored !== null) setAiEnabled(stored === "true");
  }, [loadConfigs]);

  const toggleAI = () => {
    const next = !aiEnabled;
    setAiEnabled(next);
    localStorage.setItem("codetalks_ai_enabled", String(next));
  };

  const handleSave = async () => {
    if (!modelName.trim()) return;
    setSaving(true);
    try {
      await api.settings.saveLLM({
        provider: "custom",
        model_name: modelName.trim(),
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
        proxy_mode: proxyMode,
        is_default: configs.length === 0,
      });
      setModelName("");
      setApiKey("");
      setBaseUrl("");
      await loadConfigs();
    } catch (e) {
      console.error("Failed to save config:", e);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.settings.deleteLLM(id);
      await loadConfigs();
    } catch (e) {
      console.error("Failed to delete config:", e);
    }
  };

  return (
    <div className="space-y-6 max-w-3xl">
      <h2 className="font-display text-lg font-semibold text-on-surface">
        Settings
      </h2>

      {/* AI Toggle */}
      <GlassPanel>
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium text-on-surface">
              AI Summary Generation
            </h3>
            <p className="text-xs text-on-surface-variant mt-1">
              Enable AI-powered summaries for analysis results via LLM
              providers.
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

      {/* LLM Configuration */}
      <GlassPanel>
        <h3 className="text-sm font-medium text-on-surface mb-4">
          LLM Configuration
        </h3>

        {configs.map((cfg) => (
          <div
            key={cfg.id}
            className="bg-surface-container-lowest/50 rounded-lg px-4 py-3 mb-3"
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-on-surface font-medium">
                  {cfg.model_name}
                </p>
                <div className="flex items-center gap-3 mt-1">
                  {cfg.base_url && (
                    <span className="font-data text-[10px] text-primary-fixed-dim truncate max-w-[240px]">
                      {cfg.base_url}
                    </span>
                  )}
                  <span className={`text-[10px] ${cfg.has_api_key ? "text-secondary-fixed-dim" : "text-on-surface-variant/40"}`}>
                    {cfg.has_api_key ? "Key set" : "No key"}
                  </span>
                  <span className="text-[10px] text-on-surface-variant/60">
                    {cfg.proxy_mode === "system" ? "System proxy" : "Direct"}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-3">
                {cfg.is_default && (
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-primary/10 text-primary-fixed-dim">
                    default
                  </span>
                )}
                <button
                  onClick={() => handleDelete(cfg.id)}
                  className="text-xs text-tertiary hover:text-tertiary/80"
                >
                  Remove
                </button>
              </div>
            </div>
          </div>
        ))}

        <div className="space-y-3 mt-4">
          <CyberInput
            label="Base URL"
            placeholder="http://10.0.0.1:8080/v1"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
          <div className="grid grid-cols-2 gap-4">
            <CyberInput
              label="Model Name"
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
                  placeholder="sk-..."
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  className="w-full bg-surface-container-lowest/50 text-on-surface font-data text-sm px-4 py-2 pr-12 rounded-md outline-none placeholder:text-on-surface-variant/40 focus:ring-1 focus:ring-primary-container"
                />
                <button
                  type="button"
                  onClick={() => setShowApiKey(!showApiKey)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-on-surface-variant hover:text-on-surface px-1.5 py-0.5 rounded"
                >
                  {showApiKey ? "HIDE" : "SHOW"}
                </button>
              </div>
            </div>
          </div>
          <div>
            <label className="block text-xs text-on-surface-variant mb-1.5 tracking-wide uppercase">
              Proxy
            </label>
            <div className="flex gap-2">
              {([
                { value: "system" as const, label: "Follow System Proxy" },
                { value: "direct" as const, label: "Direct (No Proxy)" },
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
          {saving ? "Saving..." : "Save"}
        </button>
      </GlassPanel>

      {/* System Health */}
      <GlassPanel>
        <h3 className="text-sm font-medium text-on-surface mb-4">
          System Health
        </h3>
        <div className="space-y-2">
          {tools.map((tool) => (
            <div
              key={tool.name}
              className="flex items-center justify-between py-2"
            >
              <div>
                <p className="text-sm text-on-surface">{tool.name}</p>
                <p className="text-xs text-on-surface-variant font-data">
                  {tool.healthy ? "Connected" : "Not available"}
                </p>
              </div>
              <StatusBadge status={tool.healthy ? "online" : "offline"} />
            </div>
          ))}
          {tools.length === 0 && (
            <p className="text-sm text-on-surface-variant/50">
              Loading tools...
            </p>
          )}
        </div>
      </GlassPanel>
    </div>
  );
}
