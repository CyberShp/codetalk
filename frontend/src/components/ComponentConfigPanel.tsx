"use client";

import { useState, useEffect, useCallback } from "react";
import GlassPanel from "@/components/ui/GlassPanel";
import CyberInput from "@/components/ui/CyberInput";
import StatusBadge from "@/components/ui/StatusBadge";
import { usePageRestoreRefresh } from "@/hooks/usePageRestoreRefresh";
import { api } from "@/lib/api";
import type {
  ComponentContract,
  ComponentStatus,
  ConfigDomain,
} from "@/lib/types";

export default function ComponentConfigPanel() {
  const [contracts, setContracts] = useState<ComponentContract[]>([]);
  const [statuses, setStatuses] = useState<ComponentStatus[]>([]);
  const [forms, setForms] = useState<Record<string, Record<string, string>>>(
    {}
  );
  const [saving, setSaving] = useState<string | null>(null);
  const [applying, setApplying] = useState<string | null>(null);
  const [restarting, setRestarting] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{
    key: string;
    ok: boolean;
    msg: string;
  } | null>(null);

  const [loadError, setLoadError] = useState("");

  const load = useCallback(async () => {
    setLoadError("");
    try {
      const [c, s] = await Promise.all([
        api.components.contracts(),
        api.components.list(),
      ]);
      setContracts(c);
      setStatuses(s);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "组件配置加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);
  usePageRestoreRefresh(() => {
    void load();
  });

  const formKey = (comp: string, domain: string) => `${comp}:${domain}`;

  const getFormValue = (comp: string, domain: string, field: string) => {
    const key = formKey(comp, domain);
    if (forms[key]?.[field] !== undefined) return forms[key][field];
    const status = statuses.find((s) => s.component === comp);
    const domainCfg = status?.domains.find((d) => d.domain === domain);
    return domainCfg?.config[field] ?? "";
  };

  const setFormValue = (
    comp: string,
    domain: string,
    field: string,
    value: string
  ) => {
    const key = formKey(comp, domain);
    setForms((prev) => ({
      ...prev,
      [key]: { ...prev[key], [field]: value },
    }));
  };

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
      setFeedback({
        key,
        ok: false,
        msg: e instanceof Error ? e.message : "保存失败",
      });
    } finally {
      setSaving(null);
    }
  };

  const handleApplyRestart = async (comp: string) => {
    setApplying(comp);
    setFeedback(null);
    try {
      const result = await api.components.applyRestart(comp);
      setFeedback({
        key: comp,
        ok: result.success,
        msg: result.message,
      });
      if (result.success) {
        setTimeout(load, 3000);
      }
    } catch (e) {
      setFeedback({
        key: comp,
        ok: false,
        msg: e instanceof Error ? e.message : "操作失败",
      });
    } finally {
      setApplying(null);
    }
  };

  const handleRestart = async (comp: string) => {
    setRestarting(comp);
    setFeedback(null);
    try {
      const result = await api.components.restart(comp);
      setFeedback({
        key: comp,
        ok: result.success,
        msg: result.message,
      });
      if (result.success) {
        setTimeout(load, 3000);
      }
    } catch (e) {
      setFeedback({
        key: comp,
        ok: false,
        msg: e instanceof Error ? e.message : "重启失败",
      });
    } finally {
      setRestarting(null);
    }
  };

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
    <GlassPanel>
      <h3 className="text-sm font-medium text-on-surface mb-4">
        组件配置
      </h3>
      <p className="text-xs text-on-surface-variant mb-6">
        配置各组件的 AI 服务端点。保存后点击「应用并重启」使配置生效。
      </p>

      <div className="space-y-6">
        {contracts.map((contract) => {
          const status = statuses.find(
            (s) => s.component === contract.component
          );
          const isApplying = applying === contract.component;
          const isRestarting = restarting === contract.component;

          return (
            <div
              key={contract.component}
              className="border border-outline-variant/10 rounded-lg p-4"
            >
              {/* Component header */}
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <h4 className="text-sm font-medium text-on-surface">
                    {contract.label}
                  </h4>
                  <StatusBadge
                    status={
                      status?.health.healthy ? "online" : "offline"
                    }
                  />
                </div>
                <div className="flex items-center gap-2">
                  {contract.domains.length > 0 && (
                    <button
                      onClick={() =>
                        handleApplyRestart(contract.component)
                      }
                      disabled={isApplying}
                      className="px-3 py-1.5 text-xs rounded-md bg-primary-container/80 text-primary hover:bg-primary-container transition-colors disabled:opacity-40"
                    >
                      {isApplying ? "应用中..." : "应用并重启"}
                    </button>
                  )}
                  <button
                    onClick={() => handleRestart(contract.component)}
                    disabled={isRestarting}
                    className="px-3 py-1.5 text-xs rounded-md bg-surface-container-high text-on-surface-variant hover:text-on-surface transition-colors disabled:opacity-40"
                  >
                    {isRestarting ? "重启中..." : "重启"}
                  </button>
                </div>
              </div>

              {/* Feedback for this component */}
              {feedback?.key === contract.component && (
                <p
                  className={`text-[11px] mb-3 ${feedback.ok ? "text-secondary-fixed-dim" : "text-tertiary"}`}
                >
                  {feedback.msg}
                </p>
              )}

              {/* Status info */}
              {status && (
                <div className="flex gap-4 text-[10px] text-on-surface-variant/60 mb-4">
                  <span>
                    容器:{" "}
                    {status.health.container_status ?? "unknown"}
                  </span>
                  {status.health.version && (
                    <span>版本: {status.health.version}</span>
                  )}
                  {status.domains
                    .filter((d) => d.applied_at)
                    .map((d) => (
                      <span key={d.domain}>
                        {d.domain} 上次应用:{" "}
                        {new Date(d.applied_at!).toLocaleString()}
                      </span>
                    ))}
                </div>
              )}

              {/* Config domains */}
              {contract.domains.length === 0 ? (
                <p className="text-xs text-on-surface-variant/40">
                  该组件无需 AI 配置
                </p>
              ) : (
                <div className="space-y-4">
                  {contract.domains.map((domain) => {
                    const key = formKey(
                      contract.component,
                      domain.domain
                    );
                    const isSaving = saving === key;
                    const hasChanges =
                      forms[key] &&
                      Object.keys(forms[key]).length > 0;

                    return (
                      <div
                        key={domain.domain}
                        className="bg-surface-container-lowest/30 rounded-md p-3"
                      >
                        <h5 className="text-xs text-on-surface-variant mb-3 uppercase tracking-wider">
                          {domain.label}
                        </h5>
                        <div className="grid grid-cols-2 gap-3">
                          {domain.fields.map((field) => {
                            if (field.field_type === "select") {
                              return (
                                <div key={field.name}>
                                  <label className="block text-xs text-on-surface-variant mb-1.5">
                                    {field.label}
                                  </label>
                                  <select
                                    value={getFormValue(
                                      contract.component,
                                      domain.domain,
                                      field.name
                                    )}
                                    onChange={(e) =>
                                      setFormValue(
                                        contract.component,
                                        domain.domain,
                                        field.name,
                                        e.target.value
                                      )
                                    }
                                    className="w-full bg-surface-container-lowest/50 text-on-surface font-data text-sm px-4 py-2 rounded-md outline-none"
                                  >
                                    <option value="">选择...</option>
                                    {field.options?.map((opt) => (
                                      <option key={opt} value={opt}>
                                        {opt}
                                      </option>
                                    ))}
                                  </select>
                                </div>
                              );
                            }
                            return (
                              <CyberInput
                                key={field.name}
                                label={field.label}
                                type={
                                  field.field_type === "secret"
                                    ? "password"
                                    : "text"
                                }
                                placeholder={
                                  field.placeholder ?? ""
                                }
                                value={getFormValue(
                                  contract.component,
                                  domain.domain,
                                  field.name
                                )}
                                onChange={(e) =>
                                  setFormValue(
                                    contract.component,
                                    domain.domain,
                                    field.name,
                                    e.target.value
                                  )
                                }
                              />
                            );
                          })}
                        </div>
                        <div className="flex items-center gap-3 mt-3">
                          <button
                            onClick={() =>
                              handleSave(contract.component, domain)
                            }
                            disabled={isSaving || !hasChanges}
                            className="px-3 py-1.5 text-xs rounded-md bg-surface-container-high text-on-surface-variant hover:text-on-surface transition-colors disabled:opacity-30"
                          >
                            {isSaving ? "保存中..." : "保存"}
                          </button>
                          {feedback?.key === key && (
                            <span
                              className={`text-[11px] ${feedback.ok ? "text-secondary-fixed-dim" : "text-tertiary"}`}
                            >
                              {feedback.msg}
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </GlassPanel>
  );
}
