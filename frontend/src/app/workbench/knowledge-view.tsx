"use client";

import type { WorkbenchController } from "./workbench-controller";


export function SemanticLibraryView({ scope }: { scope: WorkbenchController }) {
  const { AlertTriangle, ClipboardList, Database, Library, Loader2, Panel, Save, Search, buildSemanticCasesFromText, busyAction, evidenceAuditRefs, importSemanticCase, importSemanticCaseFile, loadMemorySlices, manualEvidencePath, manualEvidenceSubject, manualEvidenceText, memoryQuery, memoryResults, memorySlices, repoPath, saveManualEvidence, searchMemory, searchSemanticCases, semanticFeature, semanticFile, semanticJson, semanticLines, semanticModule, semanticQuery, semanticResults, setManualEvidencePath, setManualEvidenceSubject, setManualEvidenceText, setMemoryQuery, setSemanticFeature, setSemanticFile, setSemanticJson, setSemanticLines, setSemanticModule, setSemanticQuery, taskRunActionBusy, workspaceId } = scope;
  return (<>
            <Panel title="测试语义库" icon={<Library size={16} />}>
              <div className="space-y-3">
                <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
                  <div className="grid gap-2 sm:grid-cols-2">
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        特性
                      </span>
                      <input
                        aria-label="Semantic feature"
                        value={semanticFeature}
                        onChange={(event) =>
                          setSemanticFeature(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        模块
                      </span>
                      <input
                        aria-label="Semantic module"
                        value={semanticModule}
                        onChange={(event) =>
                          setSemanticModule(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                      />
                    </label>
                  </div>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      已有用例，每行一个
                    </span>
                    <textarea
                      aria-label="Semantic case lines"
                      value={semanticLines}
                      onChange={(event) => setSemanticLines(event.target.value)}
                      className="h-24 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 text-xs text-on-surface outline-none focus:border-primary"
                    />
                  </label>
                  <button
                    onClick={buildSemanticCasesFromText}
                    disabled={taskRunActionBusy || !semanticLines.trim()}
                    className="mt-2 inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
                  >
                    {busyAction === "build-semantic-cases" ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Library size={14} />
                    )}
                    生成语义 JSON
                  </button>
                </div>
                <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <input
                      type="file"
                      accept=".json,.jsonl,.ndjson,.csv,.txt,.md"
                      aria-label="Semantic case file"
                      onChange={(event) =>
                        setSemanticFile(event.target.files?.[0] ?? null)
                      }
                      className="min-w-0 flex-1 rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface file:mr-3 file:rounded file:border-0 file:bg-surface-container-high file:px-2 file:py-1 file:text-xs file:text-on-surface"
                    />
                    <button
                      onClick={importSemanticCaseFile}
                      disabled={taskRunActionBusy || !semanticFile}
                      className="inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
                    >
                      {busyAction === "import-semantic-file" ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <Save size={14} />
                      )}
                      导入文件
                    </button>
                  </div>
                  {semanticFile && (
                    <p className="mt-2 break-all font-data text-[11px] text-on-surface-variant">
                      {semanticFile.name}
                    </p>
                  )}
                </div>
                <textarea
                  value={semanticJson}
                  onChange={(event) => setSemanticJson(event.target.value)}
                  className="h-44 max-h-[46vh] w-full resize-y rounded-lg border border-outline-variant/30 bg-surface p-3 font-data text-xs text-on-surface outline-none focus:border-primary"
                  aria-label="Semantic JSON"
                  spellCheck={false}
                />
                <div className="flex flex-col gap-2 sm:flex-row">
                  <button
                    onClick={importSemanticCase}
                    disabled={taskRunActionBusy}
                    className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                  >
                    <Save size={14} />
                    导入用例
                  </button>
                  <input
                    value={semanticQuery}
                    onChange={(event) => setSemanticQuery(event.target.value)}
                    className="min-w-0 flex-1 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                    aria-label="Semantic search query"
                  />
                  <button
                    onClick={searchSemanticCases}
                    disabled={taskRunActionBusy || !semanticQuery.trim()}
                    className="inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
                  >
                    <Search size={14} />
                    搜索
                  </button>
                </div>
                <div className="space-y-2">
                  {semanticResults.map((item) => (
                    <div
                      key={item.semantic_id}
                      className="rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-xs"
                    >
                      <p className="font-medium text-on-surface">
                        {item.case_id}
                      </p>
                      <p className="mt-1 text-on-surface-variant">
                        {item.scenario}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            </Panel>

            <Panel title="证据库" icon={<Database size={16} />}>
              <div className="space-y-3">
                <div className="rounded-lg border border-outline-variant/30 bg-surface p-3">
                  <div className="grid gap-2 sm:grid-cols-2">
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        证据主题
                      </span>
                      <input
                        aria-label="Evidence subject"
                        value={manualEvidenceSubject}
                        onChange={(event) =>
                          setManualEvidenceSubject(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs text-on-surface-variant">
                        源码路径
                      </span>
                      <input
                        aria-label="Evidence path"
                        value={manualEvidencePath}
                        onChange={(event) =>
                          setManualEvidencePath(event.target.value)
                        }
                        className="w-full rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 font-data text-sm text-on-surface outline-none focus:border-primary"
                      />
                    </label>
                  </div>
                  <label className="mt-2 block">
                    <span className="mb-1 block text-xs text-on-surface-variant">
                      证据说明
                    </span>
                    <textarea
                      aria-label="Evidence text"
                      value={manualEvidenceText}
                      onChange={(event) =>
                        setManualEvidenceText(event.target.value)
                      }
                      className="h-20 w-full resize-y rounded-lg border border-outline-variant/30 bg-surface-container p-3 text-xs text-on-surface outline-none focus:border-primary"
                    />
                  </label>
                  <button
                    onClick={saveManualEvidence}
                    disabled={
                      taskRunActionBusy ||
                      !manualEvidenceSubject.trim() ||
                      !workspaceId.trim() ||
                      !repoPath.trim()
                    }
                    className="mt-2 inline-flex items-center justify-center gap-2 rounded-lg bg-surface-container-high px-3 py-2 text-sm text-on-surface transition-colors hover:bg-surface disabled:opacity-50"
                  >
                    {busyAction === "save-manual-evidence" ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Save size={14} />
                    )}
                    保存证据
                  </button>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <input
                    value={memoryQuery}
                    onChange={(event) => setMemoryQuery(event.target.value)}
                    className="min-w-0 flex-1 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-sm text-on-surface outline-none focus:border-primary"
                    aria-label="Evidence search query"
                  />
                  <button
                    onClick={searchMemory}
                    disabled={taskRunActionBusy || !memoryQuery.trim()}
                    className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                  >
                    <Search size={14} />
                    搜索证据
                  </button>
                </div>
                <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-xs text-amber-400">
                  <div className="flex items-start gap-2">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <span>
                      证据库只保存结构化事实；Agent
                      原始输出会作为产物上下文保存，不会直接当作事实复用。
                    </span>
                  </div>
                </div>
                <div className="space-y-2">
                  {memoryResults.map((item) => (
                    <div
                      key={item.evidence_id}
                      className="rounded-lg border border-outline-variant/30 bg-surface px-3 py-2 text-xs"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded bg-surface-container px-1.5 py-0.5 text-on-surface-variant">
                          {item.kind}
                        </span>
                        <span className="font-medium text-on-surface">
                          {item.subject_key}
                        </span>
                        <span className="text-on-surface-variant">
                          {item.status}
                        </span>
                        {item.source_read_status && (
                          <span className="rounded bg-surface-container px-1.5 py-0.5 text-on-surface-variant">
                            source:{item.source_read_status}
                          </span>
                        )}
                        {item.usable_as_source_evidence !== undefined && (
                          <span
                            className={`rounded px-1.5 py-0.5 ${
                              item.usable_as_source_evidence
                                ? "bg-green-400/10 text-green-500"
                                : "bg-amber-400/10 text-amber-500"
                            }`}
                          >
                            usable:{String(item.usable_as_source_evidence)}
                          </span>
                        )}
                      </div>
                      {item.path && (
                        <p className="mt-1 break-words font-data text-on-surface-variant">
                          {item.path}
                        </p>
                      )}
                      {item.reason && (
                        <p className="mt-1 text-on-surface-variant">
                          {item.reason}
                        </p>
                      )}
                      {(() => {
                        const refs = evidenceAuditRefs(item.provenance ?? {});
                        if (refs.length === 0) return null;
                        return (
                          <div className="mt-2 rounded bg-surface-container px-2 py-1.5">
                            <div className="flex flex-wrap gap-1.5 font-data text-[10px] text-on-surface-variant">
                              {refs.map((ref) => (
                                <span
                                  key={`${ref.label}:${ref.artifact}`}
                                  className="rounded bg-surface px-1.5 py-0.5"
                                  title={
                                    ref.sha256
                                      ? `${ref.artifact} sha:${ref.sha256}`
                                      : ref.artifact
                                  }
                                >
                                  {ref.label}: {ref.artifact}
                                  {ref.sha256
                                    ? ` sha:${ref.sha256.slice(0, 12)}`
                                    : ""}
                                </span>
                              ))}
                            </div>
                          </div>
                        );
                      })()}
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <button
                          onClick={() => loadMemorySlices(item.evidence_id)}
                          disabled={taskRunActionBusy}
                          className="inline-flex items-center gap-1 rounded bg-surface-container px-2 py-1 text-[11px] text-on-surface-variant transition-colors hover:bg-surface-container-high disabled:opacity-50"
                        >
                          {busyAction ===
                          `memory-slices-${item.evidence_id}` ? (
                            <Loader2 size={12} className="animate-spin" />
                          ) : (
                            <ClipboardList size={12} />
                          )}
                          源码切片
                        </button>
                        {memorySlices[item.evidence_id] && (
                          <span className="font-data text-[11px] text-on-surface-variant">
                            {memorySlices[item.evidence_id].length} slice(s)
                          </span>
                        )}
                      </div>
                      {memorySlices[item.evidence_id] &&
                        memorySlices[item.evidence_id].length > 0 && (
                          <div className="mt-2 space-y-2 text-on-surface-variant">
                            {memorySlices[item.evidence_id]
                              .slice(0, 3)
                              .map((slice) => (
                                <div
                                  key={slice.slice_id}
                                  className="rounded bg-surface-container px-2 py-1.5"
                                >
                                  <p className="break-words font-data text-[11px]">
                                    {slice.file_path}:{slice.start_line}-
                                    {slice.end_line} sha:
                                    {slice.sha256.slice(0, 12)}
                                    {slice.integrity_status && (
                                      <span
                                        className={`ml-1 ${
                                          slice.integrity_status ===
                                          "verified_current"
                                            ? "text-green-500"
                                            : "text-warning"
                                        }`}
                                      >
                                        {slice.integrity_status}
                                      </span>
                                    )}
                                  </p>
                                  {(slice.current_sha256 ||
                                    slice.validation_error) && (
                                    <p className="mt-1 break-words font-data text-[10px] text-warning">
                                      {slice.current_sha256
                                        ? `current:${slice.current_sha256.slice(0, 12)} `
                                        : ""}
                                      {slice.validation_error || ""}
                                    </p>
                                  )}
                                  <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words font-data text-[10px] text-on-surface">
                                    {slice.excerpt}
                                  </pre>
                                </div>
                              ))}
                          </div>
                        )}
                      {item.source_slices &&
                        item.source_slices.length > 0 &&
                        !memorySlices[item.evidence_id] && (
                          <div className="mt-2 space-y-1 text-on-surface-variant">
                            {item.source_slices.slice(0, 3).map((slice) => (
                              <p
                                key={slice.slice_id}
                                className="break-words font-data text-[11px]"
                              >
                                slice {slice.file_path}:{slice.start_line}-
                                {slice.end_line} sha:
                                {slice.sha256.slice(0, 12)}
                                {slice.integrity_status && (
                                  <span
                                    className={`ml-1 ${
                                      slice.integrity_status ===
                                      "verified_current"
                                        ? "text-green-500"
                                        : "text-warning"
                                    }`}
                                  >
                                    {slice.integrity_status}
                                  </span>
                                )}
                              </p>
                            ))}
                          </div>
                        )}
                    </div>
                  ))}
                </div>
              </div>
            </Panel>
          </>);
}
