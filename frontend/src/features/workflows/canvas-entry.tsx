"use client";

import { ArrowLeft, LayoutTemplate, Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { workflowsApi } from "@/lib/api/workflows";

type Template = "blank" | "free_source_analysis";

export function CanvasEntry() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [template, setTemplate] = useState<Template>("free_source_analysis");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const nameInput = useRef<HTMLInputElement>(null);
  const descriptionInput = useRef<HTMLTextAreaElement>(null);

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
          <label><input data-testid="workflow-template-free_source_analysis" type="radio" name="template" checked={template === "free_source_analysis"} onChange={() => setTemplate("free_source_analysis")} /><span><strong>自由源码分析</strong><small>源码工作区 → Agent → report.md</small></span></label>
          <label><input data-testid="workflow-template-blank" type="radio" name="template" checked={template === "blank"} onChange={() => setTemplate("blank")} /><span><strong>空白画布</strong><small>从节点库按需要搭建</small></span></label>
        </fieldset>
        {error && <p className="ct-v2-form-error" role="alert">{error}</p>}
        <footer><button className="ct-v2-primary-button" type="button" onClick={() => void create()} disabled={submitting}>{submitting && <Loader2 size={15} className="animate-spin" />}{submitting ? "正在创建" : "创建并打开画布"}</button></footer>
      </section>
    </main>
  );
}
