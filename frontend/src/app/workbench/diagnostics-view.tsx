"use client";

import type { WorkbenchController } from "./workbench-controller";


export function DiagnosticsWorkbenchView({ scope }: { scope: WorkbenchController }) {
  const { AlertTriangle, Loader2, Panel, PlayCircle, ProviderFactRow, ProviderSectionTitle, busyAction, commandResolutionLines, deploymentProbeResult, providerMatrix, providerProbeResults, providerStatusDisplayLabel, providerTaskProbeResults, runAllAgentProviderStartupProbes, runAllAgentProviderTaskProbes, runProviderStartupProbe, runProviderTaskProbe, runSmokeE2E, smokeE2EResult } = scope;
  return (<Panel title="执行器矩阵" icon={<AlertTriangle size={16} />}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-on-surface-variant">
                这里检查本机后端能否调用外部智能体 CLI，以及这些执行器是否具备
                MCP 凭证、产物导出和任务探测能力。
              </p>
              <button
                onClick={() => runAllAgentProviderStartupProbes()}
                disabled={
                  busyAction === "provider-probe-all-agents" ||
                  !(providerMatrix?.providers ?? []).some(
                    (provider) =>
                      provider.agent_owned &&
                      provider.diagnostics?.startup_probe_endpoint,
                  )
                }
                className="inline-flex items-center gap-2 rounded-lg bg-surface-container px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
              >
                {busyAction === "provider-probe-all-agents" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <PlayCircle size={13} />
                )}
                探测全部 Agent
              </button>
              <button
                onClick={() => runAllAgentProviderTaskProbes()}
                disabled={
                  busyAction === "provider-task-probe-all-agents" ||
                  !(providerMatrix?.providers ?? []).some(
                    (provider) =>
                      provider.agent_owned && provider.command.length > 0,
                  )
                }
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {busyAction === "provider-task-probe-all-agents" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <PlayCircle size={13} />
                )}
                任务探测
              </button>
              <button
                onClick={runSmokeE2E}
                disabled={busyAction === "smoke-e2e"}
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {busyAction === "smoke-e2e" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <PlayCircle size={13} />
                )}
                全链路烟测
              </button>
            </div>
            {smokeE2EResult && (
              <div className="mb-3 rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-xs text-on-surface-variant">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-on-surface">
                    全链路烟测
                  </span>
                  <span
                    className={
                      smokeE2EResult.status === "ready"
                        ? "font-data text-green-500"
                        : "font-data text-warning"
                    }
                  >
                    {smokeE2EResult.status}
                  </span>
                  <span className="font-data">
                    task:{smokeE2EResult.task_run_id}
                  </span>
                  <span className="font-data">
                    execution:{smokeE2EResult.execution.status}
                  </span>
                  <span className="font-data">
                    missing:
                    {smokeE2EResult.acceptance_audit.summary.missing_required}
                  </span>
                </div>
                <p className="mt-1 break-words font-data text-[10px]">
                  artifact:{smokeE2EResult.artifact.path}
                </p>
              </div>
            )}
            {deploymentProbeResult && (
              <div className="mb-3 rounded-lg border border-outline-variant/30 bg-surface-container px-3 py-2 text-xs text-on-surface-variant">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-on-surface">部署探测</span>
                  <span
                    className={
                      deploymentProbeResult.status === "healthy"
                        ? "font-data text-green-500"
                        : "font-data text-warning"
                    }
                  >
                    {deploymentProbeResult.status}
                  </span>
                  <span className="font-data">
                    healthy:{deploymentProbeResult.summary.healthy_count}/
                    {deploymentProbeResult.summary.provider_count}
                  </span>
                  <span className="font-data">
                    failed:{deploymentProbeResult.summary.failed_count}
                  </span>
                  {deploymentProbeResult.summary.task_contract_probe && (
                    <span className="font-data">
                      task-ready:
                      {deploymentProbeResult.summary.task_ready_count ?? 0}/
                      {deploymentProbeResult.summary.provider_count}
                    </span>
                  )}
                  {typeof deploymentProbeResult.evidence_count === "number" && (
                    <span className="font-data">
                      evidence:{deploymentProbeResult.evidence_count}
                    </span>
                  )}
                  <span className="font-data">
                    probe:{deploymentProbeResult.probe_id}
                  </span>
                </div>
                <p className="mt-1 break-words font-data text-[10px]">
                  artifact:
                  {deploymentProbeResult.artifact.latest_path ||
                    deploymentProbeResult.artifact.path}
                </p>
                {deploymentProbeResult.evidence_ids?.length ? (
                  <p className="mt-1 break-words font-data text-[10px]">
                    evidence_ids:{deploymentProbeResult.evidence_ids.join(", ")}
                  </p>
                ) : null}
              </div>
            )}
            <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr))]">
              {(providerMatrix?.providers ?? []).map((provider) => (
                <div
                  key={provider.provider}
                  className="ct-provider-card min-w-0 rounded-xl border border-outline-variant/30 bg-surface/80 p-4 text-xs"
                >
                  <div className="ct-provider-card-header flex items-start justify-between gap-3">
                    <div className="min-w-0 space-y-1">
                      <p className="ct-provider-name truncate text-sm font-semibold text-on-surface">
                        {provider.display_name || provider.provider}
                      </p>
                      <p className="ct-provider-slug font-data text-[11px] text-on-surface-variant">
                        {provider.provider}
                      </p>
                    </div>
                    <span className="ct-provider-status-badge shrink-0 rounded bg-surface-container px-2 py-0.5 font-data text-[10px] text-on-surface-variant">
                      {providerStatusDisplayLabel(provider.status)}
                    </span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {provider.codetalk_callable && (
                      <span className="ct-provider-pill ct-provider-pill--green rounded bg-green-400/10 px-2 py-0.5 text-[11px] font-medium text-green-500">
                        CodeTalk 可直接调用
                      </span>
                    )}
                    {provider.agent_owned && (
                      <span className="ct-provider-pill ct-provider-pill--dark rounded bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                        Agent 持有凭证
                      </span>
                    )}
                    {!provider.codetalk_callable && !provider.agent_owned && (
                      <span className="ct-provider-pill ct-provider-pill--amber rounded bg-amber-400/10 px-2 py-0.5 text-[11px] font-medium text-amber-500">
                        委托或不可用
                      </span>
                    )}
                  </div>
                  <div className="ct-provider-facts mt-3">
                    <ProviderFactRow
                      label="归属"
                      value={
                        <span className="font-data">{provider.owner}</span>
                      }
                    />
                    <ProviderFactRow
                      label="命令"
                      value={
                        <span className="font-data">
                          {provider.command.length > 0
                            ? provider.command.join(" ")
                            : "n/a"}
                        </span>
                      }
                    />
                    <ProviderFactRow
                      label="MCP"
                      value={
                        <span className="font-data">
                          {provider.capabilities.supports_mcp
                            ? provider.capabilities.mcp_profiles.length > 0
                              ? provider.capabilities.mcp_profiles.join(", ")
                              : "yes"
                            : "no"}
                        </span>
                      }
                    />
                    <ProviderFactRow
                      label="产物"
                      value={
                        <span className="font-data">
                          {provider.capabilities.supports_artifact_export
                            ? "artifact"
                            : "no-artifact"}
                        </span>
                      }
                    />
                    <ProviderFactRow
                      label="JSON"
                      value={
                        <span className="font-data">
                          {provider.capabilities.supports_json_output
                            ? "json"
                            : "no-json"}
                        </span>
                      }
                    />
                    {provider.env_hint_keys?.length ? (
                      <ProviderFactRow
                        label="环境变量"
                        value={
                          <span className="font-data">
                            {provider.env_hint_keys.join(", ")}
                          </span>
                        }
                      />
                    ) : null}
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {provider.capabilities.supports_source_discovery && (
                      <span className="ct-provider-feature rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                        源码发现
                      </span>
                    )}
                    {provider.capabilities.supports_call_graph && (
                      <span className="ct-provider-feature rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                        调用图
                      </span>
                    )}
                    {provider.capabilities.supports_source_slices && (
                      <span className="ct-provider-feature rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                        源码切片
                      </span>
                    )}
                    {provider.capabilities.supports_black_box_terms && (
                      <span className="ct-provider-feature rounded bg-surface-container px-2 py-0.5 text-[11px] text-on-surface">
                        黑盒术语
                      </span>
                    )}
                  </div>
                  {provider.credential_boundary && (
                    <p className="ct-provider-note mt-3 text-xs leading-5 text-on-surface-variant">
                      {provider.credential_boundary}
                    </p>
                  )}
                  {provider.diagnostics && (
                    <div className="ct-provider-diagnostics mt-3 space-y-2 border-t border-outline-variant/30 pt-3 text-on-surface-variant">
                      <ProviderSectionTitle>启动探测</ProviderSectionTitle>
                      {provider.diagnostics.startup_probe_endpoint && (
                        <ProviderFactRow
                          label="Probe"
                          value={
                            <span className="font-data">
                              {provider.diagnostics.startup_probe_endpoint}
                            </span>
                          }
                        />
                      )}
                      {provider.diagnostics.startup_probe_transport && (
                        <ProviderFactRow
                          label="传输"
                          value={
                            <span className="font-data">
                              {provider.diagnostics.startup_probe_transport}
                            </span>
                          }
                        />
                      )}
                      {provider.diagnostics.command_resolution && (
                        <div className="ct-provider-diag-box rounded bg-surface-container px-2 py-1.5">
                          <p className="ct-provider-diag-head">
                            <span>解析</span>
                            <span className="font-data">
                              {provider.diagnostics.command_resolution.status ||
                                "unknown"}
                            </span>
                            {provider.diagnostics.command_resolution
                              .used_fallback && (
                              <span className="ct-provider-mini-badge font-medium text-warning">
                                fallback
                              </span>
                            )}
                            {provider.diagnostics.command_resolution
                              .launch_kind && (
                              <span className="ct-provider-mini-badge font-data text-on-surface">
                                launch:
                                {
                                  provider.diagnostics.command_resolution
                                    .launch_kind
                                }
                              </span>
                            )}
                          </p>
                          {provider.diagnostics.command_resolution.reason && (
                            <p className="mt-1 break-words">
                              原因:{" "}
                              {provider.diagnostics.command_resolution.reason}
                            </p>
                          )}
                          {typeof provider.diagnostics.command_resolution
                            .attempt_count === "number" && (
                            <p className="mt-1">
                              尝试次数:{" "}
                              <span className="font-data text-on-surface">
                                {
                                  provider.diagnostics.command_resolution
                                    .attempt_count
                                }
                              </span>
                            </p>
                          )}
                          {(() => {
                            const attempts =
                              provider.diagnostics.command_resolution
                                ?.attempts ?? [];
                            const lastAttempt = attempts[attempts.length - 1];
                            const resolutionLines = commandResolutionLines(
                              lastAttempt?.resolution,
                            );
                            if (resolutionLines.length === 0) return null;
                            return (
                              <div className="mt-2 space-y-1">
                                {resolutionLines.map((line) => (
                                  <p
                                    key={line}
                                    className="break-words font-data text-[11px] text-on-surface"
                                  >
                                    {line}
                                  </p>
                                ))}
                              </div>
                            );
                          })()}
                        </div>
                      )}
                      {provider.diagnostics.probe_recipe && (
                        <div className="rounded bg-surface-container px-2 py-1.5">
                          <p className="font-medium text-on-surface">
                            探测配方
                          </p>
                          {provider.diagnostics.probe_recipe
                            .startup_probe_http && (
                            <p className="mt-1 break-words">
                              HTTP:{" "}
                              <span className="font-data text-on-surface">
                                {
                                  provider.diagnostics.probe_recipe
                                    .startup_probe_http
                                }
                              </span>
                            </p>
                          )}
                          {provider.diagnostics.probe_recipe
                            .backend_command && (
                            <p className="mt-1 break-words">
                              后端命令:{" "}
                              <span className="font-data text-on-surface">
                                {
                                  provider.diagnostics.probe_recipe
                                    .backend_command
                                }
                              </span>
                            </p>
                          )}
                          {provider.diagnostics.probe_recipe.command_env && (
                            <p className="mt-1 break-words">
                              覆盖环境变量:{" "}
                              <span className="font-data text-on-surface">
                                {provider.diagnostics.probe_recipe.command_env}
                              </span>
                            </p>
                          )}
                          {provider.diagnostics.probe_recipe.environment_checks
                            ?.length ? (
                            <p className="mt-1 break-words">
                              检查:{" "}
                              <span className="font-data text-on-surface">
                                {provider.diagnostics.probe_recipe.environment_checks.join(
                                  ", ",
                                )}
                              </span>
                            </p>
                          ) : null}
                        </div>
                      )}
                      {provider.diagnostics.manual_probe_command && (
                        <p className="break-words">
                          手工:{" "}
                          <span className="font-data text-on-surface">
                            {provider.diagnostics.manual_probe_command}
                          </span>
                        </p>
                      )}
                      {provider.diagnostics.troubleshooting?.[0] && (
                        <p className="leading-5">
                          {provider.diagnostics.troubleshooting[0]}
                        </p>
                      )}
                      {provider.diagnostics.startup_probe_endpoint && (
                        <div className="mt-2 flex flex-wrap gap-2">
                          <button
                            onClick={() =>
                              runProviderStartupProbe(provider.provider)
                            }
                            disabled={
                              busyAction ===
                              `provider-probe-${provider.provider}`
                            }
                            className="inline-flex items-center gap-2 rounded-lg bg-surface-container px-2.5 py-1.5 text-xs font-medium text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-50"
                          >
                            {busyAction ===
                            `provider-probe-${provider.provider}` ? (
                              <Loader2 size={13} className="animate-spin" />
                            ) : (
                              <PlayCircle size={13} />
                            )}
                            启动探测
                          </button>
                          {provider.agent_owned &&
                            provider.command.length > 0 && (
                              <button
                                onClick={() =>
                                  runProviderTaskProbe(provider.provider)
                                }
                                disabled={
                                  busyAction ===
                                  `provider-task-probe-${provider.provider}`
                                }
                                className="inline-flex items-center gap-2 rounded-lg bg-primary px-2.5 py-1.5 text-xs font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
                              >
                                {busyAction ===
                                `provider-task-probe-${provider.provider}` ? (
                                  <Loader2 size={13} className="animate-spin" />
                                ) : (
                                  <PlayCircle size={13} />
                                )}
                                任务探测
                              </button>
                            )}
                        </div>
                      )}
                      {providerProbeResults[provider.provider] && (
                        <div className="mt-2 rounded bg-surface-container px-2 py-1.5">
                          <p>
                            探测结果:{" "}
                            <span className="font-data text-on-surface">
                              {providerProbeResults[provider.provider].status}
                            </span>
                          </p>
                          <p className="mt-1 break-words">
                            {providerProbeResults[provider.provider].message}
                          </p>
                          {providerProbeResults[provider.provider].health
                            ?.reason && (
                            <p className="mt-1 break-words">
                              健康原因:{" "}
                              {
                                providerProbeResults[provider.provider].health
                                  ?.reason
                              }
                            </p>
                          )}
                          {providerProbeResults[provider.provider].health
                            ?.launch_kind && (
                            <p className="mt-1">
                              探测启动:{" "}
                              <span className="font-data text-on-surface">
                                {
                                  providerProbeResults[provider.provider].health
                                    ?.launch_kind
                                }
                              </span>
                              {providerProbeResults[provider.provider].health
                                ?.used_fallback && (
                                <span className="ml-2 font-medium text-warning">
                                  fallback
                                </span>
                              )}
                            </p>
                          )}
                          {providerProbeResults[provider.provider].health
                            ?.attempts && (
                            <p className="mt-1">
                              探测次数:{" "}
                              <span className="font-data text-on-surface">
                                {
                                  providerProbeResults[provider.provider].health
                                    ?.attempts?.length
                                }
                              </span>
                            </p>
                          )}
                          {(() => {
                            const attempts =
                              providerProbeResults[provider.provider].health
                                ?.attempts ?? [];
                            if (attempts.length === 0) return null;
                            return (
                              <div className="mt-2 space-y-1">
                                {attempts.slice(0, 3).map((attempt, index) => {
                                  const resolutionLines =
                                    commandResolutionLines(attempt.resolution);
                                  return (
                                    <div
                                      key={`${attempt.command ?? attempt.executable ?? index}-${index}`}
                                      className="rounded border border-outline-variant/30 px-2 py-1"
                                    >
                                      <p className="break-words font-data text-[10px] text-on-surface">
                                        attempt {index + 1}:{" "}
                                        {attempt.command ||
                                          attempt.executable ||
                                          "unknown"}{" "}
                                        {attempt.status ||
                                          attempt.probe_status ||
                                          "unknown"}
                                      </p>
                                      {(attempt.reason ||
                                        attempt.probe_message) && (
                                        <p className="mt-1 break-words">
                                          {attempt.reason ||
                                            attempt.probe_message}
                                        </p>
                                      )}
                                      {resolutionLines.length > 0 && (
                                        <div className="mt-1 space-y-0.5">
                                          {resolutionLines.map((line) => (
                                            <p
                                              key={line}
                                              className="break-words font-data text-[10px] text-on-surface"
                                            >
                                              {line}
                                            </p>
                                          ))}
                                        </div>
                                      )}
                                    </div>
                                  );
                                })}
                                {attempts.length > 3 && (
                                  <p className="font-data text-[10px]">
                                    +{attempts.length - 3} more attempts in
                                    artifact
                                  </p>
                                )}
                              </div>
                            );
                          })()}
                        </div>
                      )}
                      {providerTaskProbeResults[provider.provider] && (
                        <div className="mt-2 rounded bg-surface-container px-2 py-1.5">
                          <p>
                            任务探测:{" "}
                            <span className="font-data text-on-surface">
                              {
                                providerTaskProbeResults[provider.provider]
                                  .status
                              }
                            </span>
                            <span className="ml-2 font-data text-on-surface">
                              contract:
                              {
                                providerTaskProbeResults[provider.provider]
                                  .summary.task_contract_status
                              }
                            </span>
                          </p>
                          <p className="mt-1">
                            Execution:{" "}
                            <span className="font-data text-on-surface">
                              {
                                providerTaskProbeResults[provider.provider]
                                  .summary.execution_status
                              }
                            </span>
                            <span className="ml-2 font-data text-on-surface">
                              missing:
                              {
                                providerTaskProbeResults[provider.provider]
                                  .summary.missing_required
                              }
                            </span>
                          </p>
                          {providerTaskProbeResults[provider.provider].summary
                            .missing_artifacts.length > 0 && (
                            <p className="mt-1 break-words text-warning">
                              缺失产物:{" "}
                              {providerTaskProbeResults[
                                provider.provider
                              ].summary.missing_artifacts.join(", ")}
                            </p>
                          )}
                          <p className="mt-1 break-words font-data text-[10px]">
                            artifact:
                            {
                              providerTaskProbeResults[provider.provider]
                                .artifact.path
                            }
                          </p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
              {!providerMatrix && (
                <p className="text-sm text-on-surface-variant">
                  执行器诊断会随工作台数据一起加载。
                </p>
              )}
            </div>
            {providerMatrix?.notes?.length ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {providerMatrix.notes.map((note) => (
                  <span
                    key={note}
                    className="rounded bg-surface px-2 py-1 text-xs text-on-surface-variant"
                  >
                    {note}
                  </span>
                ))}
              </div>
            ) : null}
          </Panel>);
}
