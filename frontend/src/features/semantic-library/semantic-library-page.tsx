"use client";

import { Archive, Copy, Download, FileUp, RotateCcw, Search, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { semanticLibraryApi } from "@/lib/api/semantic-library";
import { compactMachineToken } from "@/lib/display-text";
import type {
  SemanticCase,
  SemanticCaseFacets,
  SemanticImportCommitResult,
  SemanticImportPreview,
} from "@/lib/types/semantic";

type Filters = {
  q: string;
  feature: string;
  module: string;
  test_level: string;
  interface: string;
  tag: string;
  status: string;
  source: string;
};

const EMPTY_FILTERS: Filters = { q: "", feature: "", module: "", test_level: "", interface: "", tag: "", status: "active", source: "" };
const PAGE_SIZE = 25;

export function SemanticLibraryPage() {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [items, setItems] = useState<SemanticCase[]>([]);
  const [facets, setFacets] = useState<SemanticCaseFacets | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<SemanticCase | null>(null);
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [importing, setImporting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const result = await semanticLibraryApi.list({ ...filters, page, page_size: PAGE_SIZE });
      setItems(result.items); setTotal(result.total);
    } catch (cause) { setError(message(cause, "语义用例加载失败")); }
    finally { setLoading(false); }
  }, [filters, page]);

  useEffect(() => { const timer = window.setTimeout(() => void load(), 300); return () => window.clearTimeout(timer); }, [load]);
  useEffect(() => { void semanticLibraryApi.facets().then(setFacets).catch(() => undefined); }, []);

  const updateFilter = (key: keyof Filters, value: string) => { setPage(1); setFilters((current) => ({ ...current, [key]: value })); };
  const openCase = async (semanticId: string) => {
    try { setSelected(await semanticLibraryApi.get(semanticId)); setEditing(false); }
    catch (cause) { setError(message(cause, "用例详情加载失败")); }
  };
  const lifecycle = async (action: "deprecate" | "restore") => {
    if (!selected) return;
    try {
      const next = action === "deprecate"
        ? await semanticLibraryApi.deprecate(selected.semantic_id)
        : await semanticLibraryApi.restore(selected.semantic_id);
      setSelected(next); await load();
    } catch (cause) { setError(message(cause, "状态更新失败")); }
  };
  const copyCase = async () => {
    if (!selected) return;
    const caseId = `${selected.case_id}_COPY_${Date.now().toString().slice(-6)}`;
    try {
      const created = await semanticLibraryApi.create({ ...selected, semantic_id: undefined, case_id: caseId, status: "active", source_ref: `copy:${selected.case_id}` });
      await load(); await openCase(created.semantic_id);
    } catch (cause) { setError(message(cause, "复制失败")); }
  };

  return <main className="ct-asset-page">
    <header className="ct-v2-page-header"><div><h1>语义用例库</h1></div><button className="ct-v2-primary-button" type="button" onClick={() => setImporting(true)}><FileUp size={16} />导入用例</button></header>
    <section className="ct-asset-filters" aria-label="语义用例筛选">
      <label className="ct-v2-search-field"><Search size={15} /><input aria-label="搜索语义用例" value={filters.q} onChange={(event) => updateFilter("q", event.target.value)} placeholder="场景、Case ID、标签或术语" /></label>
      <Facet label="Feature" value={filters.feature} items={facets?.features} onChange={(value) => updateFilter("feature", value)} />
      <Facet label="Module" value={filters.module} items={facets?.modules} onChange={(value) => updateFilter("module", value)} />
      <Facet label="测试级别" value={filters.test_level} items={facets?.test_levels} onChange={(value) => updateFilter("test_level", value)} />
      <Facet label="接口" value={filters.interface} items={facets?.interfaces} onChange={(value) => updateFilter("interface", value)} />
      <Facet label="标签" value={filters.tag} items={facets?.tags} onChange={(value) => updateFilter("tag", value)} />
      <Facet label="状态" value={filters.status} items={facets?.statuses} onChange={(value) => updateFilter("status", value)} />
      <Facet label="来源" value={filters.source} items={facets?.sources} onChange={(value) => updateFilter("source", value)} />
    </section>
    {error && <div className="ct-v2-notice is-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError("")}><X size={14} /></button></div>}
    <div className="ct-v2-table-summary"><span>{loading ? "正在刷新" : `${total} 条用例`}</span><button type="button" onClick={() => { setPage(1); setFilters(EMPTY_FILTERS); }}>清除筛选</button></div>
    <section className={`ct-asset-workspace ${selected ? "has-detail" : ""}`}>
      <div className="ct-v2-table-shell"><table className="ct-v2-table"><thead><tr><th>Case ID / 场景</th><th>Feature</th><th>Module</th><th>测试级别</th><th>接口</th><th>标签</th><th>状态</th><th>来源</th><th>更新时间</th></tr></thead><tbody>
        {items.map((item) => <tr key={item.semantic_id} className={selected?.semantic_id === item.semantic_id ? "is-selected" : ""} onClick={() => void openCase(item.semantic_id)}><td><button type="button" className="ct-asset-row-open"><strong title={item.case_id}>{compactMachineToken(item.case_id, 26)}</strong><span>{item.scenario}</span><small>{item.matched_fields?.length ? `命中：${item.matched_fields.map((field) => compactMachineToken(field, 18)).join("、")}` : `${item.counts?.preconditions ?? item.preconditions.length} 前置 · ${item.counts?.actions ?? item.actions.length} 步骤 · ${item.counts?.expected ?? item.expected.length} 预期`}</small></button></td><td title={item.feature || undefined}>{compactMachineToken(item.feature, 22)}</td><td title={item.module || undefined}>{compactMachineToken(item.module, 22)}</td><td>{testLevelLabel(item.test_level)}</td><td title={item.interface || undefined}>{compactMachineToken(item.interface, 22)}</td><td><TagList items={item.tags} /></td><td><span className={`ct-v2-status is-${item.status}`}>{item.status === "deprecated" ? "已废弃" : "使用中"}</span></td><td title={item.source_ref || undefined}>{compactMachineToken(item.source_ref, 24)}</td><td>{formatTime(item.updated_at)}</td></tr>)}
        {!loading && !items.length && <tr><td colSpan={9}><div className="ct-v2-table-empty">没有匹配的语义用例</div></td></tr>}
      </tbody></table></div>
      {selected && <SemanticDetail item={selected} editing={editing} onEdit={() => setEditing(true)} onClose={() => setSelected(null)} onSaved={async (item) => { setSelected(item); setEditing(false); await load(); }} onLifecycle={lifecycle} onCopy={copyCase} />}
    </section>
    <AssetPagination label="语义用例分页" page={page} total={total} onPage={setPage} />
    {importing && <SemanticImportWizard onClose={() => setImporting(false)} onCommitted={async () => { await load(); setFacets(await semanticLibraryApi.facets()); }} />}
  </main>;
}

function SemanticDetail({ item, editing, onEdit, onClose, onSaved, onLifecycle, onCopy }: { item: SemanticCase; editing: boolean; onEdit: () => void; onClose: () => void; onSaved: (item: SemanticCase) => Promise<void>; onLifecycle: (action: "deprecate" | "restore") => Promise<void>; onCopy: () => Promise<void> }) {
  const [draft, setDraft] = useState(item);
  const [saving, setSaving] = useState(false);
  useEffect(() => setDraft(item), [item]);
  const save = async () => { setSaving(true); try { await onSaved(await semanticLibraryApi.update(item.semantic_id, editablePayload(draft))); } finally { setSaving(false); } };
  return <aside className="ct-asset-detail" aria-label="语义用例详情"><header><div><span title={item.case_id}>{compactMachineToken(item.case_id, 28)}</span><h2>{item.scenario || "未命名场景"}</h2></div><button type="button" onClick={onClose} title="关闭详情"><X size={17} /></button></header>
    {editing ? <div className="ct-asset-editor"><TextField label="Case ID" value={draft.case_id} onChange={(case_id) => setDraft({ ...draft, case_id })} /><TextField label="场景" value={draft.scenario} onChange={(scenario) => setDraft({ ...draft, scenario })} multiline /><div className="ct-asset-editor-grid"><TextField label="Feature" value={draft.feature} onChange={(feature) => setDraft({ ...draft, feature })} /><TextField label="Module" value={draft.module} onChange={(module) => setDraft({ ...draft, module })} /><TextField label="测试级别" value={draft.test_level} onChange={(test_level) => setDraft({ ...draft, test_level })} /><TextField label="接口" value={draft.interface} onChange={(interfaceValue) => setDraft({ ...draft, interface: interfaceValue })} /></div><ListField label="前置条件" values={draft.preconditions} onChange={(preconditions) => setDraft({ ...draft, preconditions })} /><ListField label="操作步骤" values={draft.actions} onChange={(actions) => setDraft({ ...draft, actions })} /><ListField label="预期结果" values={draft.expected} onChange={(expected) => setDraft({ ...draft, expected })} /><ListField label="标签" values={draft.tags} onChange={(tags) => setDraft({ ...draft, tags })} /><div className="ct-asset-detail-actions"><button type="button" onClick={() => setDraft(item)}>撤销</button><button className="ct-v2-primary-button" disabled={saving} type="button" onClick={() => void save()}>{saving ? "保存中" : "保存修改"}</button></div></div> : <div className="ct-asset-detail-body"><DetailList label="前置条件" items={item.preconditions} /><DetailList label="操作步骤" items={item.actions} ordered /><DetailList label="预期结果" items={item.expected} /><dl><div><dt>Feature</dt><dd title={item.feature || undefined}>{compactMachineToken(item.feature, 30)}</dd></div><div><dt>Module</dt><dd title={item.module || undefined}>{compactMachineToken(item.module, 30)}</dd></div><div><dt>接口</dt><dd title={item.interface || undefined}>{compactMachineToken(item.interface, 30)}</dd></div><div><dt>来源</dt><dd title={item.source_ref || undefined}>{compactMachineToken(item.source_ref, 30)}</dd></div></dl><section><h3>引用关系</h3><p>{item.references?.length ? `${item.references.length} 个新运行引用` : "历史引用为空；后续运行会在这里记录来源。"}</p></section><div className="ct-asset-detail-actions"><button type="button" onClick={onEdit}>编辑</button><button type="button" onClick={() => void onCopy()}><Copy size={14} />复制</button>{item.status === "deprecated" ? <button type="button" onClick={() => void onLifecycle("restore")}><RotateCcw size={14} />恢复</button> : <button className="is-danger" type="button" onClick={() => void onLifecycle("deprecate")}><Archive size={14} />废弃</button>}</div></div>}
  </aside>;
}

function SemanticImportWizard({ onClose, onCommitted }: { onClose: () => void; onCommitted: () => Promise<void> }) {
  const [step, setStep] = useState(1); const [file, setFile] = useState<File | null>(null); const [headers, setHeaders] = useState<string[]>([]); const [mapping, setMapping] = useState<Record<string, string>>({}); const [separator, setSeparator] = useState(""); const [defaults, setDefaults] = useState({ feature: "", module: "", test_level: "black_box" }); const [preview, setPreview] = useState<SemanticImportPreview | null>(null); const [strategy, setStrategy] = useState<"skip" | "overwrite" | "create_new">("skip"); const [result, setResult] = useState<SemanticImportCommitResult | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const choose = async (next: File | null) => { setFile(next); setPreview(null); setResult(null); if (!next) { setHeaders([]); return; } const first = (await next.slice(0, 8192).text()).split(/\r?\n/, 1)[0] || ""; const nextHeaders = next.name.toLowerCase().endsWith(".csv") ? first.split(",").map((value) => value.trim()).filter(Boolean) : []; setHeaders(nextHeaders); setMapping(Object.fromEntries(nextHeaders.map((header) => [header, ["case_id", "feature", "module", "scenario", "preconditions", "actions", "expected", "test_level", "interface", "terms", "assertion_style", "tags", "source_ref", "status"].includes(header) ? header : ""]))); };
  const runPreview = async () => { if (!file) return; setBusy(true); setError(""); try { setPreview(await semanticLibraryApi.previewImport(file, { mapping, text_separator: separator, defaults })); setStep(3); } catch (cause) { setError(message(cause, "预览失败")); } finally { setBusy(false); } };
  const commit = async () => { if (!preview) return; setBusy(true); setError(""); try { const next = await semanticLibraryApi.commitImport(preview.preview_id, strategy); setResult(next); setStep(5); await onCommitted(); } catch (cause) { setError(message(cause, "导入失败")); } finally { setBusy(false); } };
  const textFile = Boolean(file && /\.(txt|md|markdown)$/i.test(file.name));
  return <div className="ct-asset-modal" role="dialog" aria-modal="true" aria-label="导入语义用例"><section><header><div><span>导入向导 · 第 {step}/5 步</span><h2>{["上传文件", "字段映射", "预览与验证", "冲突策略", "导入结果"][step - 1]}</h2></div><button type="button" onClick={onClose}><X size={18} /></button></header><ol className="ct-asset-steps">{["上传", "映射", "预览", "冲突", "完成"].map((label, index) => <li key={label} className={step >= index + 1 ? "is-active" : ""}>{index + 1}<span>{label}</span></li>)}</ol>
    <div className="ct-asset-modal-body">{step === 1 && <div className="ct-asset-upload"><FileUp size={26} /><strong>{file?.name || "选择 JSON、JSONL、CSV、TXT 或 Markdown"}</strong><p>文件只在预览阶段解析，不会直接写入语义库。</p><label><input type="file" accept=".json,.jsonl,.ndjson,.csv,.txt,.md,.markdown" onChange={(event) => void choose(event.target.files?.[0] ?? null)} />选择文件</label></div>}
    {step === 2 && <div className="ct-asset-mapping"><div className="ct-asset-editor-grid"><TextField label="默认 Feature" value={defaults.feature} onChange={(feature) => setDefaults({ ...defaults, feature })} /><TextField label="默认 Module" value={defaults.module} onChange={(moduleName) => setDefaults({ ...defaults, module: moduleName })} /></div>{headers.length > 0 && <section><h3>CSV 字段映射</h3>{headers.map((header) => <label key={header}><span>{header}</span><select value={mapping[header] || ""} onChange={(event) => setMapping({ ...mapping, [header]: event.target.value })}><option value="">忽略</option>{["case_id", "feature", "module", "scenario", "preconditions", "actions", "expected", "test_level", "interface", "terms", "assertion_style", "tags", "source_ref"].map((field) => <option key={field} value={field}>{field}</option>)}</select></label>)}</section>}{textFile && <label><span>文本分隔规则</span><select value={separator} onChange={(event) => setSeparator(event.target.value)}><option value="">请选择</option><option value="pipe">竖线：Case ID | 场景 | 预期</option><option value="tab">Tab：Case ID ⇥ 场景 ⇥ 预期</option><option value="arrow">箭头：Case ID -&gt; 场景 -&gt; 预期</option></select></label>}</div>}
    {step === 3 && preview && <div className="ct-asset-preview"><div className="ct-asset-preview-metrics"><span><strong>{preview.valid_count}</strong>有效</span><span className="is-error"><strong>{preview.invalid_count}</strong>无效</span><span><strong>{preview.duplicate_case_ids.length}</strong>重复 ID</span><span><strong>{preview.possible_duplicate_scenarios.length}</strong>疑似重复</span></div>{preview.unknown_fields.length > 0 && <p className="ct-v2-notice is-warning">未知字段将被忽略：{preview.unknown_fields.join("、")}</p>}<div className="ct-asset-preview-table"><table><thead><tr><th>#</th><th>Case ID</th><th>场景</th><th>验证</th></tr></thead><tbody>{preview.rows.map((row) => <tr key={row.index}><td>{row.index + 1}</td><td>{String(row.case.case_id || "—")}</td><td>{String(row.case.scenario || "—")}</td><td>{row.errors.length ? <span className="is-error">{row.errors.join("；")}</span> : row.warnings.length ? <span className="is-warning">{row.warnings.join("；")}</span> : <span className="is-valid">有效</span>}</td></tr>)}</tbody></table></div></div>}
    {step === 4 && preview && <div className="ct-asset-conflicts"><h3>{preview.duplicate_case_ids.length ? `${preview.duplicate_case_ids.length} 个 Case ID 与库内冲突` : "没有发现 Case ID 冲突"}</h3>{[["skip", "跳过已有", "保留库内版本，只导入新 Case ID。"], ["overwrite", "覆盖已有", "用本次文件内容更新同 Case ID 用例。"], ["create_new", "创建副本", "自动生成新 Case ID，同时保留两个版本。"]].map(([value, label, copy]) => <label key={value} className={strategy === value ? "is-selected" : ""}><input type="radio" name="strategy" value={value} checked={strategy === value} onChange={() => setStrategy(value as typeof strategy)} /><strong>{label}</strong><span>{copy}</span></label>)}<p>{preview.invalid_count} 条无效记录不会写入，可在完成后下载。</p></div>}
    {step === 5 && result && <div className="ct-asset-import-result"><strong>{result.imported_count} 条已导入</strong><p>{result.skipped_count} 条跳过，{result.failed_count} 条失败。所有失败记录都保留原始字段和原因。</p>{result.failed_count > 0 && <button type="button" onClick={() => downloadFailures(result)}><Download size={15} />下载失败记录</button>}</div>}{error && <div className="ct-v2-notice is-error" role="alert">{error}</div>}</div>
    <footer>{step > 1 && step < 5 && <button type="button" onClick={() => setStep(step - 1)}>上一步</button>}<span />{step === 1 && <button className="ct-v2-primary-button" disabled={!file} type="button" onClick={() => setStep(2)}>下一步</button>}{step === 2 && <button className="ct-v2-primary-button" disabled={busy || (textFile && !separator)} type="button" onClick={() => void runPreview()}>{busy ? "解析中" : "生成预览"}</button>}{step === 3 && <button className="ct-v2-primary-button" type="button" onClick={() => setStep(4)}>选择冲突策略</button>}{step === 4 && <button className="ct-v2-primary-button" disabled={busy} type="button" onClick={() => void commit()}>{busy ? "导入中" : "确认导入"}</button>}{step === 5 && <button className="ct-v2-primary-button" type="button" onClick={onClose}>完成</button>}</footer></section></div>;
}

function Facet({ label, value, items, onChange }: { label: string; value: string; items?: Array<{ value: string; count: number }>; onChange: (value: string) => void }) { return <label><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)}><option value="">全部</option>{items?.map((item) => <option key={item.value} value={item.value}>{compactMachineToken(item.value, 26)} ({item.count})</option>)}</select></label>; }
function TextField({ label, value, onChange, multiline = false }: { label: string; value: string; onChange: (value: string) => void; multiline?: boolean }) { return <label><span>{label}</span>{multiline ? <textarea value={value} onChange={(event) => onChange(event.target.value)} /> : <input value={value} onChange={(event) => onChange(event.target.value)} />}</label>; }
function ListField({ label, values, onChange }: { label: string; values: string[]; onChange: (values: string[]) => void }) { return <label><span>{label}（每行一项）</span><textarea value={values.join("\n")} onChange={(event) => onChange(event.target.value.split("\n").map((value) => value.trim()).filter(Boolean))} /></label>; }
function DetailList({ label, items, ordered = false }: { label: string; items: string[]; ordered?: boolean }) { const Tag = ordered ? "ol" : "ul"; return <section><h3>{label} <span>{items.length}</span></h3>{items.length ? <Tag>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</Tag> : <p>未填写</p>}</section>; }
function TagList({ items }: { items: string[] }) { return <div className="ct-asset-tags">{items.slice(0, 3).map((item) => <span key={item} title={item}>{compactMachineToken(item, 18)}</span>)}{items.length > 3 && <span>+{items.length - 3}</span>}</div>; }
function editablePayload(item: SemanticCase): Partial<SemanticCase> { return { case_id: item.case_id, feature: item.feature, module: item.module, scenario: item.scenario, preconditions: item.preconditions, actions: item.actions, expected: item.expected, test_level: item.test_level, interface: item.interface, terms: item.terms, assertion_style: item.assertion_style, tags: item.tags, source_ref: item.source_ref }; }
function downloadFailures(result: SemanticImportCommitResult) { const blob = new Blob(result.failed.map((item) => `${JSON.stringify(item)}\n`), { type: "application/x-ndjson" }); const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = `${result.import_id}-failures.ndjson`; anchor.click(); URL.revokeObjectURL(url); }
function testLevelLabel(value: string) { return ({ black_box: "黑盒", gray_box: "灰盒", white_box: "白盒" } as Record<string, string>)[value] || value || "—"; }
function formatTime(value: string) { if (!value) return "—"; return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
function AssetPagination({ label, page, total, onPage }: { label: string; page: number; total: number; onPage: (page: number) => void }) { const pages = Math.max(1, Math.ceil(total / PAGE_SIZE)); return <nav className="ct-v2-pagination" aria-label={label}><button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>上一页</button><span>第 {page} / {pages} 页</span><button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>下一页</button></nav>; }
function message(cause: unknown, fallback: string) { return cause instanceof Error ? cause.message : fallback; }
