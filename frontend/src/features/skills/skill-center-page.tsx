"use client";

import Link from "next/link";
import { BadgeCheck, ExternalLink, FileArchive, Hammer, Plus, RefreshCw, Search, ShieldCheck, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { skillsApi } from "@/lib/api/skills";
import type { SkillBuild, SkillDraft, SkillPreset, SkillReview, SkillVersion } from "@/lib/types/skill";
import { skillDisplayName } from "./skill-display";
import { SkillVersionSummary } from "./skill-version-summary";

export function SkillCenterPage() {
  const [items, setItems] = useState<SkillVersion[]>([]);
  const [presets, setPresets] = useState<SkillPreset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [labBusy, setLabBusy] = useState(false);
  const [projectName, setProjectName] = useState("CodeTalk Skill Lab");
  const [packId, setPackId] = useState("pack.codetalks-lab");
  const [projectId, setProjectId] = useState("");
  const [presetId, setPresetId] = useState("module-analysis");
  const [sourceRoot, setSourceRoot] = useState("");
  const [scenarioId, setScenarioId] = useState("module-analysis");
  const [skillId, setSkillId] = useState("skill.codetalks-module-full-analysis");
  const [draft, setDraft] = useState<SkillDraft | null>(null);
  const [draftPath, setDraftPath] = useState("references/tool-routing.md");
  const [draftContent, setDraftContent] = useState("# tool routing\n\nUpdated from Skill Center.\n");
  const [build, setBuild] = useState<SkillBuild | null>(null);
  const [review, setReview] = useState<SkillReview | null>(null);
  const [labVersion, setLabVersion] = useState<SkillVersion | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [log, setLog] = useState<string[]>([]);

  const appendLog = (message: string) => setLog((current) => [message, ...current].slice(0, 6));
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [versionResult, presetResult] = await Promise.all([
        skillsApi.listVersions(),
        skillsApi.listPresets(),
      ]);
      setItems(versionResult.items);
      setPresets(presetResult.items);
      setSelectedId((current) => current || versionResult.items[0]?.version_id || "");
      const preset = presetResult.items.find((item) => item.scenario_id === presetId) || presetResult.items[0];
      if (preset) {
        setPresetId(preset.scenario_id);
        setScenarioId((current) => current || preset.scenario_id);
        setSkillId((current) => current || preset.skill_id);
        setSourceRoot((current) => current || preset.source_root);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Skill 加载失败");
    } finally {
      setLoading(false);
    }
  }, [presetId]);

  useEffect(() => {
    void load();
  }, [load]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((item) =>
      [item.skill_id, item.version_id, item.content_digest, item.review_evidence_digest]
        .some((value) => value.toLowerCase().includes(needle)),
    );
  }, [items, query]);
  const selected = visible.find((item) => item.version_id === selectedId) || visible[0] || null;
  const projectCount = new Set(items.map((item) => item.project_id)).size;
  const createTaskHref = selected
    ? `/tasks/new?skill_id=${encodeURIComponent(selected.skill_id)}&skill_version_id=${encodeURIComponent(selected.version_id)}`
    : "/tasks/new";

  const runLabAction = async (action: () => Promise<string>) => {
    setLabBusy(true);
    setError("");
    try {
      appendLog(await action());
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Skill 操作失败");
    } finally {
      setLabBusy(false);
    }
  };

  const ensureProject = async () => {
    if (projectId) return projectId;
    const project = await skillsApi.createProject({ name: projectName, pack_id: packId });
    setProjectId(project.project_id);
    return project.project_id;
  };
  const selectPreset = (id: string) => {
    const preset = presets.find((item) => item.scenario_id === id);
    setPresetId(id);
    if (!preset) return;
    setSourceRoot(preset.source_root);
    setScenarioId(preset.scenario_id);
    setSkillId(preset.skill_id);
  };

  return (
    <main className="ct-v2-library ct-skill-center">
      <header className="ct-v2-page-header">
        <div>
          <span className="ct-v2-eyebrow">Skill-first Runtime</span>
          <h1>Skill 中心</h1>
          <p>发布版本是创建 Task 的唯一入口；每个版本保留 content digest 与 Review digest。</p>
        </div>
        <div className="ct-v2-page-actions">
          <button type="button" onClick={() => void load()}>
            <RefreshCw size={15} />
            刷新
          </button>
          <Link className="ct-v2-primary-button" aria-disabled={!selected} href={createTaskHref}>
            <Plus size={15} />
            创建任务
          </Link>
        </div>
      </header>

      <section className="ct-skill-center__metrics" aria-label="Skill 发布概览">
        <Metric icon={<FileArchive size={16} />} label="已发布版本" value={loading ? "--" : String(items.length)} />
        <Metric icon={<ShieldCheck size={16} />} label="Skill 项目" value={loading ? "--" : String(projectCount)} />
        <Metric icon={<BadgeCheck size={16} />} label="Review 状态" value={items.length ? "已冻结" : "等待发布"} />
      </section>

      <section className="ct-v2-task-filters" aria-label="Skill 筛选">
        <label className="ct-v2-search-field">
          <Search size={15} />
          <input
            aria-label="搜索 Skill"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索 skill id、version id 或 digest"
          />
        </label>
      </section>

      {error && <div className="ct-v2-notice is-error" role="alert">{error}</div>}
      <div className="ct-v2-table-summary">
        <span>{loading ? "正在刷新" : `${visible.length} 个发布版本`}</span>
        <span>点击版本查看冻结摘要</span>
      </div>

      <section className={`ct-skill-center__workspace ${selected ? "has-detail" : ""}`}>
        <div className="ct-v2-table-shell">
          <table className="ct-v2-table">
            <thead>
              <tr>
                <th>Skill</th>
                <th>Version</th>
                <th>Project</th>
                <th>Content digest</th>
                <th>Review digest</th>
                <th>发布时间</th>
                <th aria-label="操作" />
              </tr>
            </thead>
            <tbody>
              {visible.map((item) => {
                const active = selected?.version_id === item.version_id;
                return (
                  <tr key={item.version_id} className={active ? "is-selected" : ""} onClick={() => setSelectedId(item.version_id)}>
                    <td><button type="button" className="ct-skill-center__row-open"><strong>{skillDisplayName(item)}</strong><small>{item.skill_id}</small></button></td>
                    <td><span className="ct-v2-status is-published">Published</span><small>{item.version_id}</small></td>
                    <td>{item.project_id}</td>
                    <td><code>{shortDigest(item.content_digest)}</code></td>
                    <td><code>{shortDigest(item.review_evidence_digest)}</code></td>
                    <td>{formatTime(item.created_at)}</td>
                    <td>
                      <div className="ct-v2-row-actions">
                        <Link href={`/tasks/new?skill_id=${encodeURIComponent(item.skill_id)}&skill_version_id=${encodeURIComponent(item.version_id)}`} title="用此 Skill 创建任务">
                          <ExternalLink size={15} />
                        </Link>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {!loading && !visible.length && (
                <tr>
                  <td colSpan={7}><div className="ct-v2-table-empty">暂无已发布 Skill Version</div></td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {selected && (
          <aside className="ct-skill-center__detail" aria-label="Skill version detail">
            <SkillVersionSummary version={selected} />
            <dl>
              <div><dt>Source ZIP</dt><dd>{selected.source_zip_path}</dd></div>
              <div><dt>Skill IR</dt><dd>{selected.ir_path}</dd></div>
              <div><dt>Validation</dt><dd>{selected.validation_report_path}</dd></div>
              <div><dt>Manifest</dt><dd>{selected.manifest_path}</dd></div>
            </dl>
            <Link className="ct-v2-primary-button" href={`/tasks/new?skill_id=${encodeURIComponent(selected.skill_id)}&skill_version_id=${encodeURIComponent(selected.version_id)}`}>
              <Plus size={15} />
              用此版本创建任务
            </Link>
          </aside>
        )}
      </section>

      <section className="ct-skill-lab" aria-label="Skill Lab">
        <div className="ct-skill-lab__heading">
          <div>
            <span className="ct-v2-eyebrow">Skill Lab</span>
            <h2>创建、导入、修改与发布</h2>
          </div>
          <Hammer size={18} />
        </div>
        <div className="ct-skill-lab__grid">
          <label><span>项目名</span><input aria-label="Skill 项目名" value={projectName} onChange={(event) => setProjectName(event.target.value)} /></label>
          <label><span>Pack ID</span><input aria-label="Skill Pack ID" value={packId} onChange={(event) => setPackId(event.target.value)} /></label>
          <button type="button" disabled={labBusy} onClick={() => void runLabAction(async () => `Project ${await ensureProject()} 已就绪`)}>
            <Plus size={14} />
            创建项目
          </button>
        </div>
        <div className="ct-skill-lab__grid">
          <label><span>预设场景</span><select aria-label="CodeTalk 预设场景" value={presetId} onChange={(event) => selectPreset(event.target.value)}>{presets.map((item) => <option key={item.scenario_id} value={item.scenario_id}>{item.label}</option>)}</select></label>
          <label><span>Source Root</span><input aria-label="Skill Source Root" value={sourceRoot} onChange={(event) => setSourceRoot(event.target.value)} /></label>
          <label><span>Scenario ID</span><input aria-label="Skill Scenario ID" value={scenarioId} onChange={(event) => setScenarioId(event.target.value)} /></label>
          <label><span>Skill ID</span><input aria-label="Draft Skill ID" value={skillId} onChange={(event) => setSkillId(event.target.value)} /></label>
          <button type="button" disabled={labBusy} onClick={() => void runLabAction(async () => {
            const id = await ensureProject();
            const created = await skillsApi.createDraftFromSource(id, { source_root: sourceRoot, source_scenario_id: scenarioId, skill_id: skillId });
            setDraft(created);
            setBuild(null);
            setReview(null);
            setLabVersion(null);
            return `Draft ${created.draft_id} 已创建`;
          })}>
            <FileArchive size={14} />
            从源创建草稿
          </button>
        </div>
        <div className="ct-skill-lab__grid">
          <label className="is-wide"><span>ZIP 导入</span><input aria-label="Skill ZIP 文件" type="file" accept=".zip,application/zip" onChange={(event) => setImportFile(event.target.files?.[0] || null)} /></label>
          <button type="button" disabled={labBusy || !importFile} onClick={() => void runLabAction(async () => {
            if (!importFile) throw new Error("请选择 ZIP 文件");
            const id = await ensureProject();
            const imported = await skillsApi.importPackage(id, importFile, "skill.imported");
            setDraft(imported.drafts[0] || null);
            setBuild(null);
            setReview(null);
            setLabVersion(null);
            return `导入 ${imported.drafts.length} 个 Draft`;
          })}>
            <Upload size={14} />
            导入
          </button>
        </div>
        <div className="ct-skill-lab__editor">
          <label><span>草稿文件</span><input aria-label="草稿文件路径" value={draftPath} onChange={(event) => setDraftPath(event.target.value)} /></label>
          <label><span>内容</span><textarea aria-label="草稿文件内容" rows={5} value={draftContent} onChange={(event) => setDraftContent(event.target.value)} /></label>
          <button type="button" disabled={labBusy || !draft} onClick={() => void runLabAction(async () => {
            if (!draft) throw new Error("请先创建 Draft");
            const result = await skillsApi.writeDraftFile(draft.draft_id, { relative_path: draftPath, content: draftContent });
            return `修改 ${result.relative_path} · ${shortDigest(result.digest)}`;
          })}>写入草稿文件</button>
        </div>
        <div className="ct-skill-lab__actions">
          <button type="button" disabled={labBusy || !draft} onClick={() => void runLabAction(async () => {
            if (!draft) throw new Error("请先创建 Draft");
            const built = await skillsApi.buildDraft(draft.draft_id);
            setBuild(built);
            setReview(null);
            setLabVersion(null);
            return `Build ${built.build_id} · ${shortDigest(built.content_digest)}`;
          })}>构建</button>
          <button type="button" disabled={labBusy || !build} onClick={() => void runLabAction(async () => {
            if (!build) throw new Error("请先构建");
            const checked = await skillsApi.runReview(build.build_id, {
              scope: "full",
              purpose: "Skill Center release review",
              session_id: `skill-center/${Date.now()}`,
              provider: "deepseek",
              requested_model: "deepseek-v4-flash",
              effective_model: "deepseek-v4-flash",
              response_model: "deepseek-v4-flash",
              declared_context_window_tokens: 200000,
              requested_max_output_tokens: 4096,
            });
            setReview(checked);
            return `Review ${checked.decision} · ${shortDigest(checked.review_evidence_digest)}`;
          })}>审查</button>
          <button type="button" disabled={labBusy || !build || !review} onClick={() => void runLabAction(async () => {
            if (!build) throw new Error("请先构建");
            const version = await skillsApi.publishBuild(build.build_id);
            setLabVersion(version);
            setSelectedId(version.version_id);
            return `发布 ${version.version_id}`;
          })}>发布</button>
        </div>
        <div className="ct-skill-lab__state" aria-label="Skill Lab 状态">
          <StateLine label="Project" value={projectId || "--"} />
          <StateLine label="Draft" value={draft?.draft_id || "--"} />
          <StateLine label="Build" value={build?.build_id || "--"} />
          <StateLine label="Review" value={review?.decision || "--"} />
          <StateLine label="Version" value={labVersion?.version_id || "--"} />
        </div>
        {log.length > 0 && <ol className="ct-skill-lab__log">{log.map((item) => <li key={item}>{item}</li>)}</ol>}
      </section>
    </main>
  );
}

function Metric({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return <div><span>{icon}{label}</span><strong>{value}</strong></div>;
}

function StateLine({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function shortDigest(value: string) {
  return value.replace(/^sha256:/, "").slice(0, 12) || "--";
}

function formatTime(value: string) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
