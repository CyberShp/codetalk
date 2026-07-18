"use client";

import { Plus, Search, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import type {
  WorkflowCapabilities,
  WorkflowGraphNode,
  WorkflowPortDefinition,
  WorkflowProviderCapability,
} from "@/lib/types/workflow";
import { nodeKindLabel, validateInputPortId } from "../workflow-graph";

const MINDMAP_ARTIFACTS = [
  "test_design_mindmap.json",
  "test_design_mindmap.html",
  "test_design_mindmap.svg",
] as const;

const outputTypeLabels: Record<string, string> = {
  markdown: "Markdown 报告",
  json: "JSON 数据",
  text: "纯文本",
  patch: "补丁文件",
  diff: "差异文件",
  test_cases: "测试用例",
  scope_report: "范围报告",
  test_design_mindmap: "测试设计脑图",
};

interface Props {
  node: WorkflowGraphNode;
  capabilities: WorkflowCapabilities | null;
  providers: WorkflowProviderCapability[];
  onChange: (node: WorkflowGraphNode, portMutation?: InputPortMutation) => void;
  onClose: () => void;
}

export type InputPortMutation =
  | { kind: "rename"; oldId: string; newId: string }
  | { kind: "delete"; oldId: string };

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
            <InspectorSection title="输入端口">
              <p className="ct-v2-inspector-note">
                为源码、文档、链接等输入分别创建端口，再在画布上逐一连线。
              </p>
              <InputPortsEditor
                ports={config.input_ports ?? []}
                typeOptions={capabilities?.input_types ?? ["text", "file", "directory", "mr_link"]}
                onChange={(input_ports, mutation) =>
                  onChange({ ...node, config: { ...config, input_ports } }, mutation)
                }
              />
            </InspectorSection>
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
              <select
                value={String(config.type ?? "markdown")}
                onChange={(event) => {
                  const type = event.target.value;
                  updateConfig(
                    type === "test_design_mindmap"
                      ? {
                          type,
                          artifact: MINDMAP_ARTIFACTS[0],
                          companion_artifacts: MINDMAP_ARTIFACTS.slice(1),
                          required: true,
                        }
                      : { type, companion_artifacts: undefined },
                  );
                }}
              >
                {(capabilities?.output_types ?? ["markdown", "json", "test_cases", "test_design_mindmap"]).map((item) => (
                  <option key={item} value={item}>{outputTypeLabels[item] ?? item}</option>
                ))}
              </select>
            </Field>
            <Field label="文件名">
              <input
                value={String(config.artifact ?? "")}
                readOnly={config.type === "test_design_mindmap"}
                onChange={(event) => updateConfig({ artifact: event.target.value })}
                placeholder="report.md"
              />
            </Field>
            {config.type === "test_design_mindmap" && (
              <p className="ct-v2-inspector-note">
                仅适用于内置模型分阶段分析节点。系统会自动生成 {MINDMAP_ARTIFACTS.join("、")}，JSON 是唯一结构化事实源。
              </p>
            )}
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

function InputPortsEditor({
  ports,
  typeOptions,
  onChange,
}: {
  ports: WorkflowPortDefinition[];
  typeOptions: string[];
  onChange: (ports: WorkflowPortDefinition[], mutation?: InputPortMutation) => void;
}) {
  const [draftIds, setDraftIds] = useState<Record<number, string>>({});
  const [idErrors, setIdErrors] = useState<Record<number, string>>({});
  const update = (
    index: number,
    patch: Partial<WorkflowPortDefinition>,
    mutation?: InputPortMutation,
  ) => onChange(ports.map((port, itemIndex) => itemIndex === index ? { ...port, ...patch } : port), mutation);
  const uniqueId = () => {
    let index = ports.length + 1;
    while (ports.some((port) => port.id === `input_${index}`)) index += 1;
    return `input_${index}`;
  };
  const commitId = (index: number) => {
    const current = ports[index];
    if (!current) return;
    const candidate = (draftIds[index] ?? current.id).trim();
    const error = validateInputPortId(candidate, ports, index);
    setIdErrors((items) => ({ ...items, [index]: error }));
    if (error || candidate === current.id) return;
    update(index, { id: candidate }, { kind: "rename", oldId: current.id, newId: candidate });
    setDraftIds((items) => {
      const next = { ...items };
      delete next[index];
      return next;
    });
  };
  return (
    <div className="ct-v2-port-editor">
      {ports.map((port, index) => (
        <div className="ct-v2-port-editor-row" key={index}>
          <label>
            <span>端口名称</span>
            <input
              value={draftIds[index] ?? port.id}
              aria-label={`输入端口 ${index + 1} 名称`}
              onChange={(event) => {
                const value = event.target.value;
                setDraftIds((items) => ({ ...items, [index]: value }));
                setIdErrors((items) => ({
                  ...items,
                  [index]: validateInputPortId(value, ports, index),
                }));
              }}
              onBlur={() => commitId(index)}
              onKeyDown={(event) => {
                if (event.key === "Enter") event.currentTarget.blur();
              }}
              aria-invalid={Boolean(idErrors[index])}
            />
            {idErrors[index] && <small className="ct-v2-port-id-error" role="alert">{idErrors[index]}</small>}
          </label>
          <label>
            <span>类型</span>
            <select
              value={port.type || "text"}
              aria-label={`输入端口 ${index + 1} 类型`}
              onChange={(event) => update(index, { type: event.target.value })}
            >
              {Array.from(new Set([...typeOptions, port.type || "text"])).map((type) => (
                <option key={type} value={type}>{type}</option>
              ))}
            </select>
          </label>
          <label className="ct-v2-port-required">
            <input
              type="checkbox"
              checked={Boolean(port.required)}
              onChange={(event) => update(index, { required: event.target.checked })}
            />
            <span>必填</span>
          </label>
          <button
            type="button"
            className="ct-v2-icon-danger"
            aria-label={`删除输入端口 ${port.id}`}
            title="删除输入端口"
            onClick={() => {
              setDraftIds({});
              setIdErrors({});
              onChange(ports.filter((_, itemIndex) => itemIndex !== index), { kind: "delete", oldId: port.id });
            }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button
        type="button"
        className="ct-v2-add-port"
        onClick={() => onChange([...ports, { id: uniqueId(), type: "file", required: false }])}
      >
        <Plus size={14} />
        增加输入端口
      </button>
    </div>
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
