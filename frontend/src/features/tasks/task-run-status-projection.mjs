function nonEmpty(value) {
  return value !== undefined && value !== null && String(value) !== "";
}

function firstNonEmpty(...values) {
  return values.find(nonEmpty);
}

function contractVersionOf(...records) {
  for (const record of records) {
    if (!record || typeof record !== "object") continue;
    const direct = record.compiled_contract_version;
    if (nonEmpty(direct)) return direct;
    const workflowSnapshot = record.workflow_snapshot;
    if (workflowSnapshot && typeof workflowSnapshot === "object" && nonEmpty(workflowSnapshot.compiled_contract_version)) {
      return workflowSnapshot.compiled_contract_version;
    }
    const taskBundle = record.task_bundle;
    if (taskBundle && typeof taskBundle === "object" && nonEmpty(taskBundle.compiled_contract_version)) {
      return taskBundle.compiled_contract_version;
    }
    const workflowVersion = record.workflow_version;
    const compiledDefinition = workflowVersion?.compiled_definition;
    if (compiledDefinition && typeof compiledDefinition === "object" && nonEmpty(compiledDefinition.compiled_contract_version)) {
      return compiledDefinition.compiled_contract_version;
    }
  }
  return undefined;
}

/**
 * @param {any} run
 */
export function hasV3RunAxisSummary(run) {
  return Boolean(
    run
      && (
        nonEmpty(run.artifact_validation_status)
        || nonEmpty(run.governance_status)
        || nonEmpty(run.compiled_contract_version)
      ),
  );
}

/**
 * @param {any} task
 * @param {any} run
 * @param {any} runDetail
 */
export function isV3TaskRun(task, run, runDetail = null) {
  return Number(contractVersionOf(run, task, runDetail)) === 3;
}

/**
 * @typedef {{ kind: "empty", execution: string, delivery: string }} EmptyProjection
 * @typedef {{ kind: "legacy", execution: string, quality: string, delivery: string }} LegacyProjection
 * @typedef {{ kind: "v3", execution: string, artifactValidation: string, governance: string, delivery: string }} V3Projection
 * @typedef {EmptyProjection | LegacyProjection | V3Projection} TaskRunOverviewProjection
 */

/**
 * @param {any} task
 * @param {any} run
 * @param {any} runDetail
 * @param {{ loadingDetail?: boolean }} options
 * @returns {TaskRunOverviewProjection}
 */
export function taskRunOverviewProjection(task, run, runDetail = null, options = {}) {
  if (!run) return { kind: "empty", execution: "not_started", delivery: "none" };
  const execution = String(firstNonEmpty(run.execution_status, runDetail?.execution_status, runDetail?.status, "not_started"));
  const delivery = String(firstNonEmpty(run.delivery_status, runDetail?.delivery_status, "none"));
  if (!isV3TaskRun(task, run, runDetail)) {
    return {
      kind: "legacy",
      execution,
      quality: String(firstNonEmpty(run.quality_status, runDetail?.quality_status, "not_checked")),
      delivery,
    };
  }
  const loadingDetail = Boolean(options.loadingDetail);
  return {
    kind: "v3",
    execution,
    artifactValidation: String(firstNonEmpty(run.artifact_validation_status, runDetail?.artifact_validation_status, loadingDetail ? "syncing" : "not_started")),
    governance: String(firstNonEmpty(run.governance_status, runDetail?.governance_status, loadingDetail ? "syncing" : "not_requested")),
    delivery: String(firstNonEmpty(run.delivery_status, runDetail?.delivery_status, loadingDetail ? "syncing" : "pending")),
  };
}
