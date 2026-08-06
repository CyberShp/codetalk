import { expect, test, type APIRequestContext } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

assertCanMutatePublicRuntime({ env: process.env, flowName: "Skill Center product loop real E2E" });
test.setTimeout(60_000);

test("shows startup CodeTalk presets and carries the selected version into Task Wizard", async ({ page, request }) => {
  const stamp = Date.now();
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-skill-center-wizard-")));
  fs.writeFileSync(path.join(repo, "README.md"), "# wizard repo\n", "utf8");
  execFileSync("git", ["init", "-q", repo]);
  const workspaceResponse = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: `Skill Wizard ${stamp}`, repo_path: repo },
  });
  expect(workspaceResponse.ok(), await workspaceResponse.text()).toBeTruthy();
  const workspace = await workspaceResponse.json();
  const versionsResponse = await request.get(`${backendBase}/api/skills/versions`);
  expect(versionsResponse.ok(), await versionsResponse.text()).toBeTruthy();
  const versions = (await versionsResponse.json()).items as Array<Record<string, string>>;
  const requiredSkillIds = [
    "skill.codetalks-custom",
    "skill.codetalks-issue-regression",
    "skill.codetalks-module-full-analysis",
    "skill.codetalks-root-cause",
    "skill.codetalks-special-risk",
  ];
  for (const skillId of requiredSkillIds) {
    expect(versions.some((item) => item.skill_id === skillId), `${skillId} preset missing`).toBeTruthy();
  }
  const moduleVersion = versions.find((item) => item.skill_id === "skill.codetalks-module-full-analysis");
  expect(moduleVersion).toBeTruthy();

  await page.goto("/skills", { waitUntil: "domcontentloaded" });
  for (const label of ["自定义讲解", "Issue 回归", "模块全量分析", "根因定位", "专项风险"]) {
    await expect(page.getByRole("button", { name: new RegExp(label) })).toBeVisible();
  }
  await page.getByLabel("搜索 Skill").fill("skill.codetalks-module-full-analysis");
  await page.getByRole("link", { name: "用此版本创建任务" }).click();
  await expect(page).toHaveURL(/\/tasks\/new.*skill_id=skill\.codetalks-module-full-analysis/);
  await expect(page).toHaveURL(new RegExp(`/tasks/new.*skill_version_id=${encodeURIComponent(moduleVersion!.version_id)}`));
  await expect(page.getByRole("radio", { name: new RegExp(escapeRegExp(moduleVersion!.version_id)) })).toBeChecked();
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page).toHaveURL(/\/tasks\/new\?.*step=2/);
  await expect(page).toHaveURL(/skill_id=skill\.codetalks-module-full-analysis/);
  await expect(page).toHaveURL(new RegExp(`skill_version_id=${encodeURIComponent(moduleVersion!.version_id)}`));
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/tasks\/new\?.*step=2/);
  await expect(page).toHaveURL(/skill_id=skill\.codetalks-module-full-analysis/);
  await expect(page).toHaveURL(new RegExp(`skill_version_id=${encodeURIComponent(moduleVersion!.version_id)}`));
  await page.getByRole("textbox", { name: "任务名称 *" }).fill(`Skill Wizard Task ${stamp}`);
  await page.getByLabel("工作空间 *").selectOption(workspace.id);
  await page.getByRole("button", { name: "保存并继续" }).click();
  await expect(page).toHaveURL(/\/tasks\/new\?.*step=3/);
  const taskId = new URL(page.url()).searchParams.get("task");
  expect(taskId).toBeTruthy();
  const taskResponse = await request.get(`${backendBase}/api/workbench/tasks/${taskId}`);
  expect(taskResponse.ok(), await taskResponse.text()).toBeTruthy();
  expect((await taskResponse.json()).skill_version_id).toBe(moduleVersion!.version_id);
});

test("uses Skill Lab UI to import, create, lightly modify, review, and publish", async ({ page }) => {
  const stamp = Date.now();
  const archive = createImportArchive(stamp);
  await page.goto("/skills", { waitUntil: "domcontentloaded" });

  await page.getByLabel("Skill 项目名").fill(`Skill Lab ${stamp}`);
  await page.getByLabel("Skill Pack ID").fill(`pack.skill-lab-${stamp}`);
  await page.getByLabel("CodeTalk 预设场景").selectOption("custom");
  await page.getByLabel("Draft Skill ID").fill(`skill.e2e-ui-skill-${stamp}`);
  await page.getByRole("button", { name: "从源创建草稿" }).click();
  await expect(page.getByText(/Draft .* 已创建/)).toBeVisible();
  await page.getByLabel("草稿文件路径").fill("references/tool-routing.md");
  await page.getByLabel("草稿文件内容").fill(`# tool routing\n\nLight UI edit ${stamp}.\n`);
  await page.getByRole("button", { name: "写入草稿文件" }).click();
  await expect(page.getByText(/修改 references\/tool-routing.md/)).toBeVisible();
  await page.getByRole("button", { name: "构建" }).click();
  await expect(page.getByRole("button", { name: "审查" })).toBeEnabled();
  await page.getByRole("button", { name: "审查" }).click();
  await expect(page.getByText(/Review approved/)).toHaveCount(1);
  await page.getByLabel("Skill ZIP 文件").setInputFiles(archive);
  await page.getByRole("button", { name: "导入" }).click();
  await expect(page.getByText(/导入 5 个 Draft/)).toBeVisible();
  await expect(page.getByRole("button", { name: "发布" })).toBeDisabled();
  await page.getByRole("button", { name: "构建" }).click();
  await expect(page.getByRole("button", { name: "审查" })).toBeEnabled();
  await page.getByRole("button", { name: "审查" }).click();
  await expect(page.getByText(/Review approved/)).toHaveCount(2);
  await page.getByRole("button", { name: "发布" }).click();
  await expect(page.getByText(/发布 skill_version_build_/)).toBeVisible();
});

test("covers light, medium, and heavy Skill changes with frozen task/runtime evidence", async ({ page, request }) => {
  const stamp = Date.now();
  const presets = await getPresets(request);
  const custom = presets.find((item) => item.scenario_id === "custom");
  const special = presets.find((item) => item.scenario_id === "special-risk");
  expect(custom).toBeTruthy();
  expect(special).toBeTruthy();
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-skill-depth-repo-")));
  fs.writeFileSync(path.join(repo, "README.md"), "# Skill depth repo\n", "utf8");
  execFileSync("git", ["init", "-q", repo]);

  const workspaceResponse = await request.post(`${backendBase}/api/workspaces`, {
    data: { name: `Skill Depth ${stamp}`, repo_path: repo },
  });
  expect(workspaceResponse.ok(), await workspaceResponse.text()).toBeTruthy();
  const workspace = await workspaceResponse.json();
  const projectResponse = await request.post(`${backendBase}/api/skills/projects`, {
    data: { name: `Skill Depth Project ${stamp}`, pack_id: `pack.depth-${stamp}` },
  });
  expect(projectResponse.ok(), await projectResponse.text()).toBeTruthy();
  const project = await projectResponse.json();

  const lightDraft = await createDraft(request, project.project_id, custom!, "skill.codetalks-custom");
  const baseVersion = await publishDraft(request, lightDraft.draft_id, `depth/${stamp}/base`);
  const taskResponse = await request.post(`${backendBase}/api/workbench/tasks`, {
    data: {
      name: `Frozen Skill Task ${stamp}`,
      workspace_id: workspace.id,
      skill_version_id: baseVersion.version_id,
      lifecycle_status: "ready",
      input_values: {},
    },
  });
  expect(taskResponse.ok(), await taskResponse.text()).toBeTruthy();
  const task = await taskResponse.json();

  await writeDraftFile(request, lightDraft.draft_id, "references/tool-routing.md", `# routing\n\nLight change ${stamp}\n`);
  const lightChanged = await publishDraft(request, lightDraft.draft_id, `depth/${stamp}/light`);
  expect(lightChanged.content_digest).not.toBe(baseVersion.content_digest);
  expect(lightChanged.version_id).not.toBe(baseVersion.version_id);

  const mediumDraft = await createDraft(request, project.project_id, custom!, "skill.codetalks-custom");
  const manifest = JSON.parse(fs.readFileSync(path.join(custom!.source_root, "workflow-manifest.json"), "utf8"));
  manifest.steps[0].required.push("活文档/20-修改深度验证.md");
  await writeDraftFile(request, mediumDraft.draft_id, "workflow-manifest.json", `${JSON.stringify(manifest, null, 2)}\n`);
  const mediumVersion = await publishDraft(request, mediumDraft.draft_id, `depth/${stamp}/medium`);
  const mediumIrResponse = await request.get(`${backendBase}/api/skills/versions/${mediumVersion.version_id}/ir`);
  expect(mediumIrResponse.ok(), await mediumIrResponse.text()).toBeTruthy();
  const mediumIr = await mediumIrResponse.json();
  expect(mediumIr.artifacts.some((item: Record<string, string>) => item.path === "活文档/20-修改深度验证.md")).toBeTruthy();

  const heavyDraft = await createDraft(request, project.project_id, special!, "skill.codetalks-special-risk");
  const heavyVersion = await publishDraft(request, heavyDraft.draft_id, `depth/${stamp}/heavy`);
  const heavyIrResponse = await request.get(`${backendBase}/api/skills/versions/${heavyVersion.version_id}/ir`);
  expect(heavyIrResponse.ok(), await heavyIrResponse.text()).toBeTruthy();
  expect((await heavyIrResponse.json()).selected_workflow_path).toBe("workflows/special-risk.md");

  const taskDetail = await request.get(`${backendBase}/api/workbench/tasks/${task.task_id}`);
  expect(taskDetail.ok(), await taskDetail.text()).toBeTruthy();
  expect((await taskDetail.json()).skill_version_id).toBe(baseVersion.version_id);
  const runResponse = await request.post(`${backendBase}/api/workbench/tasks/${task.task_id}/runs`, { data: {} });
  expect(runResponse.ok(), await runResponse.text()).toBeTruthy();
  const run = await runResponse.json();
  const runDetailResponse = await request.get(`${backendBase}/api/workbench/task-runs/${run.task_run_id}`);
  expect(runDetailResponse.ok(), await runDetailResponse.text()).toBeTruthy();
  const invocation = (await runDetailResponse.json()).task_bundle.skill_invocation;
  const producerRuntime = invocation.runtime.producer;
  expect(invocation.skill_version_id).toBe(baseVersion.version_id);
  expect(producerRuntime.requested_provider).toBe("opencode");
  expect(producerRuntime.effective_provider).toBe("opencode");
  expect(producerRuntime.requested_model).toBe("deepseek/deepseek-v4-flash");
  expect(producerRuntime.declared_context_window_tokens).toBe(200000);
  expect(producerRuntime.requested_max_output_tokens).toBe(4096);

  await page.goto(`/tasks/${task.task_id}/runs/${run.task_run_id}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByLabel("Skill invocation").getByText("Skill 运行契约")).toBeVisible();
  await expect(page.getByLabel("Skill invocation").getByText(baseVersion.version_id, { exact: true })).toBeVisible();
});

async function getPresets(request: APIRequestContext) {
  const response = await request.get(`${backendBase}/api/skills/presets`);
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()).items as Array<{ scenario_id: string; skill_id: string; source_root: string }>;
}

async function createDraft(
  request: APIRequestContext,
  projectId: string,
  preset: { scenario_id: string; source_root: string },
  skillId: string,
) {
  const response = await request.post(`${backendBase}/api/skills/projects/${projectId}/drafts/from-source`, {
    data: { source_root: preset.source_root, source_scenario_id: preset.scenario_id, skill_id: skillId },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function writeDraftFile(
  request: APIRequestContext,
  draftId: string,
  relativePath: string,
  content: string,
) {
  const response = await request.post(`${backendBase}/api/skills/drafts/${draftId}/files`, {
    data: { relative_path: relativePath, content },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function publishDraft(request: APIRequestContext, draftId: string, sessionId: string) {
  const buildResponse = await request.post(`${backendBase}/api/skills/drafts/${draftId}/builds`, { data: {} });
  expect(buildResponse.ok(), await buildResponse.text()).toBeTruthy();
  const build = await buildResponse.json();
  const reviewResponse = await request.post(`${backendBase}/api/skills/builds/${build.build_id}/reviews/run`, {
    data: {
      scope: "full",
      purpose: "Skill modification-depth E2E review",
      session_id: sessionId,
      provider: "deepseek",
      requested_model: "deepseek-v4-flash",
      effective_model: "deepseek-v4-flash",
      response_model: "deepseek-v4-flash",
      declared_context_window_tokens: 200000,
      requested_max_output_tokens: 4096,
    },
  });
  expect(reviewResponse.ok(), await reviewResponse.text()).toBeTruthy();
  const review = await reviewResponse.json();
  expect(review.review_evidence.provider).toBe("deepseek");
  expect(review.review_evidence.declared_context_window_tokens).toBe(200000);
  const publishResponse = await request.post(`${backendBase}/api/skills/builds/${build.build_id}/publish`, { data: {} });
  expect(publishResponse.ok(), await publishResponse.text()).toBeTruthy();
  return publishResponse.json();
}

function createImportArchive(stamp: number) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-skill-import-")));
  const packageRoot = path.join(root, "official-pack");
  writeV24SkillSource(packageRoot, stamp);
  const archive = path.join(root, "pack.zip");
  execFileSync("zip", ["-qr", archive, "official-pack"], { cwd: root });
  return archive;
}

function writeV24SkillSource(root: string, stamp: number) {
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
    steps: requiredByStep.map((required, index) => {
      const id = String(index + 1).padStart(2, "0");
      return {
        id,
        file: `steps/${id}-step.md`,
        required,
        markdown_min_chars: 601 + index,
        ...(index === 3 ? { requires_glob: ["活文档/流程讲解/流程-*.md"], flow_narrative_validation: true } : {}),
      };
    }),
  };
  writeFile(root, "workflow-manifest.json", `${JSON.stringify(manifest, null, 2)}\n`);
  for (const scenario of ["custom", "issue-regression", "module-analysis", "root-cause", "special-risk"]) {
    writeFile(root, `workflows/${scenario}.md`, `# imported ${scenario} ${stamp}\n`);
  }
  for (const file of [
    "SKILL.md",
    "scripts/run_guard.py",
    "checklists/judge-checklist.md",
    "references/tool-routing.md",
    "templates/开发给测试讲代码模板.md",
    ...Object.values(manifest.required_core_rules),
    ...manifest.steps.map((step) => step.file),
  ]) {
    writeFile(root, file, `# ${file}\n`);
  }
}

function writeFile(root: string, relativePath: string, content: string) {
  const target = path.join(root, relativePath);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, content, "utf8");
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
