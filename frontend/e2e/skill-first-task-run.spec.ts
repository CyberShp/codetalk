import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

assertCanMutatePublicRuntime({ env: process.env, flowName: "Skill-first task run E2E" });

test("creates a Task from a published Skill Version and opens a frozen run cockpit", async ({ page, request }) => {
  const stamp = Date.now();
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-skill-first-repo-")));
  const source = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-skill-source-")));
  fs.writeFileSync(path.join(repo, "README.md"), "# Skill-first run source\n", "utf8");
  execFileSync("git", ["init", "-q", repo]);
  writeV24SkillSource(source);

  const workspaceResponse = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: `Skill First Repo ${stamp}`, repo_path: repo },
  });
  expect(workspaceResponse.ok()).toBeTruthy();
  const workspace = await workspaceResponse.json();

  const projectResponse = await request.post(`${backendBase}/api/skills/projects`, {
    data: { name: `Skill First Project ${stamp}`, pack_id: "pack.codetalks" },
  });
  expect(projectResponse.ok()).toBeTruthy();
  const project = await projectResponse.json();

  const draftResponse = await request.post(`${backendBase}/api/skills/projects/${project.project_id}/drafts/from-source`, {
    data: {
      source_root: source,
      source_scenario_id: "module-analysis",
      skill_id: `skill.e2e-module-analysis-${stamp}`,
    },
  });
  expect(draftResponse.ok()).toBeTruthy();
  const draft = await draftResponse.json();

  const buildResponse = await request.post(`${backendBase}/api/skills/drafts/${draft.draft_id}/builds`, { data: {} });
  expect(buildResponse.ok()).toBeTruthy();
  const build = await buildResponse.json();
  const reviewResponse = await request.post(`${backendBase}/api/skills/builds/${build.build_id}/reviews/run`, {
    data: { scope: "full", session_id: `skill-first-e2e/${stamp}` },
  });
  expect(reviewResponse.ok()).toBeTruthy();
  const publishResponse = await request.post(`${backendBase}/api/skills/builds/${build.build_id}/publish`, { data: {} });
  expect(publishResponse.ok()).toBeTruthy();
  const version = await publishResponse.json();

  await page.goto(`/tasks/new?skill_version_id=${encodeURIComponent(version.version_id)}`, { waitUntil: "domcontentloaded" });
  await page.getByRole("radio", { name: new RegExp(version.version_id) }).check();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByRole("textbox", { name: "任务名称 *" }).fill(`Skill-first Task ${stamp}`);
  await page.getByLabel("工作空间 *").selectOption(workspace.id);
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByText("该 Skill 只需要所选工作空间")).toBeVisible();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByText("Skill-first 运行会冻结此步骤").first()).toBeVisible();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByText("developer-test-code-explanation")).toBeVisible();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page.getByText("Skill 步骤", { exact: true }).locator("..")).toContainText("9");
  await expect(page.getByText("Delivery", { exact: true }).locator("..")).toContainText("8");
  await page.getByRole("button", { name: "保存为就绪任务" }).click();
  await expect(page.getByText("就绪", { exact: true })).toBeVisible();

  const taskId = page.url().split("/").pop() as string;
  const runResponse = await request.post(`${backendBase}/api/workbench/tasks/${taskId}/runs`, { data: {} });
  expect(runResponse.ok()).toBeTruthy();
  const run = await runResponse.json();
  const executeResponse = await request.post(`${backendBase}/api/workbench/task-runs/${run.task_run_id}/execute`, {
    data: { timeout_sec: 0, stop_on_error: true },
  });
  expect(executeResponse.status()).toBe(202);
  await expect.poll(async () => {
    const detailResponse = await request.get(`${backendBase}/api/workbench/task-runs/${run.task_run_id}`);
    expect(detailResponse.ok()).toBeTruthy();
    const detail = await detailResponse.json();
    return detail.execution_status || detail.runtime?.status || detail.status;
  }, { timeout: 30_000 }).toMatch(/^(completed|partial|quality_blocked)$/);
  await page.goto(`/tasks/${taskId}/runs/${run.task_run_id}`, { waitUntil: "domcontentloaded" });
  const skillInvocation = page.getByLabel("Skill invocation");
  await expect(skillInvocation.getByText("Skill 运行契约")).toBeVisible();
  await expect(skillInvocation.getByText(version.version_id, { exact: true })).toBeVisible();
});

function writeV24SkillSource(root: string) {
  const requiredByStep = [
    ["活文档/01-范围与任务契约.md"],
    ["活文档/02-输入材料消费记录.md", "内部索引/运行计划.json", "内部索引/输入材料索引.json", "活文档/覆盖门禁/步骤02-覆盖门禁.md"],
    ["活文档/03-入口清单与说明.md", "活文档/04-流程清单与说明.md", "活文档/05-状态清单与说明.md", "活文档/06-资源清单与说明.md", "活文档/07-分析模型适用性.md", "活文档/覆盖门禁/步骤03-覆盖门禁.md"],
    ["活文档/08-分支处置与解释.md", "活文档/09-状态转换处置与解释.md", "活文档/10-资源生命周期处置与解释.md", "活文档/11-异常传播链与解释.md", "活文档/12-开发讲解覆盖台账.md", "活文档/覆盖门禁/步骤04-覆盖门禁.md"],
    ["活文档/13-场景候选池与推导说明.md", "活文档/14-风险点清单与因果说明.md", "活文档/覆盖门禁/步骤05-覆盖门禁.md"],
    ["活文档/15-SFMEA分析.md", "活文档/16-黑盒控制与观测映射.md", "活文档/17-测试设计依据.md", "活文档/覆盖门禁/步骤06-覆盖门禁.md"],
    ["活文档/18-测试追溯矩阵.md", "活文档/覆盖门禁/步骤07-覆盖门禁.md"],
    ["活文档/19-独立审查报告.md", "活文档/覆盖门禁/最终覆盖门禁.md", "内部索引/独立审查状态.json"],
    ["正式输出/开发给测试讲代码.md", "正式输出/流程分支状态资源与异常传播.md", "正式输出/风险点与SFMEA.md", "正式输出/黑盒测试场景.md", "正式输出/黑盒测试流程.md", "正式输出/黑盒测试用例.md", "正式输出/覆盖审计与分析限制.md", "正式输出/完整分析报告.md"],
  ];
  const manifest = {
    version: "2.4",
    required_core_rules: {
      "path-fidelity": "references/path-fidelity.md",
      "evidence-consumption": "references/evidence-consumption.md",
      "narrative-first": "references/markdown-narrative-first.md",
    },
    evidence_allowed_status: ["parsed", "partially_parsed", "blocked", "out_of_scope", "unreadable"],
    coverage_allowed_outcomes: ["analyzed", "covered_by_other", "not_applicable", "blocked", "need_verify", "truncated"],
    flow_required_headings: ["## 一、这里是干什么的", "## 二、外部怎么触发"],
    flow_key_narrative_headings: ["## 一、这里是干什么的"],
    steps: requiredByStep.map((required, index) => ({
      id: String(index + 1).padStart(2, "0"),
      file: `steps/${String(index + 1).padStart(2, "0")}-step.md`,
      required,
      markdown_min_chars: 601 + index,
      ...(index === 3 ? { requires_glob: ["活文档/流程讲解/流程-*.md"], flow_narrative_validation: true } : {}),
    })),
  };
  writeFile(root, "workflow-manifest.json", `${JSON.stringify(manifest, null, 2)}\n`);
  for (const scenario of ["custom", "issue-regression", "module-analysis", "root-cause", "special-risk"]) {
    writeFile(root, `workflows/${scenario}.md`, `# ${scenario}\n`);
  }
  const files = [
    "SKILL.md",
    "scripts/run_guard.py",
    "checklists/judge-checklist.md",
    "references/tool-routing.md",
    "templates/开发给测试讲代码模板.md",
    ...Object.values(manifest.required_core_rules),
    ...manifest.steps.map((step) => step.file),
  ];
  for (const file of files) writeFile(root, file, `# ${file}\n`);
}

function writeFile(root: string, relativePath: string, content: string) {
  const target = path.join(root, relativePath);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, content, "utf8");
}
