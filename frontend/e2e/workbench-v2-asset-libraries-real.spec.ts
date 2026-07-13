import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { assertCanMutatePublicRuntime } from "../scripts/playwright-runtime-policy.mjs";

const backendBase = `http://localhost:${process.env.CODETALK_BACKEND_PORT ?? "3004"}`;

assertCanMutatePublicRuntime({ env: process.env, flowName: "Workbench V2 real asset libraries" });

test("imports, edits, deprecates, and restores a semantic case through the real UI", async ({ page }) => {
  const stamp = Date.now();
  const caseId = `TC_PHASE7_TLS_${stamp}`;
  const csv = [
    "case_id,feature,module,scenario,expected,test_level,interface,tags",
    `${caseId},NVMe TCP TLS,nvmf/tcp/tls,invalid certificate rejects connection,connection is rejected,black_box,NVMe/TCP,security;negative`,
    `,NVMe TCP TLS,nvmf/tcp/tls,missing identifier stays invalid,validation error is visible,black_box,NVMe/TCP,negative`,
  ].join("\n");

  await page.goto("/semantic-library", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "语义用例库" })).toBeVisible();
  await page.getByRole("button", { name: "导入用例" }).hover();
  await page.getByRole("button", { name: "导入用例" }).click();
  await page.locator('input[type="file"]').setInputFiles({ name: "phase7-cases.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await expect(page.getByRole("dialog", { name: "导入语义用例" }).getByText("phase7-cases.csv")).toBeVisible();
  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page.getByRole("heading", { name: "字段映射", exact: true })).toBeVisible();
  await page.getByLabel("默认 Feature").fill("NVMe TCP TLS");
  await page.getByLabel("默认 Module").fill("nvmf/tcp/tls");
  await page.getByRole("button", { name: "生成预览" }).click();
  await expect(page.getByRole("heading", { name: "预览与验证" })).toBeVisible();
  await expect(page.getByText("缺少 case_id", { exact: true })).toBeVisible();
  await expect(page.getByText("1", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "选择冲突策略" }).click();
  await page.getByText("创建副本", { exact: true }).click();
  await page.getByRole("button", { name: "确认导入" }).click();
  await expect(page.getByText("1 条已导入", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载失败记录" })).toBeVisible();
  const failureDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载失败记录" }).click();
  expect((await failureDownload).suggestedFilename()).toContain("failures.ndjson");
  await page.getByRole("button", { name: "完成" }).click();

  await page.getByLabel("搜索语义用例").fill(caseId);
  await expect(page.getByRole("button", { name: new RegExp(caseId) })).toBeVisible();
  await page.getByRole("button", { name: new RegExp(caseId) }).click();
  await expect(page.getByRole("complementary", { name: "语义用例详情" })).toBeVisible();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const scenario = "expired certificate rejects connection and records authentication failure";
  await page.getByLabel("场景").fill(scenario);
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect(page.getByRole("heading", { name: scenario })).toBeVisible();
  await page.getByRole("button", { name: "废弃" }).click();
  await expect(page.getByRole("button", { name: "恢复" })).toBeVisible();
  await page.getByRole("button", { name: "恢复" }).click();
  await expect(page.getByRole("button", { name: "废弃" })).toBeVisible();
  await page.getByRole("button", { name: "复制" }).click();
  await expect(page.getByText(/_COPY_\d+/, { exact: false }).first()).toBeVisible();

  const evidenceDir = path.join(process.cwd(), "output", "playwright", "phase7");
  fs.mkdirSync(evidenceDir, { recursive: true });
  await page.screenshot({ path: path.join(evidenceDir, "semantic-library-desktop.png"), fullPage: false });
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  const shell = await page.locator(".ct-asset-workspace").boundingBox();
  expect((shell?.y ?? 0) + (shell?.height ?? 0)).toBeLessThanOrEqual(900);
});

test("browses a real evidence item and source slice in bounded desktop and mobile views", async ({ page, request }) => {
  const stamp = Date.now();
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "codetalk-evidence-library-"));
  fs.mkdirSync(path.join(repo, "lib", "nvmf"), { recursive: true });
  fs.writeFileSync(path.join(repo, "lib", "nvmf", "tcp.c"), "line one\nstatic int tls_handshake(void)\nreturn 0;\n", "utf8");
  const create = await request.post(`${backendBase}/api/workbench/memory/evidence`, {
    data: {
      run_id: `run_phase7_${stamp}`,
      workspace_id: `ws_phase7_${stamp}`,
      kind: "source_file",
      subject_key: `tls_handshake_${stamp}`,
      status: "validated",
      source: "gitnexus",
      path: "lib/nvmf/tcp.c",
      symbol: "tls_handshake",
      reason: "TLS connection setup evidence",
      confidence: 0.96,
      text: `certificate handshake source evidence ${stamp}`,
      provenance: { repo_path: repo, line_start: 2, task_id: `task_phase7_${stamp}` },
    },
  });
  expect(create.ok()).toBeTruthy();

  await page.goto("/evidence-library", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "证据库" })).toBeVisible();
  await page.getByLabel("搜索证据").fill(String(stamp));
  await expect(page.getByRole("button", { name: new RegExp(`tls_handshake_${stamp}`) })).toBeVisible();
  await page.getByRole("button", { name: new RegExp(`tls_handshake_${stamp}`) }).hover();
  await page.getByRole("button", { name: new RegExp(`tls_handshake_${stamp}`) }).click();
  await expect(page.getByText("lib/nvmf/tcp.c:1-3", { exact: true })).toBeVisible();
  await expect(page.getByText("static int tls_handshake(void)", { exact: false })).toBeVisible();

  const evidenceDir = path.join(process.cwd(), "output", "playwright", "phase7");
  fs.mkdirSync(evidenceDir, { recursive: true });
  await page.screenshot({ path: path.join(evidenceDir, "evidence-library-desktop.png"), fullPage: false });
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: `tls_handshake_${stamp}` })).toBeVisible();
  const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(horizontalOverflow).toBeLessThanOrEqual(1);
  await page.screenshot({ path: path.join(evidenceDir, "evidence-library-mobile.png"), fullPage: false });
});
