"""Offline contract check for the bounded chat-load scenario."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[2]
K6 = shutil.which("k6") or r"C:\Program Files\k6\k6.exe"


@pytest.mark.skipif(not Path(K6).is_file(), reason="k6 not installed")
def test_chat_scenario_never_exceeds_model_call_budget(tmp_path: Path):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(size)
            if self.path.endswith("/chat/sessions"):
                payload = {"id": "00000000-0000-0000-0000-000000000001"}
            elif self.path.endswith("/messages"):
                calls.append(self.path)
                payload = {"answer": "offline stub"}
            else:
                self.send_error(404)
                return
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    identities = tmp_path / "identities.json"
    identities.write_text(json.dumps([
        {"bearer_token": f"fake-token-{i}", "active_org_id": "test-org",
         "notebook_id": "00000000-0000-0000-0000-000000000002"}
        for i in range(2)
    ]), encoding="utf-8")
    summary = tmp_path / "summary.json"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = dict(os.environ)
        for key in ("K6_VUS", "K6_DURATION", "K6_ITERATIONS", "K6_STAGES"):
            env.pop(key, None)
        env.update(K6_API_BASE_URL=f"http://127.0.0.1:{server.server_port}",
                   K6_IDENTITIES_FILE=str(identities), K6_WORKLOAD="chat",
                   PRODUCT_VUS="2", PRODUCT_DURATION="20s", K6_MAX_MODEL_CALLS="3",
                   K6_ALLOW_LIVE_MODEL_CALLS="yes", K6_PROVIDER_MODE="stub",
                   K6_SUMMARY_FILE=str(summary), K6_NO_USAGE_REPORT="true")
        run = subprocess.run([K6, "run", str(ROOT / "tests/system/load/capacity.js")],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert run.returncode == 0, run.stdout[-1500:] + run.stderr[-1500:]
    assert len(calls) == 3
    assert json.loads(summary.read_text(encoding="utf-8"))["max_model_calls"] == 3
