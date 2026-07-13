"use client";

import { Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import type {
  WorkflowCapabilities,
  WorkflowGraphNode,
  WorkflowProviderCapability,
} from "@/lib/types/workflow";
import { nodeKindLabel } from "../workflow-graph";

interface Props {
  node: WorkflowGraphNode;
  capabilities: WorkflowCapabilities | null;
  providers: WorkflowProviderCapability[];
  onChange: (node: WorkflowGraphNode) => void;
  onClose: () => void;
}

export function NodeInspector({ node, capabilities, providers, onChange, onClose }: Props) {
  const config = node.config;
  const updateConfig = (patch: Record<string, unknown>) =>
    onChange({ ...node, config: { ...config, ...patch } });
  const updateLabel = (label: string) =>
    onChange({ ...node, label, config: { ...config, label } });
  const selectedProvider = providers.find((item) => item.provider === config.provider);
  const mcpOptions = useMemo(
    () => selectedProvider?.capabilities?.mcp_profiles ?? [],
    [selectedProvider],
  );
  const skillOptions = capabilities?.skill_catalog ?? [];

  return (
    <aside className="ct-v2-inspector" aria-label="节点属性">
      <div className="ct-v2-inspector-header">
        <div>
          <small>{nodeKindLabel(node.kind)}节点</small>
          <strong>{node.label}</strong>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭属性面板" title="关闭属性面板">
          <X size={15} />
        </button>
      </div>
      <div className="ct-v2-inspector-scroll">
        <InspectorSection title="基础信息">
          <Field label="节点名称">
            <input value={node.label} onChange={(event) => updateLabel(event.target.value)} />
          </Field>
          <Field label="节点 ID">
            <input value={node.id} readOnly aria-readonly="true" />
          </Field>
        </InspectorSection>

        {node.kind === "input" && (
          <InspectorSection title="输入契约">
            <Field label="输入 ID">
              <input value={String(config.contract_id ?? node.id)} onChange={(event) => updateConfig({ contract_id: event.target.value })} />
            </Field>
            <Field label="类型">
              <select value={String(config.type ?? "text")} onChange={(event) => updateConfig({ type: event.target.value })}>
                {(capabilities?.input_types ?? ["text", "file", "directory", "mr_link"]).map((item) => <option key={item}>{item}</option>)}
              </select>
            </Field>
            <Field label="获取方式">
              <select value={String(config.resolver ?? "manual")} onChange={(event) => updateConfig({ resolver: event.target.value })}>
                {(capabilities?.input_resolvers ?? ["manual", "workspace", "local", "agent_mcp"]).map((item) => <option key={item}>{item}</option>)}
              </select>
            </Field>
            <Toggle label="必填" checked={Boolean(config.required)} onChange={(checked) => updateConfig({ required: checked })} />
            <Toggle label="工作流全局输入" checked={Boolean(config.global_input)} onChange={(checked) => updateConfig({ global_input: checked })} />
            <Field label="填写提示">
              <textarea rows={3} value={String(config.role ?? "")} onChange={(event) => updateConfig({ role: event.target.value })} />
            </Field>
          </InspectorSection>
        )}

        {node.kind === "agent" && (
          <>
            <InspectorSection title="执行配置">
              <Field label="分析目标">
                <textarea rows={5} value={String(config.goal ?? "")} onChange={(event) => updateConfig({ goal: event.target.value })} />
              </Field>
              <Field label="执行器">
                <select value={String(config.provider ?? "builtin-llm")} onChange={(event) => updateConfig({ provider: event.target.value, mcp_profiles: [] })}>
                  {providers.map((provider) => (
                    <option key={provider.provider} value={provider.provider}>
                      {provider.display_name} · {provider.provider}{provider.status === "unavailable" ? "（不可用）" : ""}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="超时（秒）">
                <input type="number" min={30} max={7200} value={Number(config.timeout_sec ?? 900)} onChange={(event) => updateConfig({ timeout_sec: Number(event.target.value) })} />
              </Field>
              <Field label="无输出超时（秒）">
                <input type="number" min={0} max={1800} value={Number(config.idle_timeout_sec ?? 120)} onChange={(event) => updateConfig({ idle_timeout_sec: Number(event.target.value) })} />
              </Field>
              <Field label="失败策略">
                <select value={String(config.failure_policy ?? "stop")} onChange={(event) => updateConfig({ failure_policy: event.target.value })}>
                  <option value="stop">停止工作流</option>
                  <option value="continue_independent">继续独立分支</option>
                </select>
              </Field>
            </InspectorSection>
            <InspectorSection title="Skills">
              <SearchMultiSelect
                selected={config.skill_ids ?? []}
                options={skillOptions.map((item) => ({ id: item.id, label: item.label, detail: item.description }))}
                emptyText="没有匹配的 Skill"
                onChange={(skill_ids) => updateConfig({ skill_ids })}
              />
            </InspectorSection>
            <InspectorSection title="MCP">
              <SearchMultiSelect
                selected={config.mcp_profiles ?? []}
                options={mcpOptions.map((id) => ({ id, label: id }))}
                emptyText={selectedProvider ? "该执行器没有已配置 MCP" : "先选择执行器"}
                onChange={(mcp_profiles) => updateConfig({ mcp_profiles })}
              />
            </InspectorSection>
            <InspectorSection title="产物契约">
              <Field label="必须生成的文件">
                <textarea
                  rows={3}
                  value={(config.required_artifacts ?? []).join("\n")}
                  onChange={(event) => updateConfig({ required_artifacts: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean) })}
                  placeholder="report.md"
                />
              </Field>
            </InspectorSection>
          </>
        )}

        {node.kind === "output" && (
          <InspectorSection title="输出契约">
            <Field label="输出 ID">
              <input value={String(config.output_id ?? node.id)} onChange={(event) => updateConfig({ output_id: event.target.value })} />
            </Field>
            <Field label="输出类型">
              <select value={String(config.type ?? "markdown")} onChange={(event) => updateConfig({ type: event.target.value })}>
                {(capabilities?.output_types ?? ["markdown", "json", "test_cases"]).map((item) => <option key={item}>{item}</option>)}
              </select>
            </Field>
            <Field label="文件名">
              <input value={String(config.artifact ?? "")} onChange={(event) => updateConfig({ artifact: event.target.value })} placeholder="report.md" />
            </Field>
            <Toggle label="必需交付" checked={Boolean(config.required)} onChange={(checked) => updateConfig({ required: checked })} />
            <Toggle label="写入证据库" checked={Boolean(config.evidence_memory)} onChange={(checked) => updateConfig({ evidence_memory: checked })} />
            <Toggle label="导入测试语义库" checked={Boolean(config.semantic_import)} onChange={(checked) => updateConfig({ semantic_import: checked })} />
          </InspectorSection>
        )}

        {node.kind !== "input" && node.kind !== "output" && node.kind !== "agent" && (
          <InspectorSection title="内置步骤">
            <p className="ct-v2-inspector-note">
              该节点由 CodeTalk 后端执行。输入和依赖通过画布连线指定，不需要填写 JSON。
            </p>
          </InspectorSection>
        )}
      </div>
    </aside>
  );
}

function InspectorSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="ct-v2-inspector-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="ct-v2-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="ct-v2-toggle">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

function SearchMultiSelect({
  selected,
  options,
  emptyText,
  onChange,
}: {
  selected: string[];
  options: Array<{ id: string; label: string; detail?: string }>;
  emptyText: string;
  onChange: (selected: string[]) => void;
}) {
  const [query, setQuery] = useState("");
  const visible = options
    .filter((item) => `${item.label} ${item.id} ${item.detail ?? ""}`.toLowerCase().includes(query.toLowerCase()))
    .slice(0, 30);
  return (
    <div className="ct-v2-multiselect">
      <label>
        <Search size={13} aria-hidden="true" />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索" aria-label="搜索选项" />
      </label>
      <div className="ct-v2-selected-tags">
        {selected.map((id) => (
          <button key={id} type="button" onClick={() => onChange(selected.filter((item) => item !== id))} title={`移除 ${id}`}>
            {options.find((item) => item.id === id)?.label ?? id}<X size={11} />
          </button>
        ))}
      </div>
      <div className="ct-v2-option-list">
        {visible.length ? visible.map((item) => (
          <label key={item.id}>
            <input
              type="checkbox"
              checked={selected.includes(item.id)}
              onChange={() => onChange(selected.includes(item.id) ? selected.filter((value) => value !== item.id) : [...selected, item.id])}
            />
            <span><strong>{item.label}</strong><small>{item.id}</small></span>
          </label>
        )) : <p>{emptyText}</p>}
      </div>
    </div>
  );
}
