import test from "node:test";
import assert from "node:assert/strict";

import { sameGitNexusRepoPath } from "../src/lib/gitnexus-capacity.mjs";

test("GitNexus capacity matching distinguishes repositories with the same leaf name", () => {
  assert.equal(
    sameGitNexusRepoPath("/srv/team-a/spdk", "/srv/team-b/spdk"),
    false,
  );
  assert.equal(
    sameGitNexusRepoPath("C:\\src\\spdk\\", "C:/src/spdk"),
    true,
  );
});
