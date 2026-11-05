"""Multi-file ingestion: shared embedding batches, per-file failure isolation."""

from __future__ import annotations

from app.pipelines import ingestion_pipeline as pipeline
from app.pipelines.ingestion_pipeline import BatchIngestionInput, run_ingestion_batch


def _input(filename: str, raw_bytes: bytes) -> BatchIngestionInput:
    return BatchIngestionInput(
        raw_bytes=raw_bytes,
        filename=filename,
        notebook_id="notebook-1",
        institution_id="institution-1",
        source_id=f"source-{filename}",
        source_version_id=f"version-{filename}",
    )


def _patch_indexing(monkeypatch, embed_calls, upserts):
    def fake_embed(texts, **kwargs):
        embed_calls.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(pipeline, "embed_texts", fake_embed)
    monkeypatch.setattr(pipeline, "ensure_collection", lambda size: None)
    monkeypatch.setattr(pipeline, "upsert_points", lambda points: upserts.append(list(points)))


def test_batch_ingestion_shares_one_embedding_call_across_files(monkeypatch, markdown_source_bytes: bytes):
    embed_calls: list[list[str]] = []
    upserts: list[list[dict]] = []
    _patch_indexing(monkeypatch, embed_calls, upserts)

    results = run_ingestion_batch([_input("a.md", markdown_source_bytes),
                                   _input("b.md", markdown_source_bytes)])

    assert [result.status for result in results] == ["indexed", "indexed"]
    assert len(embed_calls) == 1, "both files must share one embedding request"
    assert len(embed_calls[0]) == sum(len(result.chunks) for result in results)
    assert len(upserts) == 2, "indexing stays per file"
    assert all(result.point_ids for result in results)
    assert len({point["payload"]["source_id"] for point in upserts[0]}) == 1


def test_batch_ingestion_splits_embedding_calls_at_the_bound(monkeypatch, markdown_source_bytes: bytes):
    embed_calls: list[list[str]] = []
    upserts: list[list[dict]] = []
    _patch_indexing(monkeypatch, embed_calls, upserts)

    results = run_ingestion_batch([_input("a.md", markdown_source_bytes),
                                   _input("b.md", markdown_source_bytes)], max_embed_batch=1)

    assert [result.status for result in results] == ["indexed", "indexed"]
    assert len(embed_calls) > 1
    assert all(len(call) == 1 for call in embed_calls)


def test_batch_ingestion_isolates_a_broken_file(monkeypatch, markdown_source_bytes: bytes):
    embed_calls: list[list[str]] = []
    upserts: list[list[dict]] = []
    _patch_indexing(monkeypatch, embed_calls, upserts)

    results = run_ingestion_batch([_input("broken.xyz", b"garbage"),
                                   _input("good.md", markdown_source_bytes)])

    assert results[0].status == "failed"
    assert "Unsupported source format" in (results[0].error or "")
    assert results[1].status == "indexed"
    assert len(embed_calls) == 1 and len(embed_calls[0]) == len(results[1].chunks)
    assert len(upserts) == 1


def test_batch_ingestion_rejects_an_invalid_bound():
    import pytest

    with pytest.raises(ValueError, match="max_embed_batch"):
        run_ingestion_batch([], max_embed_batch=0)
    assert run_ingestion_batch([]) == []
