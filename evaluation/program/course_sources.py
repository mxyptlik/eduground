"""Read-only adoption and ingestion status inspection through existing APIs."""

from __future__ import annotations

from typing import Any

from evaluation.program.corpus_inventory import inventory_courses
from evaluation.program.course_upload import CourseApi, _uuid


def inspect_course_sources(root, api: CourseApi, registry=None) -> dict[str, Any]:
    """Compare course files with visible notebook sources and all job attempts.

    A notebook-only mapping can be inventoried but remains unverified against
    original bytes until the reviewer supplies a file manifest or directory.
    """
    manifest = inventory_courses(root, registry)
    candidates: dict[str, dict[str, list[str]]] = {}
    for item in manifest["documents"]:
        if item["status"] == "candidate":
            candidates.setdefault(item["course_id"], {}).setdefault(item["sha256"], []).append(item["relative_path"])
    courses: list[dict[str, Any]] = []
    for course in manifest["courses"]:
        notebook_id = course["notebook_id"]
        if not notebook_id:
            courses.append({"course_id": course["course_id"], "status": "notebook_mapping_required",
                            "sources": [], "missing_files": sorted(sum(candidates.get(course["course_id"], {}).values(), []))})
            continue
        notebook_id = _uuid(notebook_id)
        notebook = api.request_json("GET", f"/api/notebooks/{notebook_id}")
        if notebook.get("id") != notebook_id:
            raise ValueError(f"Notebook response mismatch for {course['course_id']}")
        visible = api.request_json("GET", f"/api/notebooks/{notebook_id}/sources")
        expected = candidates.get(course["course_id"], {})
        seen_hashes: set[str] = set()
        rows: list[dict[str, Any]] = []
        for source in visible:
            if source.get("notebook_id") != notebook_id:
                raise ValueError(f"Source {source.get('id')} belongs to a different notebook")
            checksum = source["checksum_sha256"].lower()
            if checksum in seen_hashes:
                raise ValueError(f"Duplicate visible checksum in notebook {notebook_id}: {checksum}")
            seen_hashes.add(checksum)
            jobs = api.request_json("GET", f"/api/sources/{source['id']}/events")
            if any(job.get("source_id") != source["id"] for job in jobs):
                raise ValueError(f"Job response mismatch for source {source['id']}")
            latest = jobs[0] if jobs else None
            local_paths = expected.get(checksum, [])
            status = source["status"]
            rows.append({
                "source_id": source["id"], "checksum_sha256": checksum,
                "version_number": source["version_number"], "source_type": source["source_type"],
                "byte_size": source["byte_size"], "status": status,
                "local_paths": local_paths,
                "membership": "matched" if local_paths else ("not_verified" if not course["relative_directory"] else "unexpected"),
                "evidence_eligible": status == "indexed" and bool(local_paths),
                "latest_job": None if latest is None else {
                    key: latest.get(key) for key in
                    ("id", "status", "stage", "error_code", "attempt_count", "segment_count",
                     "chunk_count", "embedded_count", "indexed_count", "progress_json", "finished_at")
                },
                "job_attempts": [{"id": job.get("id"), "status": job.get("status"),
                                  "attempt_count": job.get("attempt_count"), "error_code": job.get("error_code")}
                                 for job in jobs],
            })
        missing = [path for digest, paths in expected.items() if digest not in seen_hashes for path in paths]
        courses.append({
            "course_id": course["course_id"], "notebook_id": notebook_id,
            "notebook_title": notebook.get("title"),
            "status": "matched" if expected and not missing and all(row["membership"] == "matched" for row in rows)
                      else "needs_review",
            "sources": rows, "missing_files": sorted(missing),
            "indexed_eligible_count": sum(row["evidence_eligible"] for row in rows),
        })
    return {"schema_version": 1, "read_only": True, "courses": courses}
