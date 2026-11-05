"""Behavioural checks for read-only multi-course intake inventory."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evaluation.program.corpus_inventory import InventoryError, inventory_courses, load_courses


REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "tools" / "evaluation" / "inventory_courses.py"


class CourseInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("CSC442", "CSC101"):
            (self.root / name).mkdir()

    def write_csv(self, rows: list[dict[str, str]]) -> None:
        with (self.root / "courses.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "course_id", "title", "relative_directory", "notebook_id", "reviewer", "notes"
            ])
            writer.writeheader()
            writer.writerows(rows)

    def test_inventory_realistic_directory_is_read_only_and_detects_duplicates(self) -> None:
        rows = [
            {"course_id": "CSC442", "title": "Machine Learning", "relative_directory": "CSC442"},
            {"course_id": "CSC101", "title": "Introduction", "relative_directory": "CSC101", "notebook_id": "notebook-1"},
        ]
        self.write_csv(rows)
        lecture = b"%PDF-1.7\nlecture source\n"
        (self.root / "CSC442" / "lecture.pdf").write_bytes(lecture)
        (self.root / "CSC101" / "same.PDF").write_bytes(lecture)
        (self.root / "CSC442" / "notes.markdown").write_text("# Notes", encoding="utf-8")
        (self.root / "CSC101" / "figure.png").write_bytes(b"image")
        (self.root / "CSC101" / "empty.txt").touch()
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

        manifest = inventory_courses(self.root)

        after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(manifest["dry_run"])
        self.assertEqual(manifest["summary"], {
            "course_count": 2, "file_count": 5, "candidate_count": 2,
            "empty_count": 1, "unsupported_count": 2, "duplicate_group_count": 1,
        })
        self.assertEqual(manifest["duplicates"][0]["sha256"], hashlib.sha256(lecture).hexdigest())
        self.assertEqual(manifest["duplicates"][0]["course_ids"], ["CSC101", "CSC442"])
        self.assertEqual({item["suffix"] for item in manifest["documents"] if item["supported"]},
                         {".pdf", ".txt"})
        markdown = next(item for item in manifest["documents"] if item["suffix"] == ".markdown")
        self.assertIsNone(markdown["source_type"])
        self.assertEqual(inventory_courses(self.root), manifest)

    def test_inferred_ids_sanitize_messy_directory_names_without_renaming_files(self) -> None:
        (self.root / "Architectures & Organisation").mkdir()
        (self.root / "C#").mkdir()
        (self.root / "C++").mkdir()
        (self.root / "Architectures & Organisation" / "lecture.pdf").write_bytes(b"%PDF-1.7\n")
        (self.root / "Architectures & Organisation" / "old.ppt").write_bytes(b"legacy")

        manifest = inventory_courses(self.root)

        inferred = {course["relative_directory"]: course for course in manifest["courses"]
                    if course["relative_directory"] in {"Architectures & Organisation", "C#", "C++"}}
        self.assertEqual(set(inferred), {"Architectures & Organisation", "C#", "C++"})
        ids = [inferred[name]["course_id"] for name in inferred]
        self.assertEqual(len(set(ids)), len(ids), ids)
        for course_id in ids:
            self.assertRegex(course_id, r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
        self.assertEqual(inferred["Architectures & Organisation"]["title"], "Architectures & Organisation")
        self.assertTrue(manifest["dry_run"])
        self.assertEqual(manifest["summary"]["unsupported_count"], 1)

    def test_cli_emits_dry_run_json_and_no_upload_side_effects(self) -> None:
        (self.root / "CSC442" / "unit.docx").write_bytes(b"doc")
        process = subprocess.run(
            [sys.executable, str(CLI), str(self.root)], capture_output=True, text=True, check=False
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        output = json.loads(process.stdout)
        self.assertTrue(output["dry_run"])
        self.assertEqual(output["summary"]["course_count"], 2)
        self.assertEqual(output["summary"]["candidate_count"], 1)
        self.assertEqual(len(list(self.root.iterdir())), 2)

    def test_json_registry_accepts_existing_notebook_mapping_without_directory(self) -> None:
        registry = self.root / "registry.json"
        registry.write_text(json.dumps({"courses": [
            {"course_id": "CSC442", "relative_directory": "CSC442"},
            {"course_id": "HIS220", "notebook_id": "existing-notebook"},
        ]}), encoding="utf-8")
        manifest = inventory_courses(self.root, registry)
        self.assertEqual(manifest["summary"]["course_count"], 2)
        self.assertEqual(manifest["courses"][1]["notebook_id"], "existing-notebook")
        self.assertIsNone(manifest["courses"][1]["relative_directory"])

    def test_bad_rows_and_escaping_directories_are_rejected(self) -> None:
        cases = [
            ([{"course_id": "CSC442", "relative_directory": "CSC442"},
              {"course_id": "csc442", "relative_directory": "CSC101"}], "duplicate course_id"),
            ([{"course_id": "CSC442", "relative_directory": "CSC442"},
              {"course_id": "CSC101", "relative_directory": "CSC442"}], "assigned twice"),
            ([{"course_id": "CSC442", "relative_directory": "../elsewhere"}], "stay inside"),
            ([{"course_id": "CSC442", "relative_directory": str(self.root / "CSC442")}], "stay inside"),
            ([{"course_id": "bad code", "relative_directory": "CSC442"}], "invalid course_id"),
            ([{"course_id": "CSC442", "relative_directory": ""}], "provide relative_directory"),
        ]
        for rows, message in cases:
            with self.subTest(message=message):
                self.write_csv(rows)
                with self.assertRaisesRegex(InventoryError, message):
                    load_courses(self.root)

    def test_registry_rejects_ambiguous_notebook_and_non_string_json_fields(self) -> None:
        registry = self.root / "registry.json"
        registry.write_text(json.dumps([
            {"course_id": "CSC442", "notebook_id": "same"},
            {"course_id": "CSC101", "notebook_id": "same"},
        ]), encoding="utf-8")
        with self.assertRaisesRegex(InventoryError, "notebook_id is assigned"):
            load_courses(self.root, registry)
        registry.write_text(json.dumps([{"course_id": "CSC442", "notebook_id": 42}]), encoding="utf-8")
        with self.assertRaisesRegex(InventoryError, "fields must be strings"):
            load_courses(self.root, registry)

    def test_nested_course_directories_are_rejected(self) -> None:
        nested = self.root / "CSC442" / "CSC101"
        nested.mkdir()
        registry = self.root / "registry.json"
        registry.write_text(json.dumps([
            {"course_id": "CSC442", "relative_directory": "CSC442"},
            {"course_id": "CSC101", "relative_directory": "CSC442/CSC101"},
        ]), encoding="utf-8")
        with self.assertRaisesRegex(InventoryError, "Nested course directories"):
            inventory_courses(self.root, registry)

    def test_linked_source_outside_root_is_rejected_if_supported_by_host(self) -> None:
        outside = self.root.parent / (self.root.name + "-outside.txt")
        outside.write_text("private source", encoding="utf-8")
        self.addCleanup(outside.unlink)
        link = self.root / "CSC442" / "outside.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Host does not permit file symlinks")
        with self.assertRaisesRegex(InventoryError, "Linked paths are unsupported"):
            inventory_courses(self.root)

    def test_cli_bad_registry_exits_nonzero_without_json_manifest(self) -> None:
        self.write_csv([{"course_id": "CSC442", "relative_directory": "../escape"}])
        process = subprocess.run(
            [sys.executable, str(CLI), str(self.root)], capture_output=True, text=True, check=False
        )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stdout, "")
        self.assertIn("stay inside", process.stderr)


if __name__ == "__main__":
    unittest.main()
