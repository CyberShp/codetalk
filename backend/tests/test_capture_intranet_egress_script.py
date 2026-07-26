from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_capture_script_records_redacted_process_snapshot_without_changing_network(tmp_path: Path):
    """The deployment collector must correlate a pcap with CodeTalk processes.

    A tiny local tcpdump stand-in exercises the shell contract without claiming
    a traffic capture.  It also guards against copying an API key from a
    process command line into evidence that operators will archive.
    """
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "capture-intranet-egress.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "tcpdump").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  if [[ $1 == -w ]]; then printf 'pcap' > \"$2\"; exit 0; fi\n"
        "  shift\n"
        "done\n",
        encoding="utf-8",
    )
    (fake_bin / "shasum").write_text(
        "#!/usr/bin/env bash\n"
        "printf 'fake-sha256  %s\\n' \"${@: -1}\"\n",
        encoding="utf-8",
    )
    for item in fake_bin.iterdir():
        item.chmod(0o755)

    output = tmp_path / "evidence"
    result = subprocess.run(
        [
            "bash", str(script), "--interface", "lo0", "--output", str(output),
            "--seconds", "1", "--label", "contract",
        ],
        cwd=repo,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CODETALK_CAPTURE_TEST_API_KEY": "sk-very-secret-test-token",
        },
        check=True,
        text=True,
        capture_output=True,
    )

    manifest = (output / "contract-egress-manifest.txt").read_text(encoding="utf-8")
    snapshot = output / "contract-processes-before.txt"
    assert "process_snapshot_before=contract-processes-before.txt" in manifest
    assert "network_configuration_changed=false" in manifest
    assert snapshot.is_file()
    assert "sk-very-secret-test-token" not in snapshot.read_text(encoding="utf-8")
    assert (output / "contract-egress.pcap").read_text(encoding="utf-8") == "pcap"
    assert "Evidence written" in result.stdout
