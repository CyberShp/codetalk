"use client";

import { ArrowLeft, LayoutTemplate, Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { workflowsApi } from "@/lib/api/workflows";
import type { WorkflowCanvasTemplate, WorkflowCanvasTemplateId } from "@/lib/types/workflow";

export function CanvasEntry() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [template, setTemplate] = useState<WorkflowCanvasTemplateId>("free_source_analysis");
  const [templates, setTemplates] = useState<WorkflowCanvasTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templatesError, setTemplatesError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const nameInput = useRef<HTMLInputElement>(null);
  const descriptionInput = useRef<HTMLTextAreaElement>(null);

  const loadTemplates = useCallback(async () => {
    setTemplatesLoading(true);
    setTemplatesError("");
    try {
      const catalog = await workflowsApi.listTemplates();
      if (catalog.meta.schema_version !== 3 || catalog.meta.migration_contract_version !== 1) {
        throw new Error("模板版本不兼容，请刷新页面或联系管理员升级前后端版本");
      }
      if (!catalog.items.length) throw new Error("模板目录为空，请联系管理员检查部署");
      setTemplates(catalog.items);
      setTemplate((current) => (
        catalog.items.some((item) => item.id === current) ? current : catalog.items[0].id
      ));
    } catch (cause) {
      setTemplates([]);
      setTemplatesError(cause instanceof Error ? cause.message : "模板目录加载失败");
    } finally {
      setTemplatesLoading(false);
    }
  }, []);

  useEffect(() => { void loadTemplates(); }, [loadTemplates]);

  const create = async () => {
    const requestedName = (nameInput.current?.value ?? name).trim();
    const requestedDescription = (descriptionInput.current?.value ?? description).trim();
    if (!requestedName) {
      setError("请输入工作流名称");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const created = await workflowsApi.createCanvas({
        template,
        name: requestedName,
        description: requestedDescription,
      });
      router.push(created.designer_url || `/workflows/${encodeURIComponent(created.workflow.workflow_id)}/designer`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建工作流失败");
      setSubmitting(false);
    }
  };

  return (
    <main className="ct-canvas-entry">
      <header>
        <Link href="/workflows" aria-label="返回工作流库" title="返回工作流库"><ArrowLeft size={17} /></Link>
        <div><p>工作流</p><h1>新建工作流</h1></div>
      </header>
      <section className="ct-canvas-entry-dialog" data-testid="workflow-canvas-create-dialog" aria-label="创建工作流">
        <div className="ct-canvas-entry-title"><LayoutTemplate size={19} /><div><h2>从画布开始</h2><p>先创建，再在画布中连接输入、Agent 和交付件。</p></div></div>
        <label className="ct-v2-field"><span>工作流名称</span><input ref={nameInput} autoFocus defaultValue={name} onInput={(event) => setName(event.currentTarget.value)} /></label>
        <label className="ct-v2-field"><span>说明（可选）</span><textarea ref={descriptionInput} rows={3} defaultValue={description} onInput={(event) => setDescription(event.currentTarget.value)} /></label>
        <fieldset className="ct-canvas-entry-templates">
          <legend>起始模板</legend>
          {templatesLoading && <p className="ct-canvas-entry-template-state">正在加载模板...</p>}
          {templates.map((item) => (
            <label key={item.id}>
              <input data-testid={`workflow-template-${item.id}`} type="radio" name="template" checked={template === item.id} onChange={() => setTemplate(item.id)} />
              <span><strong>{item.label}{item.presentation.scope === "professional" && <em>专业</em>}</strong><small>{item.description}</small></span>
            </label>
          ))}
        </fieldset>
        {templatesError && <div className="ct-v2-notice is-error" role="alert"><span>{templatesError || "模板目录加载失败"}</span><button type="button" onClick={() => void loadTemplates()}>重试</button></div>}
        {error && <p className="ct-v2-form-error" role="alert">{error}</p>}
        <footer><button className="ct-v2-primary-button" type="button" onClick={() => void create()} disabled={submitting || templatesLoading || Boolean(templatesError)}>{submitting && <Loader2 size={15} className="animate-spin" />}{submitting ? "正在创建" : "创建并打开画布"}</button></footer>
      </section>
    </main>
  );
}
