"use client";

import Link from "next/link";
import { CheckCircle2, FlaskConical, Loader2, Play } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { workflowsApi } from "@/lib/api/workflows";
import type { AuthoringGraph } from "@/lib/types/workflow";
import type { Workspace } from "@/lib/types";
import { prepareTrialRunWithUploads } from "./trial-run-contract";

export function TrialRunPanel({ workflowId, versionId, graph, onBeforeRun, onDraftRevision }: {
  workflowId: string;
  versionId: string;
  graph: AuthoringGraph;
  onBeforeRun?: () => Promise<number | void | undefined>;
  onDraftRevision?: (revision: number) => void;
}) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [files, setFiles] = useState<Record<string, File | null>>({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [taskRunId, setTaskRunId] = useState("");
  const executableNodes = useMemo(() => graph.nodes.filter((node) => node.kind !== "input" && node.kind !== "output"), [graph.nodes]);
  const [nodeId, setNodeId] = useState("");
  const inputs = useMemo(() => graph.nodes.filter((node) => node.kind === "input" && node.config.resolver !== "workspace"), [graph.nodes]);
  useEffect(() => {
    void api.workspaces.list().then((items) => {
      setWorkspaces(items);
      setWorkspaceId((current) => current || items[0]?.id || "");
    }).catch((cause) => setError(cause instanceof Error ? cause.message : "工作空间加载失败"));
  }, []);
  useEffect(() => {
    setNodeId((current) => current || executableNodes[0]?.id || "");
  }, [executableNodes]);

  const start = async () => {
    if (!workspaceId) { setError("请先选择一个已创建的工作空间"); return; }
    const missingInput = inputs.find((node) => {
      if (!node.config.required) return false;
      const inputId = inputKey(node);
      const type = String(node.config.type || "text");
      return ["file", "file_set", "coverage_report", "patch", "diff"].includes(type)
        ? !files[inputId]
        : !values[inputId]?.trim();
    });
    if (missingInput) { setError(`请填写必需输入：${missingInput.label}`); return; }
    setRunning(true); setError(""); setTaskRunId("");
    try {
      if (!nodeId) throw new Error("请先选择一个可执行节点");
      const prepared = await prepareTrialRunWithUploads({
        values,
        files: inputs.flatMap((node) => {
          const inputId = inputKey(node);
          const file = files[inputId];
          return file ? [{ inputId, file }] : [];
        }),
        beforeRun: onBeforeRun,
        uploadInput: (file, inputId, lease) => api.workbench.uploadInputFile(file, inputId, lease),
        prepareRun: (payload, expectedRevision) => workflowsApi.testRun(workflowId, versionId, {
          workspace_id: workspaceId,
          inputs: payload,
          node_id: nodeId,
          ...(expectedRevision === undefined ? {} : { expected_revision: expectedRevision }),
        }),
        releaseUpload: (uploadId, cleanupToken) => api.workbench.releaseInputFileUpload(uploadId, cleanupToken),
        workflowId,
        workflowVersionId: versionId,
      });
      if (prepared.draft_revision !== undefined) onDraftRevision?.(prepared.draft_revision);
      // A prepared run has a durable task id before execution is queued. Show it
      // immediately so the operator can open the cockpit even if scheduling fails.
      setTaskRunId(prepared.task_run_id);
      await api.workbench.taskRuns.execute(prepared.task_run_id, 0, graph.settings.stop_on_error);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "试运行启动失败");
    } finally { setRunning(false); }
  };

  return <div className="ct-v2-trial-panel" data-testid="workflow-trial-form">
    <div className="ct-v2-trial-heading"><FlaskConical size={17} /><div><strong>节点真实试运行</strong><p>服务端重新编译当前草稿，只执行选中节点；结果仅用于诊断，不会发布或计入正式交付。</p></div></div>
    <div className="ct-v2-trial-form">
      <label><span>工作空间</span><select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}><option value="">选择已创建的工作空间</option>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}</select></label>
      <label><span>试运行节点</span><select value={nodeId} onChange={(event) => setNodeId(event.target.value)}><option value="">选择可执行节点</option>{executableNodes.map((node) => <option key={node.id} value={node.id}>{node.label}</option>)}</select></label>
      {inputs.map((node) => {
        const inputId = inputKey(node);
        const type = String(node.config.type || "text");
        const required = Boolean(node.config.required);
        return <label key={node.id}><span>{node.label}{required ? " *" : ""}</span>{["file", "file_set", "coverage_report", "patch", "diff"].includes(type) ? <input aria-label={`${node.label}${required ? " *" : ""}`} type="file" onChange={(event) => setFiles((current) => ({ ...current, [inputId]: event.target.files?.[0] ?? null }))} /> : <input aria-label={`${node.label}${required ? " *" : ""}`} value={values[inputId] ?? ""} onChange={(event) => setValues((current) => ({ ...current, [inputId]: event.target.value }))} placeholder={String(node.config.role || `填写${node.label}`)} />}</label>;
      })}
    </div>
    {error && <p className="ct-v2-form-error">{error}</p>}
    <div className="ct-v2-trial-actions"><button className="ct-v2-primary-button" type="button" onClick={() => void start()} disabled={running}>{running ? <Loader2 className="animate-spin" size={15} /> : <Play size={15} />}{running ? "正在准备" : "启动试运行"}</button>{taskRunId && <span><CheckCircle2 size={15} />运行已启动 <Link href={`/workbench?task_run_id=${encodeURIComponent(taskRunId)}`}>查看运行</Link></span>}</div>
  </div>;
}

function inputKey(node: { id: string; config: Record<string, unknown> }): string {
  return String(node.config.input_id || node.config.contract_id || node.id);
}
