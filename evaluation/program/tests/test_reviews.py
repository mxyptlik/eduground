from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from evaluation.program.reviews import KIND, ReviewError, js_digest, release_packet, validate_bundle, write_release
from evaluation.program.db_evidence import DatabaseEvidenceResolver


ROOT = Path(__file__).resolve().parents[3]


class Resolver:
    def verify_source(self, manifest):
        if manifest["sha256"] != "a" * 64:
            raise ReviewError("Source checksum changed")

    def chunk(self, chunk_id):
        if chunk_id == "c1":
            return "s1", "LIFO means last-in, first-out."
        raise ReviewError("Missing chunk")


def sample_bundle():
    dataset = {
        "dataset_id": "test", "version": "v1", "source_manifest": [
            {"source_id": "s1", "source_label": "Slides", "sha256": "a" * 64, "notebook_id": "n1"}
        ],
        "samples": [{"sample_id": "q1", "question": "What does LIFO mean?", "reference_answer": "Last in, first out.",
                     "reference_contexts": ["LIFO means last-in, first-out."], "source_ids": ["s1"],
                     "source_chunk_id": "c1", "review_status": "approved"},
                    {"sample_id": "q2", "question": "How is it applied?", "reference_answer": "Use a stack.",
                     "reference_contexts": ["LIFO means last-in, first-out."], "source_ids": ["s1"],
                     "source_chunk_id": "c1", "review_status": "approved"}],
    }
    h = js_digest(dataset)
    return {"kind": KIND, "packets": [{"id": h, "dataset_hash": h, "course_id": "CSC442", "dataset": dataset,
                                      "reviews": [], "history": []}]}


def accept_first(bundle):
    p = bundle["packets"][0]
    row = p["dataset"]["samples"][0]
    r = {"sample_id": "q1", "question": row["question"], "reference_answer": row["reference_answer"],
         "supporting_passages": row["reference_contexts"][0], "required_changes": "", "reviewer": "Human Reviewer",
         "decision": "Accept", "reviewed_at_utc": "2026-09-30T00:00:00.000Z", "evidence_checked": True,
         "revision": 1}
    r["content_hash"] = js_digest({k: r[k] for k in ("question", "reference_answer", "supporting_passages")})
    p["reviews"] = [r]
    p["history"] = [dict(r)]
    return r


def test_legacy_automated_approval_never_enters_release():
    bundle = sample_bundle()
    validate_bundle(bundle)
    with pytest.raises(ReviewError, match="No human-accepted"):
        release_packet(bundle["packets"][0], Resolver())


def test_one_reviewed_question_releases_with_provenance_and_counts(tmp_path):
    bundle = sample_bundle()
    accept_first(bundle)
    release = release_packet(bundle["packets"][0], Resolver())
    assert [s.sample_id for s in release.dataset.samples] == ["q1"]
    assert release.counts == {"Pending": 1, "Accept": 1, "Flag": 0, "Reject": 0}
    assert release.dataset.samples[0].integrity == "verified"
    destination = tmp_path / "release"
    write_release(release, destination)
    data = (destination / "dataset.json").read_bytes()
    assert sha256(data).hexdigest() == (destination / "dataset.sha256").read_text().strip()
    assert json.loads((destination / "provenance.json").read_text())["identity_assurance"].startswith("reviewer self-reported")
    with pytest.raises(FileExistsError):
        write_release(release, destination)


def test_changed_evidence_must_match_live_chunk():
    bundle = sample_bundle()
    r = accept_first(bundle)
    r["supporting_passages"] = "Fabricated assertion."
    r["content_hash"] = js_digest({k: r[k] for k in ("question", "reference_answer", "supporting_passages")})
    bundle["packets"][0]["history"][0] = dict(r)
    with pytest.raises(ReviewError, match="not contained"):
        release_packet(bundle["packets"][0], Resolver())


def test_current_review_must_be_latest_history_revision():
    bundle = sample_bundle()
    r = accept_first(bundle)
    later = dict(r, revision=2, decision="Pending", evidence_checked=False)
    bundle["packets"][0]["history"].append(later)
    with pytest.raises(ReviewError, match="latest"):
        validate_bundle(bundle)


def test_existing_browser_dataset_hash_agrees_with_python():
    path = ROOT / "evaluation/ragas/datasets/eduground_100q.gemini-v1.draft.json"
    py_hash = js_digest(json.loads(path.read_text(encoding="utf-8")))
    result = subprocess.run(
        ["node", "--input-type=module", "-e",
         "import {readFileSync} from 'node:fs'; import {digest} from './tools/evaluation/review/model.mjs'; "
         "console.log(await digest(JSON.parse(readFileSync(process.argv[1],'utf8'))))", str(path)],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == py_hash


def test_database_resolver_rejects_changed_source_and_wrong_notebook(monkeypatch):
    import sys
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from app.models.content import Chunk, Source, SourceVersion
    from app.models.enums import SourceStatus

    source = SimpleNamespace(id="s1", deleted_at=None, status=SourceStatus.INDEXED,
                             notebook_id="n1", checksum_sha256="a" * 64, version_number=1)
    chunk = SimpleNamespace(source_id="s1", source_version_id="v1", text="LIFO means last-in, first-out.")
    version = SimpleNamespace(id="v1", source_id="s1", version_number=1, checksum_sha256="a" * 64,
                              status=SourceStatus.INDEXED.value)

    class Query:
        def filter(self, *conditions):
            return self

        def all(self):
            return [version]

    class Session:
        def get(self, model, key):
            return source if model is Source else chunk if model is Chunk else None

        def query(self, model):
            assert model is SourceVersion
            return Query()

    resolver = DatabaseEvidenceResolver(Session(), notebook_id="n1")
    manifest = sample_bundle()["packets"][0]["dataset"]["source_manifest"][0]
    resolver.verify_source(manifest)
    assert resolver.chunk("c1")[0] == "s1"
    chunk.source_version_id = "old-version"
    with pytest.raises(ReviewError, match="current version"):
        resolver.chunk("c1")
    chunk.source_version_id = "v1"
    source.checksum_sha256 = "b" * 64
    with pytest.raises(ReviewError, match="checksum changed"):
        resolver.verify_source(manifest)
    source.checksum_sha256 = "a" * 64
    source.notebook_id = "other"
    with pytest.raises(ReviewError, match="outside"):
        resolver.verify_source(manifest)


def test_v2_evidence_import_can_be_human_released():
    dataset = {
        "schema_version": 2, "dataset_id": "draft-2", "version": "1", "course_id": "physics",
        "notebook_id": "n1", "source_manifest": [
            {"source_id": "s1", "source_label": "Slides", "sha256": "a" * 64, "notebook_id": "n1"}
        ],
        "samples": [{"sample_id": "q1", "question": "What does LIFO mean?",
            "reference_answer": "Last in, first out.", "answerable": True,
            "evidence": [{"source_id": "s1", "chunk_id": "c1", "quote": "LIFO means last-in, first-out."}],
            "integrity": "verified"}],
    }
    digest = js_digest(dataset)
    packet = {"id": digest, "dataset_hash": digest, "course_id": "physics", "dataset": dataset,
              "reviews": [], "history": []}
    review = {"sample_id": "q1", "question": "What does LIFO mean?",
              "reference_answer": "Last in, first out.",
              "supporting_passages": "LIFO means last-in, first-out.",
              "required_changes": "", "reviewer": "Human", "decision": "Accept",
              "reviewed_at_utc": "2026-09-30T00:00:00Z", "evidence_checked": True, "revision": 1}
    review["content_hash"] = js_digest({key: review[key] for key in
                                        ("question", "reference_answer", "supporting_passages")})
    packet["reviews"] = [review]
    packet["history"] = [dict(review)]
    released = release_packet(packet, Resolver())
    assert released.dataset.course_id == "physics"
    assert released.dataset.samples[0].evidence[0].chunk_id == "c1"


def test_v2_multiline_evidence_keeps_each_original_chunk_binding():
    dataset = {
        "schema_version": 2, "dataset_id": "draft-3", "version": "1", "course_id": "physics",
        "notebook_id": "n1", "source_manifest": [
            {"source_id": "s1", "source_label": "Slides", "sha256": "a" * 64, "notebook_id": "n1"}],
        "samples": [{"sample_id": "q1", "question": "What do both passages say?",
                     "reference_answer": "They describe LIFO.", "answerable": True,
                     "evidence": [
                         {"source_id": "s1", "chunk_id": "c1", "quote": "LIFO means\n\nlast-in"},
                         {"source_id": "s1", "chunk_id": "c1", "quote": "first-out."}]}],
    }
    digest = js_digest(dataset)
    packet = {"id": digest, "dataset_hash": digest, "course_id": "physics", "dataset": dataset,
              "reviews": [], "history": []}
    review = {"sample_id": "q1", "question": "What do both passages say?",
              "reference_answer": "They describe LIFO.",
              "supporting_passages": "LIFO means\n\nlast-in\n\nfirst-out.",
              "supporting_evidence": [
                  {"source_id": "s1", "chunk_id": "c1", "quote": "LIFO means\n\nlast-in"},
                  {"source_id": "s1", "chunk_id": "c1", "quote": "first-out."}],
              "required_changes": "", "reviewer": "Human", "decision": "Accept",
              "reviewed_at_utc": "2026-09-30T00:00:00Z", "evidence_checked": True, "revision": 1}
    review["content_hash"] = js_digest({key: review[key] for key in
                                        ("question", "reference_answer", "supporting_passages", "supporting_evidence")})
    packet["reviews"] = [review]
    packet["history"] = [dict(review)]
    release = release_packet(packet, Resolver())
    assert [e.quote for e in release.dataset.samples[0].evidence] == ["LIFO means\n\nlast-in", "first-out."]
    changed = dict(review, supporting_evidence=[dict(review["supporting_evidence"][0], chunk_id="wrong"), review["supporting_evidence"][1]])
    changed["content_hash"] = js_digest({key: changed[key] for key in
                                         ("question", "reference_answer", "supporting_passages", "supporting_evidence")})
    packet["reviews"] = [changed]
    packet["history"] = [dict(changed)]
    with pytest.raises(ReviewError, match="source and chunk"):
        release_packet(packet, Resolver())


def test_v2_unanswerable_acceptance_has_no_invented_evidence():
    dataset = {
        "schema_version": 2, "dataset_id": "draft-4", "version": "1", "course_id": "physics",
        "notebook_id": "n1", "source_manifest": [
            {"source_id": "s1", "source_label": "Slides", "sha256": "a" * 64, "notebook_id": "n1"}],
        "samples": [{"sample_id": "q1", "question": "What is the secret password?",
                     "reference_answer": None, "answerable": False, "evidence": []}],
    }
    digest = js_digest(dataset)
    packet = {"id": digest, "dataset_hash": digest, "course_id": "physics", "dataset": dataset,
              "reviews": [], "history": []}
    review = {"sample_id": "q1", "question": "What is the secret password?",
              "reference_answer": "", "supporting_passages": "", "supporting_evidence": [],
              "required_changes": "", "reviewer": "Human", "decision": "Accept",
              "reviewed_at_utc": "2026-09-30T00:00:00Z", "evidence_checked": True, "revision": 1}
    review["content_hash"] = js_digest({key: review[key] for key in
                                        ("question", "reference_answer", "supporting_passages", "supporting_evidence")})
    packet["reviews"] = [review]
    packet["history"] = [dict(review)]
    released = release_packet(packet, Resolver())
    assert released.dataset.samples[0].answerable is False
    assert released.dataset.samples[0].evidence == ()
