"""Read-only course registry and source-file inventory for evaluation intake.

This module discovers input files; it does not create notebooks or ingest sources.
Its manifest is a proposal that later intake code must verify again before upload.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path, PureWindowsPath
from typing import Any


# SourceService.ALLOWED_EXTENSIONS is narrower than the worker parser: the API
# currently refuses .markdown even though the worker can parse its contents.
# Recognition here does not assert that extraction succeeds or preserves layout.
SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx", ".pptx", ".md", ".txt"})
SOURCE_TYPES = {suffix: suffix.lstrip(".") for suffix in SUPPORTED_SUFFIXES}
COURSE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
REGISTRY_FIELDS = frozenset({"course_id", "title", "relative_directory", "notebook_id", "reviewer", "notes"})


class InventoryError(ValueError):
    """An input registry or course tree cannot be inventoried safely."""


def _suggest_course_id(directory_name: str) -> str:
    """Sanitize a directory name into a candidate course ID.

    The result is a proposal only; reviewers confirm or replace it in
    ``courses.csv`` before upload. The original directory name is kept as the
    title so nothing is silently renamed.
    """
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", directory_name.strip()).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug or not re.match(r"[A-Za-z0-9]", slug):
        slug = f"course-{slug}" if slug else "course"
    return slug[:64]


def _contained(path: Path, root: Path, *, label: str) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise InventoryError(f"{label} escapes the course root: {path}")
    return resolved


def _course_directory(root: Path, value: str, *, label: str) -> Path:
    raw = value.strip()
    if not raw:
        raise InventoryError(f"{label}: relative_directory is required without notebook_id")
    portable = PureWindowsPath(raw)
    if portable.is_absolute() or portable.drive or ".." in portable.parts:
        raise InventoryError(f"{label}: relative_directory must stay inside the course root")
    path = root / raw
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise InventoryError(f"{label}: linked course directories are unsupported")
    resolved = _contained(path, root, label=label)
    if resolved == root or not resolved.is_dir():
        raise InventoryError(f"{label}: relative_directory must name a course directory")
    return resolved


def _load_registry(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "course_id" not in reader.fieldnames:
                raise InventoryError("CSV registry requires a course_id column")
            unknown = set(reader.fieldnames) - REGISTRY_FIELDS
            if unknown:
                raise InventoryError(f"CSV registry has unknown columns: {', '.join(sorted(unknown))}")
            rows = list(reader)
    elif path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("courses") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise InventoryError("JSON registry must be a course list or an object with a courses list")
    else:
        raise InventoryError("Registry must be .csv or .json")
    if not rows:
        raise InventoryError("Course registry contains no rows")
    return rows


def load_courses(root: Path, registry: Path | None = None) -> tuple[list[dict[str, str | None]], Path | None]:
    """Validate a registry, or infer IDs from immediate course subdirectories."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise InventoryError(f"Course root is not a directory: {root}")
    if registry is None:
        default = root / "courses.csv"
        registry = default if default.is_file() else None
    if registry is not None:
        registry = registry.resolve(strict=True)
        rows = _load_registry(registry)
    else:
        rows = []
        seen_ids: set[str] = set()
        for child in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
            if not child.is_dir():
                continue
            base = _suggest_course_id(child.name)
            candidate, serial = base, 1
            while candidate.casefold() in seen_ids:
                serial += 1
                candidate = f"{base[:56]}-{serial}"
            seen_ids.add(candidate.casefold())
            rows.append({
                "course_id": candidate,
                "title": child.name,
                "relative_directory": child.name,
                "notes": "course_id inferred from the directory name; confirm or replace it in courses.csv",
            })
        if not rows:
            raise InventoryError("No course directories found; provide courses.csv or --registry")

    courses: list[dict[str, str | None]] = []
    ids: set[str] = set()
    directories: set[str] = set()
    notebook_ids: set[str] = set()
    for number, row in enumerate(rows, start=2 if registry and registry.suffix.lower() == ".csv" else 1):
        label = f"registry row {number}"
        if not isinstance(row, dict) or any(key not in REGISTRY_FIELDS for key in row):
            raise InventoryError(f"{label}: expected a course object with known fields")
        if any(not isinstance(value, str) and value is not None for value in row.values()):
            raise InventoryError(f"{label}: fields must be strings")
        course_id = (row.get("course_id") or "").strip()
        if not COURSE_ID_RE.fullmatch(course_id):
            raise InventoryError(f"{label}: invalid course_id {course_id!r}")
        if course_id.casefold() in ids:
            raise InventoryError(f"{label}: duplicate course_id {course_id!r}")
        ids.add(course_id.casefold())
        notebook_id = (row.get("notebook_id") or "").strip() or None
        if notebook_id:
            if notebook_id in notebook_ids:
                raise InventoryError(f"{label}: notebook_id is assigned to more than one course")
            notebook_ids.add(notebook_id)
        relative = (row.get("relative_directory") or "").strip()
        if not relative and not notebook_id:
            raise InventoryError(f"{label}: provide relative_directory or notebook_id")
        if relative:
            directory = _course_directory(root, relative, label=label)
            if directory.as_posix().casefold() in directories:
                raise InventoryError(f"{label}: course directory is assigned twice: {relative}")
            directories.add(directory.as_posix().casefold())
            relative = directory.relative_to(root).as_posix()
        courses.append({
            "course_id": course_id,
            "title": (row.get("title") or "").strip() or course_id,
            "relative_directory": relative or None,
            "notebook_id": notebook_id,
            "reviewer": (row.get("reviewer") or "").strip() or None,
            "notes": (row.get("notes") or "").strip() or None,
        })
    # Prevent a file under a nested course directory from being counted twice.
    paths = [root / c["relative_directory"] for c in courses if c["relative_directory"]]
    for a in paths:
        for b in paths:
            if a != b and a.is_relative_to(b):
                raise InventoryError(f"Nested course directories are ambiguous: {a} and {b}")
    return courses, registry


def _hash_file(path: Path) -> tuple[str, int]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise InventoryError(f"File changed during hashing: {path}")
    return digest.hexdigest(), after.st_size


def inventory_courses(root: Path, registry: Path | None = None) -> dict[str, Any]:
    """Return a deterministic, read-only dry-run manifest for course intake."""
    root = root.resolve(strict=True)
    courses, registry_path = load_courses(root, registry)
    documents: list[dict[str, Any]] = []
    by_hash: dict[str, list[dict[str, Any]]] = {}
    for course in courses:
        relative = course["relative_directory"]
        if not relative:
            continue  # Existing notebook mapping; checked by the later adoption task.
        directory = root / relative
        for path in sorted(directory.rglob("*"), key=lambda p: p.as_posix().casefold()):
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise InventoryError(f"Linked paths are unsupported: {path}")
            _contained(path, root, label="Course file")
            if not path.is_file():
                continue
            sha256, byte_size = _hash_file(path)
            suffix = path.suffix.lower()
            item = {
                "course_id": course["course_id"],
                "relative_path": path.relative_to(root).as_posix(),
                "suffix": suffix,
                "source_type": SOURCE_TYPES.get(suffix),
                "byte_size": byte_size,
                "sha256": sha256,
                "supported": suffix in SUPPORTED_SUFFIXES,
                "status": "empty" if byte_size == 0 else ("candidate" if suffix in SUPPORTED_SUFFIXES else "unsupported"),
            }
            documents.append(item)
            by_hash.setdefault(sha256, []).append(item)
    duplicate_groups = [
        {"sha256": digest, "paths": [item["relative_path"] for item in items],
         "course_ids": sorted({item["course_id"] for item in items})}
        for digest, items in sorted(by_hash.items()) if len(items) > 1
    ]
    return {
        "schema_version": 1,
        "dry_run": True,
        "root": str(root),
        "registry": str(registry_path) if registry_path else None,
        "courses": courses,
        "documents": documents,
        "duplicates": duplicate_groups,
        "summary": {
            "course_count": len(courses),
            "file_count": len(documents),
            "candidate_count": sum(item["status"] == "candidate" for item in documents),
            "empty_count": sum(item["byte_size"] == 0 for item in documents),
            "unsupported_count": sum(not item["supported"] for item in documents),
            "duplicate_group_count": len(duplicate_groups),
        },
    }
