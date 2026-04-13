"use client";

import { useState, useEffect, useCallback } from "react";
import GlassPanel from "@/components/ui/GlassPanel";
import CyberInput from "@/components/ui/CyberInput";
import StatusBadge from "@/components/ui/StatusBadge";
import { api } from "@/lib/api";
import type { LLMConfig, ToolInfo } from "@/lib/types";

export default function SettingsPage() {
  const [aiEnabled, setAiEnabled] = useState(true);
  const [configs, setConfigs] = useState<LLMConfig[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [provider, setProvider] = useState("google");
  const [modelName, setModelName] = useState("");
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
        provider,
        model_name: modelName.trim(),
        is_default: configs.length === 0,
      });
      setModelName("");
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
            className={`relative w-11 h-6 rounded-full transition-colors ${
              aiEnabled ? "bg-primary-container" : "bg-surface-container-high"
            }`}
          >
            <span
              className={`absolute top-0.5 w-5 h-5 rounded-full transition-transform ${
                aiEnabled
                  ? "translate-x-5.5 bg-primary"
                  : "translate-x-0.5 bg-on-surface-variant"
              }`}
            />
          </button>
        </div>
      </GlassPanel>

      {/* LLM Provider */}
      <GlassPanel>
        <h3 className="text-sm font-medium text-on-surface mb-4">
          LLM Provider Configuration
        </h3>

        {configs.map((cfg) => (
          <div
            key={cfg.id}
            className="flex items-center justify-between bg-surface-container-lowest/50 rounded-lg px-4 py-3 mb-3"
          >
            <div>
              <p className="text-sm text-on-surface">
                {cfg.provider} / {cfg.model_name}
              </p>
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
        ))}

        <div className="grid grid-cols-2 gap-4 mt-4">
          <div>
            <label className="block text-xs text-on-surface-variant mb-1.5 tracking-wide uppercase">
              Provider
            </label>
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              className="w-full bg-surface-container-lowest/50 text-on-surface font-data text-sm px-4 py-2 rounded-md outline-none focus:ring-1 focus:ring-primary-container"
            >
              <option value="google">Google (Gemini)</option>
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
              <option value="ollama">Ollama (Local)</option>
              <option value="openrouter">OpenRouter</option>
            </select>
          </div>
          <CyberInput
            label="Model Name"
            placeholder="gemini-2.0-flash"
            value={modelName}
            onChange={(e) => setModelName(e.target.value)}
          />
        </div>
        <p className="text-xs text-on-surface-variant/60 mt-3">
          API keys are configured via Docker environment variables
          (OPENAI_API_KEY, GOOGLE_API_KEY, etc. in docker-compose.yml).
          Provider/model selection here controls which LLM deepwiki uses at runtime.
        </p>
        <button
          onClick={handleSave}
          disabled={saving || !modelName.trim()}
          className="mt-4 px-4 py-2 text-sm font-medium rounded-md bg-primary-container text-primary hover:shadow-[0_0_12px_rgba(164,230,255,0.2)] transition-shadow disabled:opacity-40"
        >
          {saving ? "Saving..." : "Save Provider"}
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
