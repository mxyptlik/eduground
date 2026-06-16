from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def normalize_base_url(value: str) -> str:
    return value.rstrip("/")


class EdugroundClient:
    def __init__(self, base_url: str, bearer_token: str, active_org_id: str | None = None) -> None:
        self.base_url = normalize_base_url(base_url)
        self.headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }
        if active_org_id:
            self.headers["X-Active-Organization-Id"] = active_org_id

    def request_json(self, method: str, path: str, payload: dict | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}{path}", data=body, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc

    def put_file(self, upload_url: str, path: Path, mime_type: str) -> None:
        data = path.read_bytes()
        url = upload_url if upload_url.startswith("http") else f"{self.base_url}{upload_url}"
        request = urllib.request.Request(url, data=data, method="PUT", headers={"Content-Type": mime_type})
        with urllib.request.urlopen(request, timeout=180) as response:
            response.read()


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_type_for(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix not in {"pdf", "pptx", "docx", "txt", "md"}:
        raise ValueError(f"Unsupported Eduground source type: {path}")
    return suffix


def upload_sources(client: EdugroundClient, notebook_id: str, manifest: Path) -> list[dict]:
    uploaded: list[dict] = []
    for item in json.loads(manifest.read_text(encoding="utf-8")):
        path = Path(item)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        file_hash = checksum(path)
        upload = client.request_json(
            "POST",
            f"/api/notebooks/{notebook_id}/sources/upload-url",
            {
                "filename": path.name,
                "mime_type": mime_type,
                "byte_size": path.stat().st_size,
                "checksum_sha256": file_hash,
                "force_local_fallback": False,
            },
        )
        client.put_file(upload["upload_url"], path, mime_type)
        source = client.request_json(
            "POST",
            f"/api/notebooks/{notebook_id}/sources",
            {
                "upload_intent_id": upload["upload_intent_id"],
                "module_id": None,
                "source_type": source_type_for(path),
                "title": path.stem[:255],
                "original_filename": path.name,
                "storage_key": upload["storage_key"],
                "mime_type": mime_type,
                "checksum_sha256": file_hash,
                "byte_size": path.stat().st_size,
                "language_code": "en",
            },
        )
        uploaded.append(source)
        print(json.dumps({"uploaded": path.name, "source_id": source["id"], "status": source["status"]}))
    return uploaded


def wait_for_sources(client: EdugroundClient, sources: list[dict], timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    pending = {source["id"] for source in sources}
    while pending and time.time() < deadline:
        for source_id in list(pending):
            source = client.request_json("GET", f"/api/sources/{source_id}")
            status = source["status"]
            if status == "indexed":
                pending.remove(source_id)
                print(json.dumps({"source_id": source_id, "status": status}))
            elif status == "failed":
                raise RuntimeError(f"Source {source_id} failed ingestion: {source.get('latest_job')}")
        if pending:
            time.sleep(10)
    if pending:
        raise TimeoutError(f"Timed out waiting for {len(pending)} source(s) to index: {sorted(pending)}")


def create_or_select_notebook(client: EdugroundClient, notebook_id: str | None, title: str) -> str:
    if notebook_id:
        return notebook_id
    notebook = client.request_json(
        "POST",
        "/api/notebooks",
        {
            "title": title,
            "description": "Automated 100-question golden QA evaluation notebook.",
            "visibility": "private",
            "policy_mode": "teaching",
            "course_offering_id": None,
        },
    )
    print(json.dumps({"created_notebook_id": notebook["id"], "title": notebook["title"]}))
    return notebook["id"]


def run_questions(client: EdugroundClient, notebook_id: str, dataset: list[dict], delay_seconds: float, limit: int | None) -> list[dict]:
    session = client.request_json("POST", f"/api/notebooks/{notebook_id}/chat/sessions", {"title": "Golden QA evaluation"})
    session_id = session["id"]
    completed: list[dict] = []
    selected = dataset[:limit] if limit else dataset
    for index, sample in enumerate(selected, start=1):
        answer = client.request_json(
            "POST",
            f"/api/chat/sessions/{session_id}/messages",
            {
                "content_markdown": sample["question"],
                "selected_source_ids": [],
                "selected_module_ids": [],
            },
        )
        citations = answer.get("citations") or []
        retrieved_context = "\n\n".join(citation.get("quote_text", "") for citation in citations).strip()
        completed_sample = dict(sample)
        completed_sample.update(
            {
                "retrieved_context": retrieved_context,
                "model_answer": answer.get("content_markdown", ""),
                "eduground_answer_type": answer.get("answer_type"),
                "eduground_refusal_reason": answer.get("refusal_reason"),
                "eduground_citations": citations,
                "eduground_retrieval_trace": answer.get("retrieval_trace"),
            }
        )
        completed.append(completed_sample)
        print(json.dumps({"completed": index, "sample_key": sample["sample_key"], "answer_type": answer.get("answer_type")}))
        if delay_seconds:
            time.sleep(delay_seconds)
    return completed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a golden QA file against a deployed Eduground API.")
    parser.add_argument("--base-url", default=os.getenv("EDUGROUND_API_BASE_URL"))
    parser.add_argument("--bearer-token", default=os.getenv("EDUGROUND_BEARER_TOKEN"))
    parser.add_argument("--active-org-id", default=os.getenv("EDUGROUND_ACTIVE_ORG_ID"))
    parser.add_argument("--notebook-id", default=os.getenv("EDUGROUND_NOTEBOOK_ID"))
    parser.add_argument("--create-notebook-title", default="Eduground deployed golden QA")
    parser.add_argument("--upload-manifest", type=Path)
    parser.add_argument("--wait-for-ingestion", action="store_true")
    parser.add_argument("--ingestion-timeout-seconds", type=int, default=1800)
    parser.add_argument("--dataset", type=Path, default=Path("apps/evaluator/app/datasets/eduground_100q.seed.json"))
    parser.add_argument("--output", type=Path, default=Path("apps/evaluator/app/datasets/eduground_100q.deployed.json"))
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    if not args.base_url or not args.bearer_token:
        raise SystemExit("Provide --base-url and --bearer-token, or set EDUGROUND_API_BASE_URL and EDUGROUND_BEARER_TOKEN.")

    client = EdugroundClient(args.base_url, args.bearer_token, args.active_org_id)
    notebook_id = create_or_select_notebook(client, args.notebook_id, args.create_notebook_title)
    if args.upload_manifest:
        sources = upload_sources(client, notebook_id, args.upload_manifest)
        if args.wait_for_ingestion:
            wait_for_sources(client, sources, args.ingestion_timeout_seconds)
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    completed = run_questions(client, notebook_id, dataset, args.delay_seconds, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(completed, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "sample_count": len(completed), "notebook_id": notebook_id}, indent=2))


if __name__ == "__main__":
    main()
