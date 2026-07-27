export function workflowRevisionBody(expectedRevision?: number) {
  return JSON.stringify(
    expectedRevision === undefined ? {} : { expected_revision: expectedRevision },
  );
}
