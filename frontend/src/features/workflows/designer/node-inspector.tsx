"use client";

import { Plus, Search, Trash2, X } from "lucide-react";
import { useState } from "react";
import type {
  WorkflowCapabilities,
  WorkflowGraphNode,
  WorkflowNodeRegistry,
  WorkflowNodeRegistryEntry,
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
  registry: WorkflowNodeRegistry;
  onChange: (node: WorkflowGraphNode, portMutation?: PortMutation) => void;
  onClose: () => void;
}

export type PortMutation =
  | { direction: "input" | "output"; kind: "rename"; oldId: string; newId: string }
  | { direction: "input" | "output"; kind: "delete"; oldId: string };

export function NodeInspector({ node, capabilities, providers, registry, onChange, onClose }: Props) {
  const config = node.config;
  const updateConfig = (patch: Record<string, unknown>) =>
    onChange({ ...node, config: { ...config, ...patch } });
  const updateLabel = (label: string) =>
    onChange({ ...node, label, config: { ...config, label } });
  const definition = registry.nodes.find((item) => item.kind === node.kind);

  return (
    <aside className="ct-v2-inspector" aria-label="节点属性">
      <div className="ct-v2-inspector-header">
        <div>
          <small>{definition?.ui.label ?? nodeKindLabel(node.kind)}节点</small>
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

        {definition && (
          <RegistryContractSummary definition={definition} />
        )}

        {definition && (
          <InspectorSection title={node.kind === "agent" ? "执行配置" : node.kind === "input" ? "输入契约" : node.kind === "output" ? "输出契约" : "内置步骤"}>
            {node.kind !== "input" && node.kind !== "output" && (
              <p className="ct-v2-inspector-note">输入和依赖通过画布连线指定。以下字段由后端节点定义驱动，无需填写 JSON。</p>
            )}
            <RegistryConfigFields
              definition={definition}
              node={node}
              config={config}
              capabilities={capabilities}
              providers={providers}
              onChange={updateConfig}
              onPortMutation={(key, ports, mutation) => onChange({ ...node, config: { ...config, [key]: ports } }, mutation)}
            />
          </InspectorSection>
        )}
      </div>
    </aside>
  );
}

type RegistryField = {
  type?: string;
  label?: string;
  minimum?: number;
  options?: Array<{ value: string; label: string }>;
};

function RegistryConfigFields({
  definition,
  node,
  config,
  capabilities,
  providers,
  onChange,
  onPortMutation,
}: {
  definition: WorkflowNodeRegistryEntry;
  node: WorkflowGraphNode;
  config: WorkflowGraphNode["config"];
  capabilities: WorkflowCapabilities | null;
  providers: WorkflowProviderCapability[];
  onChange: (patch: Record<string, unknown>) => void;
  onPortMutation: (key: string, ports: WorkflowPortDefinition[], mutation: PortMutation) => void;
}) {
  const fieldOrder = definition.ui_schema?.inspector?.field_order ?? [];
  const fields = Object.entries(definition.config_schema)
    .map(([key, value]) => [key, value as RegistryField] as const)
    .filter(([, field]) => Boolean(field.type))
    .sort(([left], [right]) => {
      const leftIndex = fieldOrder.indexOf(left);
      const rightIndex = fieldOrder.indexOf(right);
      return (leftIndex < 0 ? Number.MAX_SAFE_INTEGER : leftIndex) - (rightIndex < 0 ? Number.MAX_SAFE_INTEGER : rightIndex);
    });
  if (!fields.length) return null;
  return (
    <div className="ct-v2-registry-fields">
      {fields.map(([key, field]) => {
        const label = field.label ?? key;
        const type = field.type ?? "string";
        if (field.type === "boolean") {
          return <Toggle key={key} label={label} checked={Boolean(config[key])} onChange={(checked) => onChange({ [key]: checked })} />;
        }
        if (field.type === "enum") {
          return (
            <Field key={key} label={label}>
              <select value={String(config[key] ?? "")} onChange={(event) => onChange({ [key]: event.target.value })}>
                {(field.options ?? []).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </Field>
          );
        }
        if (type === "multiline") {
          return <Field key={key} label={label}><textarea rows={key === "goal" ? 5 : 3} value={String(config[key] ?? "")} onChange={(event) => onChange({ [key]: event.target.value })} /></Field>;
        }
        if (type === "port_type") {
          return <Field key={key} label={label}><select value={String(config[key] ?? "text")} onChange={(event) => onChange({ [key]: event.target.value })}>{(capabilities?.input_types ?? ["text", "file", "directory", "mr_link"]).map((item) => <option key={item} value={item}>{item}</option>)}</select></Field>;
        }
        if (type === "input_resolver") {
          return <Field key={key} label={label}><select value={String(config[key] ?? "manual")} onChange={(event) => onChange({ [key]: event.target.value })}>{(capabilities?.input_resolvers ?? ["manual", "workspace", "local", "agent_mcp"]).map((item) => <option key={item} value={item}>{item}</option>)}</select></Field>;
        }
        if (type === "output_type") {
          return <Field key={key} label={label}><select value={String(config[key] ?? "markdown")} onChange={(event) => {
            const outputType = event.target.value;
            onChange(outputType === "test_design_mindmap" ? { type: outputType, artifact: MINDMAP_ARTIFACTS[0], companion_artifacts: MINDMAP_ARTIFACTS.slice(1), required: true } : { type: outputType, companion_artifacts: undefined });
          }}>{(capabilities?.output_types ?? ["markdown", "json", "test_cases", "test_design_mindmap"]).map((item) => <option key={item} value={item}>{outputTypeLabels[item] ?? item}</option>)}</select></Field>;
        }
        if (type === "provider") {
          return <Field key={key} label={label}><select value={String(config[key] ?? "builtin-llm")} onChange={(event) => onChange({ provider: event.target.value, mcp_profiles: [] })}>{providers.map((provider) => <option key={provider.provider} value={provider.provider}>{provider.display_name} · {provider.provider}{provider.status === "unavailable" ? "（不可用）" : ""}</option>)}</select></Field>;
        }
        if (type === "skill_multiselect") {
          return <SearchMultiSelect key={key} selected={stringList(config[key])} options={(capabilities?.skill_catalog ?? []).map((item) => ({ id: item.id, label: item.label, detail: item.description }))} emptyText="没有匹配的 Skill" onChange={(value) => onChange({ [key]: value })} />;
        }
        if (type === "mcp_multiselect") {
          const provider = providers.find((item) => item.provider === config.provider);
          const options = provider?.capabilities?.mcp_profiles ?? [];
          return <SearchMultiSelect key={key} selected={stringList(config[key])} options={options.map((id) => ({ id, label: id }))} emptyText={provider ? "该执行器没有已配置 MCP" : "先选择执行器"} onChange={(value) => onChange({ [key]: value })} />;
        }
        if (type === "artifact_list") {
          return <Field key={key} label={label}><textarea rows={3} value={stringList(config[key]).join("\n")} onChange={(event) => onChange({ [key]: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean) })} placeholder="report.md" /></Field>;
        }
        if (type === "port_list") {
          const direction = key === "output_ports" ? "output" : "input";
          return <PortListEditor key={key} direction={direction} ports={portList(config[key])} typeOptions={capabilities?.input_types ?? ["text", "file", "directory", "mr_link"]} onChange={(ports, mutation) => {
            if (mutation) onPortMutation(key, ports, mutation);
            else onChange({ [key]: ports });
          }} />;
        }
        return (
          <Field key={key} label={label}>
            <input
              type={type === "integer" ? "number" : "text"}
              min={field.minimum}
              value={String(config[key] ?? "")}
              readOnly={type === "artifact_name" && node.kind === "output" && config.type === "test_design_mindmap"}
              onChange={(event) => onChange({ [key]: type === "integer" ? Number(event.target.value) : event.target.value })}
            />
          </Field>
        );
      })}
    </div>
  );
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

function portList(value: unknown): WorkflowPortDefinition[] {
  return Array.isArray(value)
    ? value.filter((item): item is WorkflowPortDefinition => Boolean(item) && typeof item === "object")
    : [];
}

function RegistryContractSummary({ definition }: { definition: WorkflowNodeRegistry["nodes"][number] }) {
  const inputSummary = definition.default_ports.input_ports.map((port) => `${port.id} · ${port.type}`).join("，") || "无";
  const outputSummary = definition.default_ports.output_ports.map((port) => `${port.id} · ${port.type}`).join("，") || "无";
  return (
    <InspectorSection title="节点定义">
      <p className="ct-v2-inspector-note">{definition.ui.description}</p>
      <dl className="ct-v2-registry-contract">
        <div><dt>节点版本</dt><dd>v{definition.version}</dd></div>
        <div><dt>默认输入</dt><dd>{inputSummary}</dd></div>
        <div><dt>默认输出</dt><dd>{outputSummary}</dd></div>
      </dl>
    </InspectorSection>
  );
}

function PortListEditor({
  direction,
  ports,
  typeOptions,
  onChange,
}: {
  direction: "input" | "output";
  ports: WorkflowPortDefinition[];
  typeOptions: string[];
  onChange: (ports: WorkflowPortDefinition[], mutation?: PortMutation) => void;
}) {
  const [draftIds, setDraftIds] = useState<Record<number, string>>({});
  const [idErrors, setIdErrors] = useState<Record<number, string>>({});
  const update = (
    index: number,
    patch: Partial<WorkflowPortDefinition>,
    mutation?: PortMutation,
  ) => onChange(ports.map((port, itemIndex) => itemIndex === index ? { ...port, ...patch } : port), mutation);
  const uniqueId = () => {
    let index = ports.length + 1;
    const prefix = direction === "input" ? "input" : "output";
    while (ports.some((port) => port.id === `${prefix}_${index}`)) index += 1;
    return `${prefix}_${index}`;
  };
  const commitId = (index: number) => {
    const current = ports[index];
    if (!current) return;
    const candidate = (draftIds[index] ?? current.id).trim();
    const error = validateInputPortId(candidate, ports, index);
    setIdErrors((items) => ({ ...items, [index]: error }));
    if (error || candidate === current.id) return;
    update(index, { id: candidate }, { direction, kind: "rename", oldId: current.id, newId: candidate });
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
              aria-label={`${direction === "input" ? "输入" : "输出"}端口 ${index + 1} 名称`}
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
              aria-label={`${direction === "input" ? "输入" : "输出"}端口 ${index + 1} 类型`}
              onChange={(event) => update(index, { type: event.target.value })}
            >
              {Array.from(new Set([...typeOptions, port.type || "text"])).map((type) => (
                <option key={type} value={type}>{type}</option>
              ))}
            </select>
          </label>
          {direction === "input" && <label className="ct-v2-port-required">
            <input type="checkbox" checked={Boolean(port.required)} onChange={(event) => update(index, { required: event.target.checked })} />
            <span>必填</span>
          </label>}
          <button
            type="button"
            className="ct-v2-icon-danger"
            aria-label={`删除${direction === "input" ? "输入" : "输出"}端口 ${port.id}`}
            title={`删除${direction === "input" ? "输入" : "输出"}端口`}
            onClick={() => {
              setDraftIds({});
              setIdErrors({});
              onChange(ports.filter((_, itemIndex) => itemIndex !== index), { direction, kind: "delete", oldId: port.id });
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
        增加{direction === "input" ? "输入" : "输出"}端口
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
