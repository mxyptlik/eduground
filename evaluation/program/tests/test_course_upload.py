"""Course upload tests exercise API state, reruns, fallback, and actual HTTP headers."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from evaluation.program.course_upload import ApiError, EdugroundApi, upload_courses


REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "tools" / "evaluation" / "upload_courses.py"
NOTEBOOK_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"


class FakeCourseApi:
    def __init__(self) -> None:
        self.notebooks: list[dict] = []
        self.sources: dict[str, list[dict]] = {}
        self.intents: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail_primary_put = False
        self.fail_finalize_once = False
        self.mutate_at_source_list = None

    def request_json(self, method: str, path: str, payload: dict | None = None):
        self.calls.append((method, path, payload))
        if (method, path) == ("GET", "/api/notebooks"):
            return list(self.notebooks)
        if (method, path) == ("POST", "/api/notebooks"):
            notebook = {"id": NOTEBOOK_ID, **payload}
            self.notebooks.append(notebook)
            return notebook
        if method == "GET" and path == f"/api/notebooks/{NOTEBOOK_ID}":
            return next(notebook for notebook in self.notebooks if notebook["id"] == NOTEBOOK_ID)
        if method == "GET" and path == f"/api/notebooks/{NOTEBOOK_ID}/sources":
            if self.mutate_at_source_list:
                self.mutate_at_source_list()
                self.mutate_at_source_list = None
            return list(self.sources.get(NOTEBOOK_ID, []))
        if method == "POST" and path.endswith("/sources/upload-url"):
            number = str(len(self.intents) + 1)
            intent = {"upload_intent_id": number, "storage_key": f"storage/{number}",
                      "upload_url": f"http://fake/upload/{number}", "payload": payload}
            self.intents[number] = intent
            return intent
        if method == "POST" and path == f"/api/notebooks/{NOTEBOOK_ID}/sources":
            if self.fail_finalize_once:
                self.fail_finalize_once = False
                raise ApiError(503, "simulated queue outage")
            intent = self.intents[payload["upload_intent_id"]]
            assert hashlib.sha256(intent["data"]).hexdigest() == payload["checksum_sha256"]
            assert len(intent["data"]) == payload["byte_size"]
            if any(item["checksum_sha256"] == payload["checksum_sha256"] for item in self.sources.get(NOTEBOOK_ID, [])):
                raise ApiError(409, "duplicate")
            source = {"id": SOURCE_ID, "checksum_sha256": payload["checksum_sha256"],
                      "source_type": payload["source_type"], "status": "processing"}
            self.sources.setdefault(NOTEBOOK_ID, []).append(source)
            return source
        if method == "POST" and path == f"/api/sources/{SOURCE_ID}/reindex":
            self.sources[NOTEBOOK_ID][0]["status"] = "processing"
            return {"status": "retrying"}
        raise AssertionError((method, path))

    def put_file(self, upload_url: str, path: Path, mime_type: str, byte_size: int) -> None:
        intent = self.intents[upload_url.rsplit("/", 1)[1]]
        if self.fail_primary_put and not intent["payload"].get("force_local_fallback"):
            raise ApiError(503, "primary storage unavailable")
        intent["data"] = path.read_bytes()
        assert byte_size == len(intent["data"])


class UploadTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "CSC442").mkdir()
        self.file = self.root / "CSC442" / "lecture.pdf"
        self.file.write_bytes(b"%PDF-1.7 course A")
        self.api = FakeCourseApi()

    def test_new_notebook_upload_and_repeat_run_are_idempotent(self) -> None:
        same = self.root / "CSC442" / "copy.pdf"
        same.write_bytes(self.file.read_bytes())
        first = upload_courses(self.root, self.api)
        self.assertEqual(first["summary"]["uploaded"], 1)
        self.assertEqual(first["summary"]["existing"], 1)
        self.assertEqual(first["notebooks"]["CSC442"], NOTEBOOK_ID)
        self.assertEqual(len(self.api.sources[NOTEBOOK_ID]), 1)
        second = upload_courses(self.root, self.api)
        self.assertEqual(second["summary"]["uploaded"], 0)
        self.assertEqual(second["summary"]["existing"], 2)
        self.assertEqual(len(self.api.notebooks), 1)
        self.assertEqual(len(self.api.sources[NOTEBOOK_ID]), 1)
        self.assertEqual(len([call for call in self.api.calls if call[1].endswith("/sources/upload-url")]), 1)

    def test_failed_finalize_can_resume_without_duplicate_source(self) -> None:
        self.api.fail_finalize_once = True
        first = upload_courses(self.root, self.api)
        self.assertEqual(first["summary"]["error"], 1)
        self.assertEqual(self.api.sources.get(NOTEBOOK_ID), None)
        second = upload_courses(self.root, self.api)
        self.assertEqual(second["summary"]["uploaded"], 1)
        self.assertEqual(len(self.api.sources[NOTEBOOK_ID]), 1)

    def test_failed_ingestion_reuses_source_and_requests_reindex(self) -> None:
        upload_courses(self.root, self.api)
        self.api.sources[NOTEBOOK_ID][0]["status"] = "failed"
        result = upload_courses(self.root, self.api)
        self.assertEqual(result["summary"]["reindexed"], 1)
        self.assertEqual(len(self.api.sources[NOTEBOOK_ID]), 1)
        self.assertIn(("POST", f"/api/sources/{SOURCE_ID}/reindex", {"force": False}), self.api.calls)

    def test_primary_storage_failure_uses_normal_local_fallback_intent(self) -> None:
        self.api.fail_primary_put = True
        result = upload_courses(self.root, self.api)
        self.assertEqual(result["summary"]["uploaded"], 1)
        self.assertTrue(result["results"][0]["local_fallback"])
        self.assertEqual(len(self.api.intents), 2)
        self.assertTrue(self.api.intents["2"]["payload"]["force_local_fallback"])

    def test_changed_file_is_not_uploaded_after_inventory(self) -> None:
        self.api.mutate_at_source_list = lambda: self.file.write_bytes(b"changed after inventory")
        result = upload_courses(self.root, self.api)
        self.assertEqual(result["summary"]["error"], 1)
        self.assertIn("changed since inventory", result["results"][0]["error"])
        self.assertEqual(len(self.api.intents), 0)

    def test_registry_notebook_mapping_skips_notebook_creation(self) -> None:
        self.api.notebooks.append({"id": NOTEBOOK_ID, "title": "Existing", "description": None})
        with (self.root / "courses.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["course_id", "relative_directory", "notebook_id"])
            writer.writeheader()
            writer.writerow({"course_id": "CSC442", "relative_directory": "CSC442", "notebook_id": NOTEBOOK_ID})
        result = upload_courses(self.root, self.api)
        self.assertEqual(result["summary"]["uploaded"], 1)
        self.assertFalse(any(method == "POST" and path == "/api/notebooks" for method, path, _ in self.api.calls))

    def test_cli_default_is_dry_run_without_credentials_or_network(self) -> None:
        process = subprocess.run([sys.executable, str(CLI), str(self.root)],
                                 capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        output = json.loads(process.stdout)
        self.assertTrue(output["dry_run"])
        self.assertEqual(output["summary"]["candidate_count"], 1)

    def test_real_http_client_scopes_headers_to_api_and_signed_put(self) -> None:
        observations = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                observations.append(("GET", self.path, dict(self.headers), b""))
                data = b"[]"
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_PUT(self):
                data = self.rfile.read(int(self.headers["Content-Length"]))
                observations.append(("PUT", self.path, dict(self.headers), data))
                self.send_response(200)
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        api = EdugroundApi(f"http://127.0.0.1:{server.server_port}", "secret", "org-1")
        self.assertEqual(api.request_json("GET", "/api/notebooks"), [])
        api.put_file(f"http://127.0.0.1:{server.server_port}/signed-upload?token=x", self.file,
                     "application/pdf", self.file.stat().st_size)
        self.assertEqual(observations[0][2]["Authorization"], "Bearer secret")
        self.assertEqual(observations[0][2]["X-Active-Organization-Id"], "org-1")
        self.assertNotIn("Authorization", observations[1][2])
        self.assertEqual(observations[1][3], self.file.read_bytes())


if __name__ == "__main__":
    unittest.main()
