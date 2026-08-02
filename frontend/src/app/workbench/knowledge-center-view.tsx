"use client";

import {
  Check,
  ClipboardList,
  FileText,
  History,
  Loader2,
  RotateCcw,
  Search,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  addKnowledgePatternVersion,
  getKnowledgeIncident,
  getKnowledgePattern,
  importKnowledgeFiles,
  importKnowledgePaste,
  listKnowledgeImportJobs,
  listKnowledgeIncidents,
  listKnowledgePatterns,
  recordKnowledgeFeedback,
  restoreKnowledgePatternVersion,
  retryKnowledgeImportStage,
  reviewKnowledgePattern,
  startKnowledgeAgentEnrichment,
  updateKnowledgePatternLifecycle,
  type KnowledgeImportJob,
  type KnowledgeIncident,
  type KnowledgePattern,
} from "@/lib/knowledge-center";

type Tab = "incidents" | "patterns" | "imports";

const tabLabels: Record<Tab, string> = {
  incidents: "历史事件",
  patterns: "经验模式",
  imports: "导入任务",
};

export function KnowledgeCenterView() {
  const [tab, setTab] = useState<Tab>("incidents");
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<"project" | "personal_global">("personal_global");
  const [workspaceIdentity, setWorkspaceIdentity] = useState("");
  const [incidents, setIncidents] = useState<KnowledgeIncident[]>([]);
  const [patterns, setPatterns] = useState<KnowledgePattern[]>([]);
  const [jobs, setJobs] = useState<KnowledgeImportJob[]>([]);
  const [selectedIncident, setSelectedIncident] = useState<KnowledgeIncident | null>(null);
  const [selectedPattern, setSelectedPattern] = useState<KnowledgePattern | null>(null);
  const [paste, setPaste] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [mrUrl, setMrUrl] = useState("");
  const [versionDraft, setVersionDraft] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const loadIncidents = useCallback(async () => {
    setIncidents(await listKnowledgeIncidents({ query, scope, workspaceIdentity }));
  }, [query, scope, workspaceIdentity]);

  const loadPatterns = useCallback(async () => {
    setPatterns(await listKnowledgePatterns({ query, scope, workspaceIdentity }));
  }, [query, scope, workspaceIdentity]);

  const loadJobs = useCallback(async () => {
    setJobs(await listKnowledgeImportJobs());
  }, []);

  useEffect(() => {
    setBusy("load");
    const loader = tab === "incidents" ? loadIncidents : tab === "patterns" ? loadPatterns : loadJobs;
    loader()
      .catch((error) => setMessage(error instanceof Error ? error.message : "加载失败"))
      .finally(() => setBusy(""));
  }, [tab, loadIncidents, loadPatterns, loadJobs]);

  async function refreshPattern() {
    if (!selectedPattern) return;
    setSelectedPattern(await getKnowledgePattern(selectedPattern.pattern_id));
    await loadPatterns();
  }

  async function saveVersion() {
    if (!selectedPattern || !versionDraft.trim()) return;
    setBusy("version");
    try {
      await addKnowledgePatternVersion(selectedPattern.pattern_id, { content: versionDraft.trim() });
      setVersionDraft("");
      await refreshPattern();
      setMessage("已新增模式版本");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "版本保存失败");
    } finally {
      setBusy("");
    }
  }

  async function submitPaste() {
    if (!paste.trim()) return;
    setBusy("paste");
    try {
      const result = await importKnowledgePaste({
        text: paste.trim(),
        scope,
        workspace_identity: workspaceIdentity,
        mr_url: mrUrl.trim() || undefined,
      });
      setPaste("");
      setMessage(result.extraction.status + " · " + result.job.job_id);
      await loadJobs();
      setTab("imports");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "导入失败");
    } finally {
      setBusy("");
    }
  }

  async function submitFiles() {
    if (!files.length) return;
    setBusy("files");
    try {
      const result = await importKnowledgeFiles(files, {
        scope,
        workspaceIdentity,
        mrUrl: mrUrl.trim() || undefined,
      });
      setFiles([]);
      setMessage(result.extraction.status + " · " + result.job.job_id);
      await loadJobs();
      setTab("imports");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "导入失败");
    } finally {
      setBusy("");
    }
  }

  async function retry(job: KnowledgeImportJob, stage: string) {
    setBusy(job.job_id);
    try {
      await retryKnowledgeImportStage(job.job_id, stage);
      await loadJobs();
      setMessage("已将阶段重新置为待处理");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "重试失败");
    } finally {
      setBusy("");
    }
  }

  async function enrich(job: KnowledgeImportJob) {
    setBusy(`enrich-${job.job_id}`);
    try {
      const result = await startKnowledgeAgentEnrichment(job.job_id);
      setMessage(`Agent 运行已启动 · ${result.task_run_id}`);
      await loadJobs();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Agent 提炼启动失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="flex min-h-0 flex-col gap-4 p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-on-surface-variant">Knowledge Center</p>
          <h1 className="mt-1 text-xl font-semibold text-on-surface">测试知识中心</h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            历史材料只作为调查线索，当前结论仍需本次运行证据。
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <select
            aria-label="知识作用域"
            value={scope}
            onChange={(event) => setScope(event.target.value as "project" | "personal_global")}
            className="h-9 rounded-md border border-outline-variant/50 bg-surface px-2 text-on-surface"
          >
            <option value="personal_global">个人全局</option>
            <option value="project">当前项目</option>
          </select>
          <input
            value={workspaceIdentity}
            onChange={(event) => setWorkspaceIdentity(event.target.value)}
            placeholder="项目身份（可选）"
            className="h-9 w-52 rounded-md border border-outline-variant/50 bg-surface px-3 text-on-surface"
          />
        </div>
      </header>

      <nav className="flex gap-1 border-b border-outline-variant/30" aria-label="知识中心标签页">
        {(Object.keys(tabLabels) as Tab[]).map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => setTab(item)}
            className={tab === item ? "border-b-2 border-primary px-3 py-2 text-sm text-primary" : "border-b-2 border-transparent px-3 py-2 text-sm text-on-surface-variant"}
          >
            {tabLabels[item]}
          </button>
        ))}
      </nav>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-64 flex-1">
          <Search size={15} className="pointer-events-none absolute left-3 top-2.5 text-on-surface-variant" />
          <input
            aria-label="搜索知识"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索事件、条件、协议或资源"
            className="h-9 w-full rounded-md border border-outline-variant/50 bg-surface pl-9 pr-3 text-sm text-on-surface"
          />
        </div>
        {busy === "load" && <Loader2 size={16} className="animate-spin text-primary" />}
        {message && <span className="text-xs text-on-surface-variant">{message}</span>}
      </div>

      {tab === "incidents" && (
        <div className="grid min-h-0 gap-4 lg:grid-cols-[minmax(260px,0.8fr)_minmax(0,1.4fr)]">
          <div className="space-y-2 overflow-auto">
            {incidents.map((incident) => (
              <button
                key={incident.incident_id}
                type="button"
                onClick={() =>
                  getKnowledgeIncident(incident.incident_id)
                    .then(setSelectedIncident)
                    .catch((error) => setMessage(error instanceof Error ? error.message : "详情加载失败"))
                }
                className={selectedIncident?.incident_id === incident.incident_id ? "w-full rounded-md border border-primary bg-primary/5 p-3 text-left" : "w-full rounded-md border border-outline-variant/30 p-3 text-left"}
              >
                <span className="flex items-center gap-2 text-sm font-medium text-on-surface">
                  <History size={15} /> {incident.title}
                </span>
                <span className="mt-1 block text-xs text-on-surface-variant">{incident.summary}</span>
                <span className="mt-2 block text-[11px] text-on-surface-variant">
                  {incident.scope} · {incident.workspace_identity || "未绑定项目"}
                </span>
              </button>
            ))}
            {!incidents.length && <p className="p-4 text-sm text-on-surface-variant">暂无历史事件</p>}
          </div>
          <article className="min-w-0 rounded-md border border-outline-variant/30 p-4">
            {selectedIncident ? (
              <>
                <h2 className="text-lg font-semibold text-on-surface">{selectedIncident.title}</h2>
                <p className="mt-2 whitespace-pre-wrap text-sm text-on-surface-variant">{selectedIncident.summary}</p>
                <div className="mt-4 flex flex-wrap gap-2">
                  {selectedIncident.terms.map((term) => (
                    <span key={term} className="rounded bg-surface-container px-2 py-1 text-xs text-on-surface-variant">{term}</span>
                  ))}
                </div>
                <h3 className="mt-5 text-sm font-semibold text-on-surface">溯源</h3>
                <pre className="mt-2 max-h-64 overflow-auto rounded bg-surface-container p-3 text-xs text-on-surface-variant">
                  {JSON.stringify(selectedIncident.provenance ?? [], null, 2)}
                </pre>
                <button
                  type="button"
                  onClick={() =>
                    recordKnowledgeFeedback({
                      subject_type: "incident",
                      subject_id: selectedIncident.incident_id,
                      outcome: "useful",
                    }).then(() => setMessage("已记录反馈"))
                  }
                  className="mt-3 inline-flex items-center gap-2 rounded-md border border-outline-variant/50 px-3 py-2 text-xs text-on-surface"
                >
                  <Check size={14} /> 这条经验有帮助
                </button>
              </>
            ) : (
              <p className="text-sm text-on-surface-variant">选择一个事件查看详情和溯源。</p>
            )}
          </article>
        </div>
      )}

      {tab === "patterns" && (
        <div className="grid min-h-0 gap-4 lg:grid-cols-[minmax(260px,0.8fr)_minmax(0,1.4fr)]">
          <div className="space-y-2 overflow-auto">
            {patterns.map((pattern) => (
              <button
                key={pattern.pattern_id}
                type="button"
                onClick={() => getKnowledgePattern(pattern.pattern_id).then(setSelectedPattern)}
                className={selectedPattern?.pattern_id === pattern.pattern_id ? "w-full rounded-md border border-primary bg-primary/5 p-3 text-left" : "w-full rounded-md border border-outline-variant/30 p-3 text-left"}
              >
                <span className="flex items-center gap-2 text-sm font-medium text-on-surface">
                  <ClipboardList size={15} /> {pattern.name}
                </span>
                <span className="mt-1 block text-xs text-on-surface-variant">
                  v{pattern.version_number ?? "?"} · {pattern.review_state} · {pattern.lifecycle_state}
                </span>
              </button>
            ))}
            {!patterns.length && <p className="p-4 text-sm text-on-surface-variant">暂无经验模式</p>}
          </div>
          <article className="min-w-0 rounded-md border border-outline-variant/30 p-4">
            {selectedPattern ? (
              <>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <h2 className="text-lg font-semibold text-on-surface">{selectedPattern.name}</h2>
                    <p className="mt-1 text-xs text-on-surface-variant">{selectedPattern.review_state} · {selectedPattern.lifecycle_state}</p>
                  </div>
                  <div className="flex gap-2">
                    <button type="button" onClick={() => reviewKnowledgePattern(selectedPattern.pattern_id, "confirmed").then(refreshPattern)} className="rounded-md border border-outline-variant/50 px-2 py-1 text-xs text-on-surface">标记已复核</button>
                    <button type="button" onClick={() => updateKnowledgePatternLifecycle(selectedPattern.pattern_id, "deprecated").then(refreshPattern)} className="rounded-md border border-outline-variant/50 px-2 py-1 text-xs text-on-surface">标记弃用</button>
                  </div>
                </div>
                <p className="mt-3 whitespace-pre-wrap text-sm text-on-surface-variant">{selectedPattern.content}</p>
                <div className="mt-4 space-y-2">
                  {(selectedPattern.versions ?? []).map((version) => (
                    <div key={version.pattern_version_id} className="flex items-center justify-between gap-3 rounded border border-outline-variant/30 p-2">
                      <span className="text-xs text-on-surface-variant">v{version.version_number}</span>
                      <span className="min-w-0 flex-1 truncate text-xs text-on-surface">{version.content}</span>
                      <button
                        type="button"
                        title="恢复此版本"
                        onClick={() =>
                          restoreKnowledgePatternVersion(selectedPattern.pattern_id, version.pattern_version_id).then(refreshPattern)
                        }
                        className="grid h-7 w-7 shrink-0 place-items-center rounded border border-outline-variant/50 text-on-surface"
                      >
                        <RotateCcw size={13} />
                      </button>
                    </div>
                  ))}
                </div>
                <div className="mt-4 flex gap-2">
                  <textarea
                    value={versionDraft}
                    onChange={(event) => setVersionDraft(event.target.value)}
                    placeholder="新增版本内容"
                    className="min-h-20 min-w-0 flex-1 rounded-md border border-outline-variant/50 bg-surface p-2 text-sm text-on-surface"
                  />
                  <button type="button" disabled={busy === "version" || !versionDraft.trim()} onClick={saveVersion} className="self-end rounded-md bg-primary px-3 py-2 text-xs text-on-primary disabled:opacity-50">
                    {busy === "version" ? <Loader2 size={14} className="animate-spin" /> : "新增版本"}
                  </button>
                </div>
              </>
            ) : (
              <p className="text-sm text-on-surface-variant">选择一个模式管理版本和状态。</p>
            )}
          </article>
        </div>
      )}

      {tab === "imports" && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(300px,0.9fr)]">
          <div className="space-y-4">
            <div className="rounded-md border border-outline-variant/30 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-on-surface"><FileText size={16} /> 粘贴历史材料</div>
              <textarea value={paste} onChange={(event) => setPaste(event.target.value)} placeholder="粘贴事件经过、现象、恢复条件或测试记录" className="mt-3 min-h-36 w-full rounded-md border border-outline-variant/50 bg-surface p-3 text-sm text-on-surface" />
              <button type="button" disabled={busy === "paste" || !paste.trim()} onClick={submitPaste} className="mt-3 inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-on-primary disabled:opacity-50">
                {busy === "paste" ? <Loader2 size={15} className="animate-spin" /> : <ClipboardList size={15} />} 导入材料
              </button>
            </div>
            <div className="rounded-md border border-outline-variant/30 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-on-surface"><Upload size={16} /> 批量文件</div>
              <input type="file" multiple accept=".docx,.pdf,.xlsx,.txt,.md,.log,.csv" onChange={(event) => setFiles(Array.from(event.target.files ?? []))} className="mt-3 block w-full text-sm text-on-surface-variant" />
              <p className="mt-2 text-xs text-on-surface-variant">支持 DOCX、文本 PDF、XLSX；扫描 PDF 会进入待 OCR 状态。</p>
              <button type="button" disabled={busy === "files" || !files.length} onClick={submitFiles} className="mt-3 inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-on-primary disabled:opacity-50">
                {busy === "files" ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />} 导入材料
              </button>
            </div>
            <label className="block text-sm text-on-surface">
              <span className="mb-1 block text-xs text-on-surface-variant">可选 MR 链接</span>
              <input value={mrUrl} onChange={(event) => setMrUrl(event.target.value)} placeholder="仅填写后生成只读 CodeHub 请求" className="h-9 w-full rounded-md border border-outline-variant/50 bg-surface px-3 text-sm" />
            </label>
          </div>
          <div className="rounded-md border border-outline-variant/30 p-4">
            <h2 className="text-sm font-semibold text-on-surface">导入任务</h2>
            <div className="mt-3 space-y-2">
              {jobs.map((job) => (
                <div key={job.job_id} className="rounded border border-outline-variant/30 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-xs text-on-surface">{job.job_id}</span>
                    <span className="text-xs text-primary">{job.status}</span>
                  </div>
                  <p className="mt-1 text-xs text-on-surface-variant">{job.source_count} 个来源 · {job.scope}</p>
                  <div className="mt-2 flex items-center justify-between gap-2">
                    <p className="text-xs text-on-surface-variant">Agent 状态：{job.status}</p>
                    {job.status !== "completed" && job.status !== "agent_enrichment_running" && (
                      <button
                        type="button"
                        onClick={() => enrich(job)}
                        disabled={busy === `enrich-${job.job_id}`}
                        className="inline-flex items-center gap-2 rounded-md bg-primary px-2.5 py-1.5 text-xs text-on-primary disabled:opacity-50"
                      >
                        {busy === `enrich-${job.job_id}` ? (
                          <Loader2 size={13} className="animate-spin" />
                        ) : (
                          <ClipboardList size={13} />
                        )}
                        Agent 提炼
                      </button>
                    )}
                  </div>
                  {job.stages.map((stage) => (
                    <div key={stage.stage} className="mt-2 flex items-center justify-between gap-2 text-xs">
                      <span className="text-on-surface-variant">{stage.stage}</span>
                      <span className={stage.status === "failed" ? "text-error" : "text-on-surface"}>{stage.status}</span>
                      {stage.status === "failed" && (
                        <button type="button" title="重试此阶段" onClick={() => retry(job, stage.stage)} className="grid h-6 w-6 place-items-center rounded border border-outline-variant/50">
                          <RotateCcw size={12} />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              ))}
              {!jobs.length && <p className="text-sm text-on-surface-variant">暂无导入任务</p>}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

export default KnowledgeCenterView;
