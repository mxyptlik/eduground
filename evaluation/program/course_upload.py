"""Resumable course-file intake through Eduground's notebook and source APIs.

The API source list is the resume journal. Every invocation inventories the local
tree again and compares checksums within the corresponding notebook. This module
never writes directly to storage, ingestion tables, or the vector index.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from evaluation.program.corpus_inventory import InventoryError, inventory_courses


class ApiError(RuntimeError):
    def __init__(self, status: int | None, message: str) -> None:
        self.status = status
        super().__init__(message)


class CourseApi(Protocol):
    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any: ...
    def put_file(self, upload_url: str, path: Path, mime_type: str, byte_size: int) -> None: ...


class EdugroundApi:
    """Small authenticated HTTP client; signed PUT deliberately omits auth headers."""

    def __init__(self, base_url: str, bearer_token: str, organization_id: str | None = None) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("API base URL must use http or https")
        if not bearer_token.strip():
            raise ValueError("A bearer token is required for execution")
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.organization_id = organization_id

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self.bearer_token}", "Accept": "application/json"}
        if self.organization_id:
            headers["X-Active-Organization-Id"] = self.organization_id
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, f"{method} {path}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(None, f"{method} {path}: API unreachable: {exc.reason}") from exc

    def put_file(self, upload_url: str, path: Path, mime_type: str, byte_size: int) -> None:
        url = urllib.parse.urljoin(self.base_url + "/", upload_url)
        if not url.startswith(("http://", "https://")):
            raise ApiError(None, "Signed upload URL must use http or https")
        with path.open("rb") as handle:
            blocks = iter(lambda: handle.read(1024 * 1024), b"")
            request = urllib.request.Request(
                url, data=blocks, method="PUT",
                headers={"Content-Type": mime_type, "Content-Length": str(byte_size)},
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    response.read()
            except urllib.error.HTTPError as exc:
                raise ApiError(exc.code, f"Signed upload: HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                raise ApiError(None, f"Signed upload unreachable: {exc.reason}") from exc


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError) as exc:
        raise InventoryError(f"Invalid notebook_id: {value!r}") from exc


def _file_hash(path: Path) -> tuple[str, int]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise InventoryError(f"File changed during checksum verification: {path}")
    return digest.hexdigest(), after.st_size


def _verify_path(root: Path, path: Path) -> None:
    if not path.resolve(strict=True).is_relative_to(root):
        raise InventoryError(f"Course file escaped the supplied root: {path}")
    current = path
    while current != root:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise InventoryError(f"Linked course path appeared after inventory: {current}")
        current = current.parent


def _notebook_for_course(api: CourseApi, course: dict[str, Any]) -> str:
    existing_id = course.get("notebook_id")
    if existing_id:
        notebook_id = _uuid(existing_id)
        notebook = api.request_json("GET", f"/api/notebooks/{notebook_id}")
        if notebook.get("id") != notebook_id:
            raise ApiError(None, f"Notebook lookup mismatch for course {course['course_id']}")
        return notebook_id

    title = f"Eduground Evaluation - {course['course_id']}"
    description = f"Eduground evaluation course {course['course_id']}; source-only corpus."
    matches = [notebook for notebook in api.request_json("GET", "/api/notebooks")
               if notebook.get("title") == title and notebook.get("description") == description]
    if len(matches) > 1:
        raise ApiError(None, f"More than one evaluation notebook matches course {course['course_id']}")
    if matches:
        return _uuid(matches[0]["id"])
    created = api.request_json("POST", "/api/notebooks", {
        "title": title,
        "description": description,
        "visibility": "private",
        "policy_mode": "teaching",
        "course_offering_id": None,
    })
    return _uuid(created["id"])


def _sources_by_checksum(api: CourseApi, notebook_id: str) -> dict[str, dict[str, Any]]:
    sources = api.request_json("GET", f"/api/notebooks/{notebook_id}/sources")
    mapping: dict[str, dict[str, Any]] = {}
    for source in sources:
        checksum = source["checksum_sha256"]
        if checksum in mapping:
            raise ApiError(None, f"Ambiguous active source checksum in notebook {notebook_id}: {checksum}")
        mapping[checksum] = source
    return mapping


def _signed_upload(api: CourseApi, notebook_id: str, path: Path, item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = {
        "filename": path.name,
        "mime_type": mime_type,
        "byte_size": item["byte_size"],
        "checksum_sha256": item["sha256"],
    }
    endpoint = f"/api/notebooks/{notebook_id}/sources/upload-url"
    intent = api.request_json("POST", endpoint, payload)
    fallback_used = False
    try:
        api.put_file(intent["upload_url"], path, mime_type, item["byte_size"])
    except ApiError:
        intent = api.request_json("POST", endpoint, {**payload, "force_local_fallback": True})
        api.put_file(intent["upload_url"], path, mime_type, item["byte_size"])
        fallback_used = True
    source = api.request_json("POST", f"/api/notebooks/{notebook_id}/sources", {
        "upload_intent_id": intent["upload_intent_id"],
        "module_id": None,
        "source_type": item["source_type"],
        "title": path.stem[:255],
        "original_filename": path.name,
        "storage_key": intent["storage_key"],
        "mime_type": mime_type,
        "checksum_sha256": item["sha256"],
        "byte_size": item["byte_size"],
        "language_code": None,
    })
    return source, fallback_used


def upload_courses(root: Path, api: CourseApi, registry: Path | None = None) -> dict[str, Any]:
    """Execute intake. Re-running uses current API state, not a fragile local journal."""
    manifest = inventory_courses(root, registry)
    root = Path(manifest["root"])
    items_by_course: dict[str, list[dict[str, Any]]] = {}
    for item in manifest["documents"]:
        items_by_course.setdefault(item["course_id"], []).append(item)
    results: list[dict[str, Any]] = []
    notebooks: dict[str, str] = {}
    for course in manifest["courses"]:
        course_id = course["course_id"]
        try:
            notebook_id = _notebook_for_course(api, course)
            notebooks[course_id] = notebook_id
            known = _sources_by_checksum(api, notebook_id)
        except (ApiError, InventoryError, KeyError, TypeError) as exc:
            results.append({"course_id": course_id, "action": "error", "error": str(exc)})
            continue
        for item in items_by_course.get(course_id, []):
            result = {"course_id": course_id, "relative_path": item["relative_path"], "notebook_id": notebook_id}
            if item["status"] != "candidate":
                results.append({**result, "action": "skipped", "reason": item["status"]})
                continue
            try:
                path = root / item["relative_path"]
                _verify_path(root, path)
                # Reject a changed tree even if the old checksum already exists remotely.
                actual_hash, actual_size = _file_hash(path)
                if (actual_hash, actual_size) != (item["sha256"], item["byte_size"]):
                    raise InventoryError(f"File changed since inventory: {path}")
                existing = known.get(actual_hash)
                if existing:
                    if existing["status"] == "failed":
                        api.request_json("POST", f"/api/sources/{existing['id']}/reindex", {"force": False})
                        action = "reindexed"
                    else:
                        action = "existing"
                    results.append({**result, "action": action, "source_id": existing["id"],
                                    "source_status": existing["status"]})
                    continue
                try:
                    source, fallback_used = _signed_upload(api, notebook_id, path, item)
                except ApiError as exc:
                    if exc.status != 409:
                        raise
                    # A concurrent or previously finalized upload may have won.
                    known = _sources_by_checksum(api, notebook_id)
                    existing = known.get(actual_hash)
                    if existing is None:
                        raise
                    if existing["status"] == "failed":
                        api.request_json("POST", f"/api/sources/{existing['id']}/reindex", {"force": False})
                        action = "reindexed"
                    else:
                        action = "existing"
                    results.append({**result, "action": action, "source_id": existing["id"],
                                    "source_status": existing["status"]})
                    continue
                known[actual_hash] = source
                results.append({**result, "action": "uploaded", "source_id": source["id"],
                                "source_status": source["status"], "local_fallback": fallback_used})
            except (ApiError, InventoryError, OSError, KeyError, TypeError) as exc:
                results.append({**result, "action": "error", "error": str(exc)})
    return {
        "schema_version": 1,
        "mode": "execute",
        "notebooks": notebooks,
        "results": results,
        "summary": {action: sum(row["action"] == action for row in results)
                    for action in ("uploaded", "existing", "reindexed", "skipped", "error")},
    }
