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
import { providerConfigValue, providerSelectionPatch } from "./provider-contract";

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
  schemaVersion?: number;
  capabilities: WorkflowCapabilities | null;
  providers: WorkflowProviderCapability[];
  registry: WorkflowNodeRegistry;
  declaredOutputs?: Array<{ id: string; label: string; artifact: string }>;
  onChange: (node: WorkflowGraphNode, portMutation?: PortMutation) => void;
  onClose: () => void;
  onCreatePort?: (nodeId: string, direction: "input" | "output") => Promise<WorkflowGraphNode>;
  onUpdatePort?: (
    nodeId: string,
    portId: string,
    patch: { label?: string; type?: string; required?: boolean; collection?: boolean },
  ) => Promise<WorkflowGraphNode>;
  onDeletePort?: (nodeId: string, portId: string) => Promise<WorkflowGraphNode>;
  onUpdateValidatorHandler?: (nodeId: string, handlerId: string) => Promise<WorkflowGraphNode>;
}

export type PortMutation =
  | { direction: "input" | "output"; kind: "rename"; oldId: string; newId: string }
  | { direction: "input" | "output"; kind: "delete"; oldId: string };

export function NodeInspector({ node, schemaVersion = 2, capabilities, providers, registry, declaredOutputs = [], onChange, onClose, onCreatePort, onUpdatePort, onDeletePort, onUpdateValidatorHandler }: Props) {
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [portError, setPortError] = useState("");
  const [handlerError, setHandlerError] = useState("");
  const [handlerChanging, setHandlerChanging] = useState(false);
  const isV3 = schemaVersion === 3;
  const config = node.config;
  const runPortCommand = async (command: () => Promise<WorkflowGraphNode>) => {
    setPortError("");
    try {
      onChange(await command());
      return true;
    } catch (cause) {
      setPortError(cause instanceof Error ? cause.message : "端口操作失败");
      return false;
    }
  };
  const updateConfig = (patch: Record<string, unknown>) => {
    const nextHandler = typeof patch.handler_id === "string" ? patch.handler_id : null;
    if (isV3 && node.kind === "validator" && nextHandler && onUpdateValidatorHandler) {
      if (nextHandler === String(config.handler_id ?? "")) return;
      setHandlerError("");
      setHandlerChanging(true);
      void onUpdateValidatorHandler(node.id, nextHandler)
        .catch((cause) => {
          setHandlerError(cause instanceof Error ? cause.message : "Validator 切换失败");
        })
        .finally(() => setHandlerChanging(false));
      return;
    }
    const nextType = typeof patch.type === "string" ? patch.type : null;
    const contractPort = node.kind === "input" ? node.ports?.outputs[0] : null;
    if (isV3 && nextType && contractPort && contractPort.type !== nextType && onUpdatePort) {
      void runPortCommand(async () => {
        const updated = await onUpdatePort(node.id, contractPort.id, { type: nextType });
        return { ...updated, config: { ...updated.config, ...patch } };
      });
      return;
    }
    onChange({ ...node, config: { ...config, ...patch } });
  };
  const updateLabel = (label: string) =>
    onChange({ ...node, label, config: { ...config, label } });
  const definition = registry.nodes.find((item) => item.kind === node.kind);

  return (
    <aside className="ct-v2-inspector" aria-label="节点属性" data-testid="workflow-selected-node-id" data-node-id={node.id}>
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
          {!isV3 && <Field label="节点 ID">
            <input value={node.id} readOnly aria-readonly="true" />
          </Field>}
        </InspectorSection>

        <InspectorSection title="高级">
          <button type="button" className="ct-v2-add-port" onClick={() => setShowDiagnostics((current) => !current)}>高级诊断</button>
          {showDiagnostics && <TechnicalIdentifiers node={node} />}
        </InspectorSection>

        {definition && (
          <RegistryContractSummary definition={definition} />
        )}

        {definition && (
          <InspectorSection title={node.kind === "agent" ? "执行配置" : node.kind === "input" ? "输入契约" : node.kind === "output" ? "输出契约" : "内置步骤"}>
            {node.kind !== "input" && node.kind !== "output" && (
              <p className="ct-v2-inspector-note">输入和依赖通过画布连线指定。以下字段由后端节点定义驱动，无需填写 JSON。</p>
            )}
            {isV3 && (node.kind === "validator" || node.kind === "governance") && (
              <p className="ct-v2-inspector-note">处理器端口由系统维护，可在画布上连接，但不能增加、删除或修改。</p>
            )}
            <RegistryConfigFields
              definition={definition}
              node={node}
              config={config}
              capabilities={capabilities}
              providers={providers}
              declaredOutputs={declaredOutputs}
              onChange={updateConfig}
              handlerChanging={handlerChanging}
              isV3={isV3}
              onCreatePort={onCreatePort ? async (direction) => onChange(await onCreatePort(node.id, direction)) : undefined}
              onPortMutation={(key, ports, mutation) => {
                if (isV3) {
                  onChange({ ...node, ports: { inputs: directionPorts(node, "input", key, ports), outputs: directionPorts(node, "output", key, ports) } }, mutation);
                  return;
                }
                onChange({ ...node, config: { ...config, [key]: ports } }, mutation);
              }}
            />
            {handlerError && <p className="ct-v2-port-id-error" role="alert">{handlerError}</p>}
            {isV3 && node.kind === "output" && node.ports?.inputs[0] && onUpdatePort && (
              <Field label="接收端口类型">
                <select
                  value={node.ports.inputs[0].type}
                  onChange={(event) => {
                    void runPortCommand(() => onUpdatePort(node.id, node.ports!.inputs[0].id, {
                      type: event.target.value,
                    }));
                  }}
                >
                  {Array.from(new Set([
                    node.ports.inputs[0].type,
                    "artifact",
                    "markdown",
                    "structured_json",
                    "json",
                    "text",
                  ])).map((type) => <option key={type} value={type}>{type}</option>)}
                </select>
              </Field>
            )}
          </InspectorSection>
        )}

        {isV3 && node.kind === "agent" && (
          <InspectorSection title="端口">
            <p className="ct-v2-inspector-note">端口名称用于画布和运行表单，内部 ID 由系统维护。</p>
            <PortListEditor
              direction="input"
              ports={node.ports?.inputs ?? []}
              typeOptions={capabilities?.input_types ?? ["text", "file", "directory", "mr_link"]}
              technicalIdentityLocked
              onCreate={onCreatePort ? async (direction) => { await runPortCommand(() => onCreatePort(node.id, direction)); } : undefined}
              onPatch={onUpdatePort ? (portId, patch) => runPortCommand(() => onUpdatePort(node.id, portId, patch)) : undefined}
              onDelete={onDeletePort ? async (portId) => { await runPortCommand(() => onDeletePort(node.id, portId)); } : undefined}
              onChange={(ports) => onChange({
                ...node,
                ports: { inputs: ports, outputs: node.ports?.outputs ?? [] },
              })}
            />
            <PortListEditor
              direction="output"
              ports={node.ports?.outputs ?? []}
              typeOptions={Array.from(new Set([...(capabilities?.output_types ?? ["markdown", "json"]), "artifact", "structured_json", "artifact_ref"]))}
              technicalIdentityLocked
              onCreate={onCreatePort ? async (direction) => { await runPortCommand(() => onCreatePort(node.id, direction)); } : undefined}
              onPatch={onUpdatePort ? (portId, patch) => runPortCommand(() => onUpdatePort(node.id, portId, patch)) : undefined}
              onDelete={onDeletePort ? async (portId) => { await runPortCommand(() => onDeletePort(node.id, portId)); } : undefined}
              onChange={(ports) => onChange({
                ...node,
                ports: { inputs: node.ports?.inputs ?? [], outputs: ports },
              })}
            />
            {portError && <p className="ct-v2-port-id-error" role="alert">{portError}</p>}
          </InspectorSection>
        )}
      </div>
    </aside>
  );
}

function directionPorts(node: WorkflowGraphNode, direction: "input" | "output", key: string, ports: WorkflowPortDefinition[]) {
  const current = node.ports ?? { inputs: [], outputs: [] };
  const target = key === "output_ports" ? "output" : "input";
  return direction === target ? ports : current[direction === "input" ? "inputs" : "outputs"];
}

function TechnicalIdentifiers({ node }: { node: WorkflowGraphNode }) {
  const fields = [
    ["节点 ID", node.id],
    ...inputPortDefinitionsForDiagnostics(node).map((port) => ["输入端口 ID", port.id]),
    ...outputPortDefinitionsForDiagnostics(node).map((port) => ["输出端口 ID", port.id]),
    ...["contract_id", "output_id", "step_id"].flatMap((key) => node.config[key] ? [[key, String(node.config[key])]] : []),
  ];
  return <div className="ct-v2-technical-identifiers" data-testid="workflow-technical-identifiers">{fields.map(([label, value], index) => <div className="ct-v2-field" key={`${label}-${index}`}><span>{label}</span><code>{value}</code><input type="hidden" value={value} readOnly aria-readonly="true" /></div>)}</div>;
}

function inputPortDefinitionsForDiagnostics(node: WorkflowGraphNode) { return node.ports?.inputs ?? portList(node.config.input_ports); }
function outputPortDefinitionsForDiagnostics(node: WorkflowGraphNode) { return node.ports?.outputs ?? portList(node.config.output_ports); }

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
  declaredOutputs,
  onChange,
  onPortMutation,
  isV3,
  onCreatePort,
  handlerChanging,
}: {
  definition: WorkflowNodeRegistryEntry;
  node: WorkflowGraphNode;
  config: WorkflowGraphNode["config"];
  capabilities: WorkflowCapabilities | null;
  providers: WorkflowProviderCapability[];
  declaredOutputs: Array<{ id: string; label: string; artifact: string }>;
  onChange: (patch: Record<string, unknown>) => void;
  onPortMutation: (key: string, ports: WorkflowPortDefinition[], mutation: PortMutation) => void;
  isV3: boolean;
  onCreatePort?: (direction: "input" | "output") => Promise<void>;
  handlerChanging?: boolean;
}) {
  const fieldOrder = definition.ui_schema?.inspector?.field_order ?? [];
  const fields = Object.entries(definition.config_schema)
    .map(([key, value]) => [key, value as RegistryField] as const)
    .filter(([key, field]) => Boolean(field.type) && !(isV3 && /(^|_)(contract|output|step|port)_id$/.test(key)))
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
              <select
                value={String(config[key] ?? "")}
                disabled={key === "handler_id" && handlerChanging}
                onChange={(event) => onChange({ [key]: event.target.value })}
              >
                {(field.options ?? []).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </Field>
          );
        }
        if (field.type === "enum_multiselect") {
          return (
            <Field key={key} label={label}>
              <SearchMultiSelect
                selected={stringList(config[key])}
                options={(field.options ?? []).map((option) => ({
                  id: option.value,
                  label: option.label,
                }))}
                emptyText="没有可用的验收角色"
                ariaLabel={label}
                onChange={(value) => onChange({ [key]: value })}
              />
            </Field>
          );
        }
        if (field.type === "declared_output_multiselect") {
          const selectedOutputs = stringList(config[key]);
          return (
            <Field key={key} label={label}>
              <SearchMultiSelect
                selected={selectedOutputs}
                options={declaredOutputs.map((output) => ({
                  id: output.id,
                  label: output.label,
                  detail: output.artifact,
                }))}
                emptyText="请先在画布中添加并连接输出节点"
                ariaLabel={label}
                onChange={(value) => onChange({ [key]: value })}
              />
              {!selectedOutputs.length && (
                <p className="ct-v2-inspector-note is-warning" role="alert">
                  请至少选择一个已声明交付件，否则无法发布。
                </p>
              )}
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
            onChange(outputType === "test_design_mindmap"
              ? {
                  type: outputType,
                  media_type: "text/markdown",
                  artifact: MINDMAP_ARTIFACTS[0],
                  companion_artifacts: MINDMAP_ARTIFACTS.slice(1),
                  required: true,
                }
              : {
                  type: outputType,
                  media_type: outputMediaType(outputType),
                  companion_artifacts: undefined,
                });
          }}>{(capabilities?.output_types ?? ["markdown", "json", "test_cases", "test_design_mindmap"]).map((item) => <option key={item} value={item}>{outputTypeLabels[item] ?? item}</option>)}</select></Field>;
        }
        if (type === "provider") {
          return <Field key={key} label={label}><select value={providerConfigValue(config)} onChange={(event) => onChange(providerSelectionPatch(event.target.value, isV3))}>{providers.map((provider) => <option key={provider.provider} value={provider.provider}>{provider.display_name} · {provider.provider}{provider.status === "unavailable" ? "（不可用）" : ""}</option>)}</select></Field>;
        }
        if (type === "skill_multiselect") {
          return <SearchMultiSelect key={key} selected={stringList(config[key])} options={(capabilities?.skill_catalog ?? []).map((item) => ({ id: item.id, label: item.label, detail: item.description }))} emptyText="没有匹配的 Skill" onChange={(value) => onChange({ [key]: value })} />;
        }
        if (type === "mcp_multiselect") {
          const provider = providers.find((item) => item.provider === providerConfigValue(config));
          const options = provider?.capabilities?.mcp_profiles ?? [];
          return <SearchMultiSelect key={key} selected={stringList(config[key])} options={options.map((id) => ({ id, label: id }))} emptyText={provider ? "该执行器没有已配置 MCP" : "先选择执行器"} onChange={(value) => onChange({ [key]: value })} />;
        }
        if (type === "artifact_list") {
          return <Field key={key} label={label}><textarea rows={3} value={stringList(config[key]).join("\n")} onChange={(event) => onChange({ [key]: event.target.value.split("\n").map((item) => item.trim()).filter(Boolean) })} placeholder="report.md" /></Field>;
        }
        if (type === "port_list") {
          const direction = key === "output_ports" ? "output" : "input";
          const typeOptions = direction === "output"
            ? Array.from(new Set([...(capabilities?.output_types ?? ["markdown", "json", "test_cases"]), "artifact", "structured_json", "artifact_ref"]))
            : capabilities?.input_types ?? ["text", "file", "directory", "mr_link"];
          const ports = isV3 ? (direction === "input" ? node.ports?.inputs ?? [] : node.ports?.outputs ?? []) : portList(config[key]);
          return <PortListEditor key={key} direction={direction} ports={ports} typeOptions={typeOptions} technicalIdentityLocked={isV3} readOnly={isV3 && (node.kind === "validator" || node.kind === "governance")} onCreate={onCreatePort} onChange={(ports, mutation) => {
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
      {node.kind === "output" && ["json", "structured_json", "test_cases"].includes(String(config.type ?? "")) && (
        <Field label="结构规则">
          <select
            value={outputSchemaType(config.schema)}
            onChange={(event) => {
              const schemaType = event.target.value;
              onChange({ schema: schemaType ? { type: schemaType } : undefined });
            }}
          >
            <option value="">不限制 JSON 结构</option>
            <option value="object">JSON 对象</option>
            <option value="array">JSON 数组</option>
          </select>
        </Field>
      )}
    </div>
  );
}

function outputMediaType(outputType: string): string {
  return ["json", "structured_json", "test_cases"].includes(outputType)
    ? "application/json"
    : "text/markdown";
}

function outputSchemaType(schema: unknown): string {
  if (!schema || typeof schema !== "object") return "";
  const type = (schema as { type?: unknown }).type;
  return type === "object" || type === "array" ? type : "";
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
  technicalIdentityLocked = false,
  readOnly = false,
  onCreate,
  onPatch,
  onDelete,
  onChange,
}: {
  direction: "input" | "output";
  ports: WorkflowPortDefinition[];
  typeOptions: string[];
  technicalIdentityLocked?: boolean;
  readOnly?: boolean;
  onCreate?: (direction: "input" | "output") => Promise<void>;
  onPatch?: (
    portId: string,
    patch: { label?: string; type?: string; required?: boolean; collection?: boolean },
  ) => Promise<boolean>;
  onDelete?: (portId: string) => Promise<void>;
  onChange: (ports: WorkflowPortDefinition[], mutation?: PortMutation) => void;
}) {
  const [draftIds, setDraftIds] = useState<Record<number, string>>({});
  const [idErrors, setIdErrors] = useState<Record<number, string>>({});
  const [pendingPatches, setPendingPatches] = useState<Record<string, Partial<WorkflowPortDefinition>>>({});
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
    const error = validateInputPortId(candidate, ports, index, direction === "input" ? "输入" : "输出");
    setIdErrors((items) => ({ ...items, [index]: error }));
    if (error || candidate === current.id) return;
    update(index, { id: candidate }, { direction, kind: "rename", oldId: current.id, newId: candidate });
    setDraftIds((items) => {
      const next = { ...items };
      delete next[index];
      return next;
    });
  };
  const patchPort = async (portId: string, patch: Partial<WorkflowPortDefinition>) => {
    if (!onPatch) return false;
    setPendingPatches((current) => ({
      ...current,
      [portId]: { ...current[portId], ...patch },
    }));
    const accepted = await onPatch(portId, patch);
    setPendingPatches((current) => {
      const next = { ...current };
      delete next[portId];
      return next;
    });
    return accepted;
  };
  return (
    <div className="ct-v2-port-editor">
      {ports.map((port, index) => {
        const visiblePort = { ...port, ...pendingPatches[port.id] };
        return (
        <div className="ct-v2-port-editor-row" key={index}>
          <label>
            <span>端口名称</span>
            <input
              value={technicalIdentityLocked ? (draftIds[index] ?? port.label ?? port.id) : (draftIds[index] ?? port.id)}
              readOnly={readOnly}
              aria-label={`${direction === "input" ? "输入" : "输出"}端口 ${index + 1} 名称`}
              onChange={(event) => {
                if (readOnly) return;
                const value = event.target.value;
                if (technicalIdentityLocked) {
                  setDraftIds((items) => ({ ...items, [index]: value }));
                  return;
                }
                setDraftIds((items) => ({ ...items, [index]: value }));
                setIdErrors((items) => ({
                  ...items,
                  [index]: validateInputPortId(value, ports, index, direction === "input" ? "输入" : "输出"),
                }));
              }}
              onBlur={() => {
                if (readOnly) return;
                if (!technicalIdentityLocked) {
                  commitId(index);
                  return;
                }
                const label = (draftIds[index] ?? port.label ?? "").trim();
                if (label && onPatch) {
                  void patchPort(port.id, { label }).then(() => setDraftIds((items) => {
                    const next = { ...items };
                    delete next[index];
                    return next;
                  }));
                }
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") event.currentTarget.blur();
              }}
              aria-invalid={technicalIdentityLocked ? undefined : Boolean(idErrors[index])}
            />
            {!technicalIdentityLocked && idErrors[index] && <small className="ct-v2-port-id-error" role="alert">{idErrors[index]}</small>}
          </label>
          <label>
            <span>类型</span>
            <select
              value={visiblePort.type || "text"}
              disabled={readOnly}
              aria-label={`${direction === "input" ? "输入" : "输出"}端口 ${index + 1} 类型`}
              onChange={(event) => {
                if (technicalIdentityLocked && onPatch) {
                  void patchPort(port.id, { type: event.target.value });
                  return;
                }
                update(index, { type: event.target.value });
              }}
            >
              {Array.from(new Set([...typeOptions, visiblePort.type || "text"])).map((type) => (
                <option key={type} value={type}>{type}</option>
              ))}
            </select>
          </label>
          {direction === "input" && <label className="ct-v2-port-required">
            <input aria-label={`输入端口 ${index + 1} 是否必填`} type="checkbox" checked={Boolean(visiblePort.required)} disabled={readOnly} onChange={(event) => {
              if (technicalIdentityLocked && onPatch) {
                void patchPort(port.id, { required: event.target.checked });
                return;
              }
              update(index, { required: event.target.checked });
            }} />
            <span>必填</span>
          </label>}
          {!readOnly && <button
            type="button"
            className="ct-v2-icon-danger"
            aria-label={`删除${direction === "input" ? "输入" : "输出"}端口 ${port.id}`}
            title={`删除${direction === "input" ? "输入" : "输出"}端口`}
            onClick={() => {
              if (technicalIdentityLocked) {
                if (onDelete) void onDelete(port.id);
                return;
              }
              setDraftIds({});
              setIdErrors({});
              onChange(ports.filter((_, itemIndex) => itemIndex !== index), { direction, kind: "delete", oldId: port.id });
            }}
          >
            <Trash2 size={14} />
          </button>}
        </div>
      );})}
      {!readOnly && <button
        type="button"
        className="ct-v2-add-port"
        onClick={() => {
          if (onCreate) { void onCreate(direction); return; }
          onChange([...ports, { id: uniqueId(), type: "file", required: false }]);
        }}
      >
        <Plus size={14} />
        增加{direction === "input" ? "输入" : "输出"}端口
      </button>}
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
  ariaLabel,
  onChange,
}: {
  selected: string[];
  options: Array<{ id: string; label: string; detail?: string }>;
  emptyText: string;
  ariaLabel?: string;
  onChange: (selected: string[]) => void;
}) {
  const [query, setQuery] = useState("");
  const visible = options
    .filter((item) => `${item.label} ${item.id} ${item.detail ?? ""}`.toLowerCase().includes(query.toLowerCase()))
    .slice(0, 30);
  return (
    <div className="ct-v2-multiselect" role="group" aria-label={ariaLabel}>
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
