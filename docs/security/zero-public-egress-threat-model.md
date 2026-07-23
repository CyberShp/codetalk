---
feature_ids: [workbench-v3, zero-public-egress]
topics: [security, network, agents, sdk]
doc_kind: threat-model
created: 2026-07-23
---

# Controlled Runtime Egress Threat Model

## Security objective

When CodeTalk runs in intranet mode, product services, model clients, MCP clients, and
spawned Agent processes must not establish a connection for an unapproved purpose. An enterprise
network may legitimately use addresses that look public; CodeTalk never classifies a destination
as safe or unsafe from its IP range. For non-model integrations, deployment-approved hostnames or
CIDRs remain an explicit routing record. For inference, the user/deployment-configured provider
endpoint plus the provider adapter's narrow inference route is the approval record, even when the
endpoint resolves to a public-looking address.

The product boundary is **controlled purpose-based egress**, not an IP-address heuristic:
an approved model inference request is permitted, while the same vendor's SDK telemetry,
tracing, update, extension marketplace, package registry, callback, or hosted-MCP request is
not. Approval of a model host never grants those autonomous uses.

Development and CI may download, inspect, pin, and compare SDKs under the team's normal approved
network policy. The following controls apply to the deployed CodeTalk runtime, not to that
controlled engineering work.

Provider configuration probes use the same minimal inference route as a real task. Runtime
must not call provider model-list or discovery endpoints merely to populate a selector; the
operator configures an approved model identifier explicitly.

## Threats

| Source | Risk | Required control |
| --- | --- | --- |
| Backend HTTP client | A new integration calls a public URL | Central allow-list transport and tests |
| Provider SDK | telemetry, tracing, update checks, extension discovery, or retries escape the intranet | Disable autonomous features and proxy inheritance; verify with traffic capture |
| CLI Agent | CLI plugin or MCP config connects externally | sanitized environment, generated private-only config, OS-level egress policy |
| MCP configuration | user-supplied endpoint bypasses product routing | validate host/IP and capability registration before process start |
| Browser/UI | frontend fetches an accidental public asset | CSP/connect-src deployment policy and build-time URL lint |
| Deployment | shell package updater or proxy restores egress | dedicated service account, no public DNS route, firewall audit |

## Enforcement layers

1. **Application layer:** all backend outbound calls use CodeTalk's network policy client.
   It rejects every unapproved host/CIDR before connection. Model-provider requests are admitted
   only when the deployment explicitly approves the hostname and the request matches an adapter
   inference API route; model discovery, telemetry, package/update, hosted-trace and hosted-MCP destinations are hard-denied
   even when a bad configuration attempts to allow-list them. It emits a redacted audit event.
2. **Harness layer:** Agent processes receive a scrubbed environment with telemetry/update
   controls disabled and only generated, allow-listed MCP configuration. The command contract
   records effective endpoint identifiers, never secrets.
3. **Host/deployment layer:** the production service account is denied outbound traffic by
   firewall or network namespace policy except configured approved destinations. This is the
   enforcement backstop for arbitrary subprocesses and SDK regressions; it is intentionally not
   implemented as a blanket RFC1918-only rule.
4. **Evidence layer:** each release captures an approved-endpoint-only test run plus packet
   or proxy logs showing no vendor, telemetry, update or unapproved destination. A blocked
   connection is visible in the cockpit's
   technical diagnostics without exposing credential material.

## Non-goals

This policy does not make an untrusted Agent safe to execute arbitrary local commands. It
only governs outbound destinations and purposes. Filesystem, process, secret, and shell
capabilities remain independently constrained by the Agent Harness policy.
