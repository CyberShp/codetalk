---
feature_ids: [workbench-v3, zero-public-egress]
topics: [security, network, agents, sdk]
doc_kind: threat-model
created: 2026-07-23
---

# Controlled Egress Threat Model

## Security objective

When CodeTalk runs in intranet mode, product services, model clients, MCP clients, and
spawned Agent processes must not establish a connection to an unapproved destination.
Allowed destinations are Unix/local sockets plus deployment-approved hostnames and CIDRs.
An enterprise hostname may legitimately resolve to a non-RFC1918 address; it is the approved
hostname/CIDR policy, not a simplistic private-IP test, that determines admission. An explicitly
approved model-provider endpoint is permitted through CodeTalk's provider adapter, even when it
uses a public-looking address.

The product boundary is **controlled purpose-based egress**, not an IP-address heuristic:
an approved model inference request is permitted, while the same vendor's SDK telemetry,
tracing, update, extension marketplace, package registry, callback, or hosted-MCP request is
not. Approval of a model host never grants those autonomous uses.

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
   API route; telemetry, package/update, hosted-trace and hosted-MCP destinations are hard-denied
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
