#!/usr/bin/env bash
# Capture a bounded deployment evidence window. Run as an administrator while
# a user starts one approved CodeTalk task in the browser.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: sudo scripts/capture-intranet-egress.sh \
  --interface en0 --output /Volumes/Media/codetalk-e2e-artifacts/<stamp> \
  --seconds 900 --label builtin-deepseek

The capture records DNS plus HTTPS only. It never changes firewall, DNS, proxy,
or CodeTalk configuration. Start it first, run exactly one browser workflow,
then let the bounded window finish or press Ctrl-C.
USAGE
}

interface=""
output=""
seconds=900
label="workflow"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --interface) interface="${2:-}"; shift 2 ;;
    --output) output="${2:-}"; shift 2 ;;
    --seconds) seconds="${2:-}"; shift 2 ;;
    --label) label="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$interface" && -n "$output" ]] || { usage >&2; exit 2; }
[[ "$seconds" =~ ^[1-9][0-9]*$ ]] || { echo "--seconds must be a positive integer" >&2; exit 2; }
command -v tcpdump >/dev/null || { echo "tcpdump is required" >&2; exit 127; }
command -v shasum >/dev/null || { echo "shasum is required" >&2; exit 127; }

mkdir -p "$output"
pcap="$output/${label}-egress.pcap"
manifest="$output/${label}-egress-manifest.txt"
processes_before="$output/${label}-processes-before.txt"
processes_after="$output/${label}-processes-after.txt"
started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

capture_process_snapshot() {
  local destination="$1"
  # Do not archive full process tables or environment-derived secrets.  This
  # is deliberately a correlation aid for CodeTalk service processes, not a
  # forensic dump of every user process on the host.
  ps -axo pid=,ppid=,user=,command= 2>/dev/null \
    | grep -E -i 'codetalk|uvicorn|next([[:space:]]|$)|python[^[:space:]]*.*backend' \
    | grep -v 'grep -E' \
    | sed -E \
      -e 's/sk-[[:alnum:]_-]{8,}/[REDACTED_API_KEY]/g' \
      -e 's/([[:alnum:]_]*(API_KEY|TOKEN|SECRET|PASSWORD)[[:alnum:]_]*=)[^[:space:]]+/\1[REDACTED]/g' \
    > "$destination" || true
}

capture_process_snapshot "$processes_before"

{
  echo "capture_kind=intranet_egress_evidence"
  echo "started_at=$started"
  echo "interface=$interface"
  echo "duration_seconds=$seconds"
  echo "filter=(udp port 53 or tcp port 53 or tcp port 443)"
  echo "network_configuration_changed=false"
  echo "process_snapshot_before=$(basename "$processes_before")"
  echo "process_snapshot_after=$(basename "$processes_after")"
  echo "operator_uid=$(id -u)"
  echo "host=$(hostname)"
  echo "command=tcpdump -n -s 0 -i $interface -G $seconds -W 1 -w $pcap '(udp port 53 or tcp port 53 or tcp port 443)'"
  echo "purpose=Capture one user-triggered approved-provider workflow; inspect DNS/SNI/firewall logs against the deployment allowlist."
  echo "non_claim=This pcap alone does not authorize a destination or prove SDK telemetry absence without matching process and gateway logs."
} > "$manifest"

echo "Capturing $seconds seconds on $interface. Run one browser workflow now."
tcpdump -n -s 0 -i "$interface" -G "$seconds" -W 1 -w "$pcap" \
  '(udp port 53 or tcp port 53 or tcp port 443)'

capture_process_snapshot "$processes_after"

{
  echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  shasum -a 256 "$pcap"
  shasum -a 256 "$processes_before" "$processes_after"
} >> "$manifest"
echo "Evidence written to $output"
