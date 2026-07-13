"use client";

import Link from "next/link";
import { CheckCircle2, FlaskConical, Loader2, Play } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { workflowsApi } from "@/lib/api/workflows";
import type { AuthoringGraphV2 } from "@/lib/types/workflow";
import type { Workspace } from "@/lib/types";

export function TrialRunPanel({ workflowId, versionId, graph, onBeforeRun }: {
  workflowId: string;
  versionId: string;
  graph: AuthoringGraphV2;
  onBeforeRun?: () => Promise<void>;
}) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [files, setFiles] = useState<Record<string, File | null>>({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [taskRunId, setTaskRunId] = useState("");
  const inputs = useMemo(() => graph.nodes.filter((node) => node.kind === "input" && node.config.resolver !== "workspace"), [graph.nodes]);
  useEffect(() => {
    void api.workspaces.list().then((items) => {
      setWorkspaces(items);
      setWorkspaceId((current) => current || items[0]?.id || "");
    }).catch((cause) => setError(cause instanceof Error ? cause.message : "工作空间加载失败"));
  }, []);

  const start = async () => {
    if (!workspaceId) { setError("请先选择一个已创建的工作空间"); return; }
    const missingInput = inputs.find((node) => {
      if (!node.config.required) return false;
      const inputId = String(node.config.contract_id || node.id);
      const type = String(node.config.type || "text");
      return ["file", "file_set", "coverage_report", "patch", "diff"].includes(type)
        ? !files[inputId]
        : !values[inputId]?.trim();
    });
    if (missingInput) { setError(`请填写必需输入：${missingInput.label}`); return; }
    setRunning(true); setError(""); setTaskRunId("");
    try {
      await onBeforeRun?.();
      const payload: Record<string, unknown> = { ...values };
      for (const node of inputs) {
        const inputId = String(node.config.contract_id || node.id);
        const file = files[inputId];
        if (file) {
          const uploaded = await api.workbench.uploadInputFile(file, inputId);
          payload[inputId] = uploaded.input_payload;
        }
      }
      const prepared = await workflowsApi.testRun(workflowId, versionId, { workspace_id: workspaceId, inputs: payload });
      setTaskRunId(prepared.task_run_id);
      await api.workbench.taskRuns.execute(prepared.task_run_id, 0, graph.settings.stop_on_error);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "试运行启动失败");
    } finally { setRunning(false); }
  };

  return <div className="ct-v2-trial-panel">
    <div className="ct-v2-trial-heading"><FlaskConical size={17} /><div><strong>真实试运行</strong><p>服务端重新编译当前草稿，并通过现有执行器运行；不会自动发布。</p></div></div>
    <div className="ct-v2-trial-form">
      <label><span>工作空间</span><select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}><option value="">选择已创建的工作空间</option>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}</select></label>
      {inputs.map((node) => {
        const inputId = String(node.config.contract_id || node.id);
        const type = String(node.config.type || "text");
        return <label key={node.id}><span>{node.label}{node.config.required ? " *" : ""}</span>{["file", "file_set", "coverage_report", "patch", "diff"].includes(type) ? <input type="file" onChange={(event) => setFiles((current) => ({ ...current, [inputId]: event.target.files?.[0] ?? null }))} /> : <input value={values[inputId] ?? ""} onChange={(event) => setValues((current) => ({ ...current, [inputId]: event.target.value }))} placeholder={String(node.config.role || `填写${node.label}`)} />}</label>;
      })}
    </div>
    {error && <p className="ct-v2-form-error">{error}</p>}
    <div className="ct-v2-trial-actions"><button className="ct-v2-primary-button" type="button" onClick={() => void start()} disabled={running}>{running ? <Loader2 className="animate-spin" size={15} /> : <Play size={15} />}{running ? "正在准备" : "启动试运行"}</button>{taskRunId && <span><CheckCircle2 size={15} />运行已启动 <Link href={`/workbench?task_run_id=${encodeURIComponent(taskRunId)}`}>查看运行</Link></span>}</div>
  </div>;
}
