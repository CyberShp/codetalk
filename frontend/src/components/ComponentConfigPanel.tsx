"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import GlassPanel from "@/components/ui/GlassPanel";
import CyberInput from "@/components/ui/CyberInput";
import { usePageRestoreRefresh } from "@/hooks/usePageRestoreRefresh";
import { api } from "@/lib/api";
import type {
  ComponentContract,
  ComponentStatus,
  ConfigDomain,
} from "@/lib/types";

// ── Status dot — 6 px breathing light ──────────────────────────────────────

function NerveDot({ healthy }: { healthy: boolean }) {
  return (
    <span
      className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
      style={{
        backgroundColor: healthy ? "#A4E6FF" : "#FFD1CD",
        boxShadow: healthy
          ? "0 0 4px 1px rgba(164,230,255,0.5)"
          : "0 0 4px 1px rgba(255,209,205,0.4)",
      }}
    />
  );
}

// ── Save / Restart icon buttons ─────────────────────────────────────────────

function IconButton({
  onClick,
  disabled,
  active,
  title,
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`w-8 h-8 flex items-center justify-center rounded-md transition-all shrink-0
        ${active
          ? "text-primary hover:bg-primary/10"
          : "text-on-surface-variant/30 cursor-default"
        }
        disabled:opacity-30`}
    >
      {children}
    </button>
  );
}

// ── Section A: compact tool connection row ──────────────────────────────────

function ToolRow({
  contract,
  status,
  value,
  dirty,
  saving,
  feedback,
  onChange,
  onSave,
}: {
  contract: ComponentContract;
  status: ComponentStatus | undefined;
  value: string;
  dirty: boolean;
  saving: boolean;
  feedback: { ok: boolean; msg: string } | null;
  onChange: (v: string) => void;
  onSave: () => void;
}) {
  const healthy = status?.health.healthy ?? false;

  return (
    <div className="flex items-center gap-3 py-2 px-1">
      <NerveDot healthy={healthy} />
      <span
        className="text-xs text-on-surface font-medium shrink-0"
        style={{ width: 108 }}
      >
        {contract.label.split(" ")[0]}
      </span>
      <div className="flex-1 min-w-0">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={
            contract.domains.find((d) => d.domain === "connection")?.fields[0]
              ?.placeholder ?? "http://..."
          }
          className="w-full bg-surface-container-lowest/40 text-on-surface font-mono text-xs
            px-3 h-8 rounded-md outline-none
            placeholder:text-on-surface-variant/30
            focus:ring-1 focus:ring-primary/30 transition-shadow"
        />
        {feedback && (
          <p
            className={`text-[10px] mt-0.5 ${feedback.ok ? "text-secondary-fixed-dim" : "text-tertiary"}`}
          >
            {feedback.msg}
          </p>
        )}
      </div>
      {/* Save icon — disk */}
      <IconButton
        onClick={onSave}
        disabled={!dirty || saving}
        active={dirty && !saving}
        title="保存连接地址"
      >
        {saving ? (
          <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <circle cx="12" cy="12" r="10" strokeOpacity={0.3} />
            <path d="M12 2a10 10 0 0 1 10 10" />
          </svg>
        ) : (
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
            <polyline points="17 21 17 13 7 13 7 21" />
            <polyline points="7 3 7 8 15 8" />
          </svg>
        )}
      </IconButton>
    </div>
  );
}

// ── Section B: one card per component, all its domains inside ───────────────

function DomainFields({
  component,
  domain,
  getFormValue,
  setFormValue,
  hasChanges,
  saving,
  onSave,
}: {
  component: string;
  domain: ConfigDomain;
  getFormValue: (comp: string, dom: string, field: string) => string;
  setFormValue: (comp: string, dom: string, field: string, v: string) => void;
  hasChanges: boolean;
  saving: boolean;
  onSave: () => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h5 className="text-[10px] text-on-surface-variant/60 uppercase tracking-wider">
          {domain.label}
        </h5>
        <button
          onClick={onSave}
          disabled={saving || !hasChanges}
          className="px-2.5 py-1 text-[10px] rounded bg-surface-container-high text-on-surface-variant hover:text-on-surface transition-colors disabled:opacity-30"
        >
          {saving ? "保存中..." : "保存"}
        </button>
      </div>
      <div className="space-y-3">
        {domain.fields.map((field) => {
          if (field.field_type === "select") {
            return (
              <div key={field.name}>
                <label className="block text-xs text-on-surface-variant mb-1.5">
                  {field.label}
                </label>
                <select
                  value={getFormValue(component, domain.domain, field.name)}
                  onChange={(e) =>
                    setFormValue(component, domain.domain, field.name, e.target.value)
                  }
                  className="w-full bg-surface-container-lowest/50 text-on-surface font-mono text-sm px-4 py-2 rounded-md outline-none"
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
            <CyberInput
              key={field.name}
              label={field.label}
              type={field.field_type === "secret" ? "password" : "text"}
              placeholder={field.placeholder ?? ""}
              value={getFormValue(component, domain.domain, field.name)}
              onChange={(e) =>
                setFormValue(component, domain.domain, field.name, e.target.value)
              }
            />
          );
        })}
      </div>
    </div>
  );
}

function ComponentCard({
  contract,
  domains,
  getFormValue,
  setFormValue,
  hasDomainChanges,
  savingKey,
  applying,
  feedback,
  onSaveDomain,
  onApplyRestart,
}: {
  contract: ComponentContract;
  domains: ConfigDomain[];
  getFormValue: (comp: string, dom: string, field: string) => string;
  setFormValue: (comp: string, dom: string, field: string, v: string) => void;
  hasDomainChanges: (comp: string, dom: string) => boolean;
  savingKey: string | null;
  applying: boolean;
  feedback: { ok: boolean; msg: string } | null;
  onSaveDomain: (domain: ConfigDomain) => void;
  onApplyRestart: () => void;
}) {
  return (
    <div className="bg-surface-container-lowest/30 rounded-lg p-4 flex flex-col gap-4">
      {/* Domain fields — side by side when multiple */}
      <div className={domains.length > 1 ? "grid grid-cols-2 gap-4" : ""}>
        {domains.map((domain) => {
          const key = `${contract.component}:${domain.domain}`;
          return (
            <DomainFields
              key={domain.domain}
              component={contract.component}
              domain={domain}
              getFormValue={getFormValue}
              setFormValue={setFormValue}
              hasChanges={hasDomainChanges(contract.component, domain.domain)}
              saving={savingKey === key}
              onSave={() => onSaveDomain(domain)}
            />
          );
        })}
      </div>
      {/* Single Apply & Restart for the whole component */}
      <div className="flex items-center gap-2 pt-1 border-t border-outline-variant/10">
        <button
          onClick={onApplyRestart}
          disabled={applying}
          className="px-3 py-1.5 text-xs rounded-md bg-primary-container/80 text-primary hover:bg-primary-container transition-colors disabled:opacity-40"
        >
          {applying ? "应用中..." : "应用并重启"}
        </button>
        {feedback && (
          <span className={`text-[10px] ${feedback.ok ? "text-secondary-fixed-dim" : "text-tertiary"}`}>
            {feedback.msg}
          </span>
        )}
      </div>
    </div>
  );
}

// ── Main panel ──────────────────────────────────────────────────────────────

export default function ComponentConfigPanel() {
  const [contracts, setContracts] = useState<ComponentContract[]>([]);
  const [statuses, setStatuses] = useState<ComponentStatus[]>([]);

  // Form state for Section A (connection rows): comp → url string
  const [connValues, setConnValues] = useState<Record<string, string>>({});
  const [connDirty, setConnDirty] = useState<Record<string, boolean>>({});
  const connDirtyRef = useRef(connDirty);
  connDirtyRef.current = connDirty;
  const [connSaving, setConnSaving] = useState<Record<string, boolean>>({});
  const [connFeedback, setConnFeedback] = useState<Record<string, { ok: boolean; msg: string } | null>>({});

  // Form state for Section B (container domains)
  const [forms, setForms] = useState<Record<string, Record<string, string>>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [applying, setApplying] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ key: string; ok: boolean; msg: string } | null>(null);

  const [loadError, setLoadError] = useState("");

  // Two-phase load:
  // Phase 1 — contracts (instant, ~1ms): renders the form shell immediately.
  // Phase 2 — statuses (health checks, up to 3s per tool): updates status dots
  //            and seeds saved URL values, runs in background after phase 1.
  const load = useCallback(async () => {
    setLoadError("");
    try {
      // Phase 1: contracts only — show form immediately
      const c = await api.components.contracts();
      setContracts(c);

      // Phase 2: statuses — health + saved config values (background)
      api.components.list().then((s) => {
        setStatuses(s);
        // Seed connection values from saved DB config (only if not dirty)
        const newVals: Record<string, string> = {};
        for (const contract of c) {
          const connDomain = contract.domains.find((d) => d.domain === "connection");
          if (!connDomain) continue;
          const status = s.find((st) => st.component === contract.component);
          const saved = status?.domains.find((d) => d.domain === "connection");
          newVals[contract.component] = saved?.config?.base_url ?? "";
        }
        setConnValues((prev) => {
          const dirty = connDirtyRef.current;
          const merged = { ...newVals };
          for (const [k, v] of Object.entries(prev)) {
            if (dirty[k]) merged[k] = v;
          }
          return merged;
        });
      }).catch(() => {/* health checks failed — form still usable */});
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "组件配置加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);
  usePageRestoreRefresh(() => { void load(); });

  // ── Connection row handlers ───────────────────────────────────────────────

  const handleConnChange = useCallback((comp: string, val: string) => {
    setConnValues((prev) => ({ ...prev, [comp]: val }));
    setConnDirty((prev) => ({ ...prev, [comp]: true }));
  }, []);

  const handleConnSave = async (comp: string) => {
    setConnSaving((prev) => ({ ...prev, [comp]: true }));
    setConnFeedback((prev) => ({ ...prev, [comp]: null }));
    try {
      await api.components.saveConfig(comp, "connection", {
        base_url: connValues[comp] ?? "",
      });
      setConnDirty((prev) => ({ ...prev, [comp]: false }));
      setConnFeedback((prev) => ({ ...prev, [comp]: { ok: true, msg: "已更新" } }));
      setTimeout(() => setConnFeedback((prev) => ({ ...prev, [comp]: null })), 2000);
    } catch (e) {
      setConnFeedback((prev) => ({
        ...prev,
        [comp]: { ok: false, msg: e instanceof Error ? e.message : "保存失败" },
      }));
    } finally {
      setConnSaving((prev) => ({ ...prev, [comp]: false }));
    }
  };

  // ── Container domain handlers ─────────────────────────────────────────────

  const formKey = (comp: string, domain: string) => `${comp}:${domain}`;

  const getFormValue = (comp: string, domain: string, field: string) => {
    const key = formKey(comp, domain);
    if (forms[key]?.[field] !== undefined) return forms[key][field];
    const status = statuses.find((s) => s.component === comp);
    const domainCfg = status?.domains.find((d) => d.domain === domain);
    return domainCfg?.config[field] ?? "";
  };

  const setFormValue = useCallback((comp: string, domain: string, field: string, value: string) => {
    const key = formKey(comp, domain);
    setForms((prev) => ({ ...prev, [key]: { ...prev[key], [field]: value } }));
  }, []);

  const pollUntilHealthy = useCallback((comp: string, maxAttempts = 8, interval = 3000) => {
    let attempt = 0;
    const tick = async () => {
      attempt++;
      try {
        const s = await api.components.health(comp);
        if (s.health.healthy) {
          setFeedback({ key: comp, ok: true, msg: "重启成功，服务已恢复在线" });
          void load(); // refresh statuses once healthy
          return;
        }
      } catch { /* health check failed, keep polling */ }
      if (attempt >= maxAttempts) {
        setFeedback({ key: comp, ok: false, msg: "健康检查超时，服务可能仍在启动中" });
        return;
      }
      setTimeout(tick, interval);
    };
    setTimeout(tick, interval);
  }, [load]);

  const handleSave = async (comp: string, domain: ConfigDomain) => {
    const key = formKey(comp, domain.domain);
    const values = forms[key];
    if (!values || Object.keys(values).length === 0) return;
    setSaving(key);
    setFeedback(null);
    try {
      await api.components.saveConfig(comp, domain.domain, values);
      setForms((prev) => ({ ...prev, [key]: {} }));
      await load();
      setFeedback({ key, ok: true, msg: "配置已保存" });
    } catch (e) {
      setFeedback({ key, ok: false, msg: e instanceof Error ? e.message : "保存失败" });
    } finally {
      setSaving(null);
    }
  };

  const handleApplyRestart = async (comp: string) => {
    setApplying(comp);
    setFeedback(null);
    try {
      const result = await api.components.applyRestart(comp);
      setFeedback({ key: comp, ok: result.success, msg: result.success ? `${result.message}，等待健康检查...` : result.message });
      if (result.success) pollUntilHealthy(comp);
    } catch (e) {
      setFeedback({ key: comp, ok: false, msg: e instanceof Error ? e.message : "操作失败" });
    } finally {
      setApplying(null);
    }
  };

  // ── Derived data ──────────────────────────────────────────────────────────

  // Section A: all contracts with a connection domain
  const toolContracts = contracts.filter((c) =>
    c.domains.some((d) => d.domain === "connection")
  );

  // Section B: group container-target domains by component (deepwiki handled in settings page)
  const componentGroups = useMemo(() => {
    const groups: { contract: ComponentContract; domains: ConfigDomain[] }[] = [];
    for (const contract of contracts) {
      if (contract.component === "deepwiki") continue;
      const domains = contract.domains.filter(
        (d) => d.domain !== "connection" && d.target !== "backend"
      );
      if (domains.length > 0) groups.push({ contract, domains });
    }
    return groups;
  }, [contracts]);

  const hasDomainChanges = useCallback((comp: string, dom: string) => {
    const key = formKey(comp, dom);
    return !!(forms[key] && Object.keys(forms[key]).length > 0);
  }, [forms]);

  // ── Render ────────────────────────────────────────────────────────────────

  if (loadError) {
    return (
      <GlassPanel className="bg-tertiary-container/20 border-tertiary/30 py-6 flex flex-col items-center gap-3">
        <p className="text-sm text-tertiary">{loadError}</p>
        <button
          onClick={() => { void load(); }}
          className="px-3 py-1.5 rounded-lg border border-primary/20 bg-primary/10 text-primary text-xs font-bold uppercase tracking-widest hover:bg-primary/15 transition-colors"
        >
          重试
        </button>
      </GlassPanel>
    );
  }

  if (contracts.length === 0) return null;

  return (
    <div className="space-y-4">
      {/* Section A: Tool Connections */}
      {toolContracts.length > 0 && (
        <GlassPanel>
          <div className="flex items-center gap-2 mb-3">
            <h3 className="text-sm font-medium text-on-surface">工具连接</h3>
            <span className="font-mono text-[10px] text-on-surface-variant/30">// RUNTIME_ENDPOINTS</span>
          </div>
          <div className="divide-y divide-outline-variant/10">
            {toolContracts.map((contract) => {
              const status = statuses.find((s) => s.component === contract.component);
              return (
                <ToolRow
                  key={contract.component}
                  contract={contract}
                  status={status}
                  value={connValues[contract.component] ?? ""}
                  dirty={connDirty[contract.component] ?? false}
                  saving={connSaving[contract.component] ?? false}
                  feedback={connFeedback[contract.component] ?? null}
                  onChange={(v) => handleConnChange(contract.component, v)}
                  onSave={() => handleConnSave(contract.component)}
                />
              );
            })}
          </div>
        </GlassPanel>
      )}

      {/* Section B: AI Model Config — one card per component */}
      {componentGroups.length > 0 && (
        <GlassPanel>
          <div className="flex items-center gap-2 mb-4">
            <h3 className="text-sm font-medium text-on-surface">AI 模型配置</h3>
            <span className="font-mono text-[10px] text-on-surface-variant/30">// SYSTEM_COMPONENTS</span>
          </div>
          <div className="space-y-4">
            {componentGroups.map(({ contract, domains }) => (
              <ComponentCard
                key={contract.component}
                contract={contract}
                domains={domains}
                getFormValue={getFormValue}
                setFormValue={setFormValue}
                hasDomainChanges={hasDomainChanges}
                savingKey={saving}
                applying={applying === contract.component}
                feedback={feedback?.key === contract.component || domains.some(
                  (d) => feedback?.key === formKey(contract.component, d.domain)
                )
                  ? { ok: feedback!.ok, msg: feedback!.msg }
                  : null
                }
                onSaveDomain={(domain) => handleSave(contract.component, domain)}
                onApplyRestart={() => handleApplyRestart(contract.component)}
              />
            ))}
          </div>
        </GlassPanel>
      )}
    </div>
  );
}
