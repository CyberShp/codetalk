export function normalizeGitNexusRepoPath(value) {
  return String(value || "")
    .replace(/\\/g, "/")
    .replace(/\/+$/, "")
    .toLowerCase();
}

export function sameGitNexusRepoPath(left, right) {
  const normalizedLeft = normalizeGitNexusRepoPath(left);
  return Boolean(
    normalizedLeft && normalizedLeft === normalizeGitNexusRepoPath(right),
  );
}
