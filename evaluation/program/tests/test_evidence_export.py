from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import importlib
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from evaluation.program.evidence_export import EvidenceExportError, export_course_evidence, write_evidence_packet


API_ROOT = Path(__file__).resolve().parents[3] / "apps" / "api"


@pytest.fixture
def db():
    sys.path.insert(0, str(API_ROOT))
    from app.db.base import Base
    for name in ("identity", "curriculum", "content", "operations", "tutoring", "learning"):
        importlib.import_module("app.models." + name)
    from app.models.content import Chunk, Source, SourceVersion
    from app.models.curriculum import Notebook
    from app.models.enums import NotebookVisibility, PolicyMode, SourceOrigin, SourceStatus, SourceType

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        notebook = Notebook(id="n1", institution_id="i1", title="Course", owner_user_id="u1",
                            visibility=NotebookVisibility.PRIVATE, policy_mode=PolicyMode.TEACHING)
        source = Source(id="s1", notebook_id="n1", source_type=SourceType.PDF, title="Lecture",
                        original_filename="lecture.pdf", storage_key="k", mime_type="application/pdf",
                        checksum_sha256="a" * 64, byte_size=100, version_number=2,
                        status=SourceStatus.INDEXED, source_origin=SourceOrigin.UPLOAD,
                        created_by_user_id="u1")
        version = SourceVersion(id="v2", source_id="s1", version_number=2, storage_key="k",
                                checksum_sha256="a" * 64, status="indexed", created_at=now,
                                created_by_user_id="u1")
        current = Chunk(id="c2", source_id="s1", source_version_id="v2", chunk_index=0,
                        token_count=5, char_count=26, text="Current evidence paragraph.",
                        normalized_text="Current evidence paragraph.", qdrant_point_id="p2", created_at=now,
                        start_page=1, end_page=1, heading_path=["Topic"])
        old = Chunk(id="c1", source_id="s1", source_version_id="v1", chunk_index=0,
                    token_count=4, char_count=20, text="Old, obsolete evidence.",
                    normalized_text="Old, obsolete evidence.", qdrant_point_id="p1", created_at=now)
        session.add_all([notebook, source, version, current, old])
        session.commit()
        yield session
    engine.dispose()


def test_export_only_current_version_and_exact_original_hash(db, tmp_path):
    packet = export_course_evidence(db, course_id="physics", notebook_id="n1", expected_sha256={"a" * 64})
    assert packet["corpus_membership"] == "matched_to_expected_hashes"
    assert [chunk["chunk_id"] for chunk in packet["sources"][0]["chunks"]] == ["c2"]
    assert packet["sources"][0]["chunks"][0]["start_page"] == 1
    assert packet["provenance"]["actual_ingestion_embedding_model"] is None
    target = tmp_path / "packet.json"
    digest = write_evidence_packet(packet, target)
    assert digest == sha256(target.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        write_evidence_packet(packet, target)


def test_export_rejects_missing_expected_corpus_and_empty_chunks(db):
    with pytest.raises(EvidenceExportError, match="No eligible"):
        export_course_evidence(db, course_id="physics", notebook_id="n1", expected_sha256={"b" * 64})
    from app.models.content import Chunk
    db.query(Chunk).filter(Chunk.id == "c2").delete()
    db.commit()
    with pytest.raises(EvidenceExportError, match="missing or empty"):
        export_course_evidence(db, course_id="physics", notebook_id="n1", expected_sha256={"a" * 64})
