#!/usr/bin/env python3
"""CodeCompass wrapper: starts webserver + exposes POST /api/parse for parser.

Runs CodeCompass_webserver on an internal port and proxies all traffic,
adding the /api/parse endpoint that invokes CodeCompass_parser.
"""

import http.server
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

WEBSERVER_PORT = int(os.environ.get("CC_WEBSERVER_PORT", "6252"))
LISTEN_PORT = int(os.environ.get("CC_LISTEN_PORT", "6251"))
WORKSPACE_DIR = os.environ.get("CC_WORKSPACE_DIR", "/data/workspaces")
DB_CONNECTION = os.environ.get(
    "CC_DATABASE",
    "sqlite:/data/workspaces/cc.sqlite",
)
# Parser timeout: 30 minutes (large C++ projects)
PARSE_TIMEOUT = int(os.environ.get("CC_PARSE_TIMEOUT", "1800"))


class Handler(http.server.BaseHTTPRequestHandler):
    """Route /api/parse to parser, proxy everything else to webserver."""

    def do_POST(self):
        if self.path == "/api/parse":
            self._handle_parse()
        else:
            self._proxy()

    def do_GET(self):
        self._proxy()

    def _handle_parse(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}

        project_name = body.get("project_name", "default")
        source_path = body.get("source_path", "")

        if not source_path:
            self._respond(400, {"error": "source_path is required"})
            return

        workspace = f"{WORKSPACE_DIR}/{project_name}"
        os.makedirs(workspace, exist_ok=True)

        cmd = [
            "CodeCompass_parser",
            "-d", DB_CONNECTION,
            "-w", workspace,
            "-n", project_name,
            "-i", source_path,
        ]

        self.log_message("Starting parse: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=PARSE_TIMEOUT,
            )
            if result.returncode == 0:
                self._respond(200, {"status": "ok", "project": project_name})
            else:
                self._respond(500, {
                    "error": "parse failed",
                    "returncode": result.returncode,
                    "stderr": result.stderr[-1000:] if result.stderr else "",
                })
        except subprocess.TimeoutExpired:
            self._respond(504, {"error": "parse timed out", "timeout": PARSE_TIMEOUT})
        except FileNotFoundError:
            self._respond(500, {"error": "CodeCompass_parser not found in PATH"})
        except Exception as exc:
            self._respond(500, {"error": str(exc)})

    def _proxy(self):
        url = f"http://localhost:{WEBSERVER_PORT}{self.path}"
        try:
            req = urllib.request.Request(url, method=self.command)
            for key in ("Content-Type", "Accept"):
                val = self.headers.get(key)
                if val:
                    req.add_header(key, val)

            data = None
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length:
                data = self.rfile.read(content_length)

            with urllib.request.urlopen(req, data=data, timeout=60) as resp:
                body = resp.read()
                self.send_response(resp.status)
                for key, value in resp.headers.items():
                    if key.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self._respond(502, {"error": f"proxy to webserver failed: {exc}"})

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    # Start CodeCompass_webserver on internal port
    webserver_proc = subprocess.Popen(
        ["CodeCompass_webserver", "-w", WORKSPACE_DIR, "-p", str(WEBSERVER_PORT)],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    print(
        f"CodeCompass wrapper: webserver pid={webserver_proc.pid} "
        f"on :{WEBSERVER_PORT}, wrapper on :{LISTEN_PORT}",
        flush=True,
    )

    server = http.server.HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        webserver_proc.terminate()
        webserver_proc.wait(timeout=10)


if __name__ == "__main__":
    main()
