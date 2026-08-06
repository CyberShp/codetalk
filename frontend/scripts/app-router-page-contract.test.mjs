import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

const allowedNamedExports = new Set([
  "config",
  "dynamic",
  "dynamicParams",
  "experimental_ppr",
  "fetchCache",
  "generateImageMetadata",
  "generateMetadata",
  "generateSitemaps",
  "generateStaticParams",
  "maxDuration",
  "metadata",
  "preferredRegion",
  "revalidate",
  "runtime",
  "viewport",
]);

function namedFunctionExports(source) {
  return Array.from(source.matchAll(/export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(/g)).map(
    (match) => match[1],
  );
}

test("workbench page module only exports App Router page-safe fields", () => {
  const pageSource = readFileSync(new URL("../src/app/workbench/page.tsx", import.meta.url), "utf8");
  const illegalExports = namedFunctionExports(pageSource).filter((name) => !allowedNamedExports.has(name));

  assert.deepEqual(illegalExports, []);
});

test("workbench no longer carries deleted Workflow sibling product routes", () => {
  assert.equal(existsSync(new URL("../src/app/workbench/designer/page.tsx", import.meta.url)), false);
  assert.equal(existsSync(new URL("../src/app/workbench/semantic/page.tsx", import.meta.url)), false);
});

test("workspace creation keeps the optional local folder browser wired", () => {
  const pageSource = readFileSync(
    new URL("../src/app/workspaces/new/page.tsx", import.meta.url),
    "utf8",
  );
  const apiSource = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");

  assert.match(pageSource, /本地文件夹路径[\s\S]{0,80}可选/);
  assert.match(pageSource, /浏览/);
  assert.match(pageSource, /api\.workspaces\.browseFolders/);
  assert.match(pageSource, /repo_path:\s*submittedRepoPath/);
  assert.match(apiSource, /browseFolders/);
  assert.match(apiSource, /\/api\/workspaces\/folders/);
});

test("workspace creation treats the local folder as optional", () => {
  const pageSource = readFileSync(
    new URL("../src/app/workspaces/new/page.tsx", import.meta.url),
    "utf8",
  );

  assert.match(pageSource, /本地文件夹路径[\s\S]{0,80}可选/);
  assert.doesNotMatch(pageSource, /请输入代码仓库路径/);
  assert.match(pageSource, /repo_path:\s*submittedRepoPath/);
});

test("workspace folder picker uses backend-provided roots instead of a hardcoded POSIX root", () => {
  const pageSource = readFileSync(
    new URL("../src/app/workspaces/new/page.tsx", import.meta.url),
    "utf8",
  );
  const typesSource = readFileSync(
    new URL("../src/lib/types.ts", import.meta.url),
    "utf8",
  );

  assert.match(typesSource, /roots:\s*WorkspaceFolderRoot\[\]/);
  assert.match(pageSource, /folderData\?\.roots/);
  assert.doesNotMatch(pageSource, /loadFolders\(["']\/["']\)/);
  assert.doesNotMatch(pageSource, /\/Volumes\/Media\/project/);
  assert.doesNotMatch(pageSource, /\/home\/user\/project/);
});

test("settings and workspace path hints avoid OS-specific absolute path literals", () => {
  const workspacePageSource = readFileSync(
    new URL("../src/app/workspaces/new/page.tsx", import.meta.url),
    "utf8",
  );
  const settingsPageSource = readFileSync(
    new URL("../src/app/settings/page.tsx", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(workspacePageSource, /\/Volumes\/|\/Volums\/|\/home\/user/);
  assert.doesNotMatch(settingsPageSource, /C:\/innernet|C:\\\\innernet|\/Volumes\/|\/home\/user/);
});
test("coverage analysis is not exposed as a standalone app route", () => {
  const coveragePage = new URL("../src/app/coverage/page.tsx", import.meta.url);
  const sidebarSource = readFileSync(
    new URL("../src/components/layout/Sidebar.tsx", import.meta.url),
    "utf8",
  );

  assert.equal(existsSync(coveragePage), false);
  assert.doesNotMatch(sidebarSource, /href:\s*["']\/coverage["']/);
  assert.doesNotMatch(sidebarSource, /覆盖率分析/);
});
