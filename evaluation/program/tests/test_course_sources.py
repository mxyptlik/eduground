import csv

import pytest

from evaluation.program.course_sources import inspect_course_sources


NOTEBOOK = "11111111-1111-1111-1111-111111111111"


class Api:
    def __init__(self, digest, *, status="indexed"):
        self.digest = digest
        self.status = status
        self.paths = []

    def request_json(self, method, path, payload=None):
        self.paths.append(path)
        assert method == "GET" and payload is None
        if path == f"/api/notebooks/{NOTEBOOK}":
            return {"id": NOTEBOOK, "title": "Physics"}
        if path == f"/api/notebooks/{NOTEBOOK}/sources":
            return [{"id": "s1", "notebook_id": NOTEBOOK, "checksum_sha256": self.digest,
                     "version_number": 1, "source_type": "pdf", "byte_size": 3, "status": self.status}]
        if path == "/api/sources/s1/events":
            return [{"id": "j2", "source_id": "s1", "status": self.status,
                     "attempt_count": 2, "chunk_count": 4, "indexed_count": 4, "error_code": None},
                    {"id": "j1", "source_id": "s1", "status": "failed", "attempt_count": 1,
                     "error_code": "parse_error"}]
        raise AssertionError(path)


def _root(tmp_path, with_file=True):
    course = tmp_path / "physics"
    course.mkdir()
    if with_file:
        (course / "notes.pdf").write_bytes(b"pdf")
    with (tmp_path / "courses.csv").open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=["course_id", "relative_directory", "notebook_id"])
        writer.writeheader()
        writer.writerow({"course_id": "physics", "relative_directory": "physics", "notebook_id": NOTEBOOK})
    return tmp_path


def test_matched_indexed_source_and_retry_history(tmp_path):
    root = _root(tmp_path)
    import hashlib
    api = Api(hashlib.sha256(b"pdf").hexdigest())
    report = inspect_course_sources(root, api)
    course = report["courses"][0]
    assert course["status"] == "matched"
    assert course["indexed_eligible_count"] == 1
    assert course["sources"][0]["latest_job"]["id"] == "j2"
    assert len(course["sources"][0]["job_attempts"]) == 2


def test_failed_source_is_not_eligible(tmp_path):
    root = _root(tmp_path)
    import hashlib
    course = inspect_course_sources(root, Api(hashlib.sha256(b"pdf").hexdigest(), status="failed"))["courses"][0]
    assert course["sources"][0]["evidence_eligible"] is False


def test_unmatched_hash_requires_review(tmp_path):
    root = _root(tmp_path)
    course = inspect_course_sources(root, Api("a" * 64))["courses"][0]
    assert course["status"] == "needs_review"
    assert course["missing_files"] == ["physics/notes.pdf"]


def test_without_notebook_mapping_makes_no_calls(tmp_path):
    root = tmp_path / "physics"
    root.mkdir()
    (root / "notes.pdf").write_bytes(b"pdf")
    api = Api("a" * 64)
    report = inspect_course_sources(tmp_path, api)
    assert report["courses"][0]["status"] == "notebook_mapping_required"
    assert api.paths == []
