export type TrialUploadResult = {
  upload_id: string;
  cleanup_token: string;
  input_payload: { path: string };
};

export async function prepareTrialRunWithUploads<T>({
  values,
  files,
  beforeRun,
  uploadInput,
  prepareRun,
  releaseUpload,
  workflowId,
  workflowVersionId,
}: {
  values: Record<string, string>;
  files: Array<{ inputId: string; file: File }>;
  beforeRun?: () => Promise<number | void | undefined>;
  uploadInput: (
    file: File,
    inputId: string,
    lease?: { workflowId: string; workflowVersionId: string; expectedRevision: number },
  ) => Promise<TrialUploadResult>;
  prepareRun: (inputs: Record<string, unknown>, expectedRevision: number | undefined) => Promise<T>;
  releaseUpload: (uploadId: string, cleanupToken: string) => Promise<unknown>;
  workflowId: string;
  workflowVersionId: string;
}): Promise<T> {
  const expectedRevision = await beforeRun?.();
  const uploadedInputs: TrialUploadResult[] = [];
  const payload: Record<string, unknown> = { ...values };
  try {
    for (const item of files) {
      const uploaded = await uploadInput(
        item.file,
        item.inputId,
        expectedRevision === undefined
          ? undefined
          : { workflowId, workflowVersionId, expectedRevision },
      );
      uploadedInputs.push(uploaded);
      payload[item.inputId] = uploaded.input_payload;
    }
    return await prepareRun(payload, expectedRevision as number | undefined);
  } catch (cause) {
    if (isConfirmedStaleDraft(cause)) {
      await Promise.allSettled(
        uploadedInputs.map((uploaded) =>
          releaseUpload(uploaded.upload_id, uploaded.cleanup_token),
        ),
      );
    }
    throw cause;
  }
}

function isConfirmedStaleDraft(cause: unknown): boolean {
  if (!(cause instanceof Error)) return false;
  const error = cause as Error & { status?: unknown; errorCode?: unknown };
  return error.status === 409 && error.errorCode === "stale_draft";
}
