"""Queue-level ingestion batching: buffering, flush, shared embeddings."""

from __future__ import annotations

import pytest

from app.jobs import batching
from app.jobs import ingestion as ingestion_jobs
from app.services import embeddings
from app.services.embeddings import EmbeddingBatchSession, embedding_batch_session


class FakeRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}
        self.values: dict[str, str] = {}

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lpop(self, key, count):
        items = self.lists.get(key, [])[:count]
        self.lists[key] = self.lists.get(key, [])[count:]
        return items or []

    def llen(self, key):
        return len(self.lists.get(key, []))

    def expire(self, key, ttl):
        self.expires[key] = ttl

    def set(self, key, value, nx=False, px=None):
        if nx and key in self.values:
            return False
        self.values[key] = str(value)
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)
        self.lists.pop(key, None)
        return 1

    def lrem(self, key, count, value):
        rows = self.lists.get(key, [])
        if value in rows:
            rows.remove(value)
            return 1
        return 0

    def eval(self, script, count, *args):
        if script == batching.CLAIM_BATCH_LUA:
            source, claimed, limit = args
            rows = self.lpop(source, limit) or []
            for row in rows:
                self.rpush(claimed, row)
            return rows
        if script == batching.RELEASE_LOCK_LUA:
            key, token = args
            if self.values.get(key) == token:
                return self.delete(key)
            return 0
        if script == batching.RENEW_LOCK_LUA:
            return 1
        raise AssertionError("Unknown Redis script")


def test_buffer_and_take_roundtrip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(batching, "_redis", lambda: fake)
    ref = {"job_id": "j1", "source_id": "s1", "source_version_id": "v1", "institution_id": "i1"}
    assert batching.buffer_reference("i1:n1", ref) == 1
    assert batching.buffer_reference("i1:n1", {**ref, "job_id": "j2"}) == 2
    assert batching.queue_depth("i1:n1") == 2
    assert batching.take_batch("i1:n1", 1) == [ref]
    assert batching.queue_depth("i1:n1") == 1


def test_enabled_actor_buffers_and_schedules_one_flush(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(batching, "_redis", lambda: fake)
    scheduled = []
    monkeypatch.setattr(batching, "schedule_flush",
                        lambda key, delay_ms=0: scheduled.append((key, delay_ms)))
    monkeypatch.setattr(ingestion_jobs, "load_ingestion_context",
                        lambda **kwargs: type("Ctx", (), {"notebook_id": "n1"})())
    monkeypatch.setattr(ingestion_jobs.settings, "ingestion_batch_enabled", True)
    monkeypatch.setattr(ingestion_jobs.settings, "ingestion_batch_window_seconds", 5)
    monkeypatch.setattr(ingestion_jobs, "get_object_bytes",
                        lambda key: (_ for _ in ()).throw(AssertionError("must not process while buffering")))

    assert ingestion_jobs.process_source_ingestion_job.fn("j1", "s1", "v1", "i1") is None
    assert ingestion_jobs.process_source_ingestion_job.fn("j2", "s2", "v2", "i1") is None
    assert scheduled == [("i1:n1", 5000), ("i1:n1", 5000)], \
        "every buffered file schedules a flush; duplicates are harmless"
    assert batching.queue_depth("i1:n1") == 2


def test_flush_shares_one_embedding_call_and_runs_jobs_in_session(monkeypatch):
    from app import telemetry

    telemetry.reset_test_state()
    fake = FakeRedis()
    monkeypatch.setattr(batching, "_redis", lambda: fake)
    refs = [{"job_id": "j1", "source_id": "s1", "source_version_id": "v1", "institution_id": "i1"},
            {"job_id": "j2", "source_id": "s2", "source_version_id": "v2", "institution_id": "i1"}]
    for ref in refs:
        batching.buffer_reference("i1:n1", ref)
    monkeypatch.setattr(batching, "_prepared_texts",
                        lambda ref, **kwargs: [ref["source_id"] + "-a", ref["source_id"] + "-b"])
    embed_calls = []

    def fake_embed(texts, *, guard=None):
        embed_calls.append(list(texts))
        return [[0.1] for _ in texts]

    monkeypatch.setattr(batching, "embed_texts", fake_embed)
    job_calls = []

    def fake_job(job_id, source_id, source_version_id, institution_id, **kwargs):
        assert kwargs.get("direct") is True
        session = embeddings._BATCH_SESSION.get()
        assert session is not None, "per-source jobs must run inside the batch session"
        assert session.lookup([source_id + "-a", source_id + "-b"]) is not None
        job_calls.append(source_id)

    monkeypatch.setattr(ingestion_jobs.process_source_ingestion_job, "fn", fake_job)
    monkeypatch.setattr(batching, "schedule_flush", lambda key, delay_ms=0: None)

    summary = batching.flush_ingestion_batch.fn("i1:n1")

    assert summary["files"] == 2 and summary["prewarmed_files"] == 2
    assert len(embed_calls) == 1 and len(embed_calls[0]) == 4, "both files share one embedding request"
    assert job_calls == ["s1", "s2"]
    assert batching.queue_depth("i1:n1") == 0
    stages = {event.attributes["stage"] for event in telemetry.get_metric_events()
              if "stage" in event.attributes}
    assert {"batch.preprocess", "batch.embed", "batch.write"} <= stages
    assert summary["workers"] == 2 and summary["preprocess_ms"] >= 0 and summary["embed_ms"] >= 0


def test_interrupted_batch_keeps_claim_for_manual_recovery(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(batching, "_redis", lambda: fake)
    ref = {"job_id": "j1", "source_id": "s1", "source_version_id": "v1", "institution_id": "i1"}
    batching.buffer_reference("i1:n1", ref)
    monkeypatch.setattr(batching, "_prepared_texts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(batching, "schedule_flush", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingestion_jobs.process_source_ingestion_job, "fn",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("worker stopped")))
    with pytest.raises(RuntimeError, match="worker stopped"):
        batching.flush_ingestion_batch.fn("i1:n1")
    assert batching.claimed_count("i1:n1") == 1
    assert batching.queue_depth("i1:n1") == 0
    assert batching.flush_ingestion_batch.fn("i1:n1") == {
        "manual_recovery_required": True, "claimed_count": 1,
    }


def test_flush_requeues_when_more_files_remain(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(batching, "_redis", lambda: fake)
    for index in range(3):
        batching.buffer_reference("i1:n1", {"job_id": f"j{index}", "source_id": f"s{index}",
                                            "source_version_id": f"v{index}", "institution_id": "i1"})
    monkeypatch.setattr(batching.settings, "ingestion_batch_max_files", 2)
    monkeypatch.setattr(batching, "_prepared_texts", lambda ref, **kwargs: [])
    monkeypatch.setattr(batching, "embed_texts", lambda texts, *, guard=None: [])
    monkeypatch.setattr(ingestion_jobs.process_source_ingestion_job, "fn", lambda *args, **kwargs: None)
    scheduled = []
    monkeypatch.setattr(batching, "schedule_flush", lambda key, delay_ms=0: scheduled.append(key))

    summary = batching.flush_ingestion_batch.fn("i1:n1")

    assert summary["files"] == 2 and summary["remaining"] == 1
    assert scheduled[-1] == "i1:n1", "leftover files are re-flushed immediately"


def test_flush_waits_for_quiet_period_after_the_last_upload(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(batching, "_redis", lambda: fake)
    ref = {"job_id": "j1", "source_id": "s1", "source_version_id": "v1", "institution_id": "i1"}
    batching.buffer_reference("i1:n1", ref)
    batching.mark_batch_deadline("i1:n1", window_ms=5000)
    scheduled = []
    monkeypatch.setattr(batching, "schedule_flush",
                        lambda key, delay_ms=0: scheduled.append((key, delay_ms)))
    jobs = []
    monkeypatch.setattr(ingestion_jobs.process_source_ingestion_job, "fn",
                        lambda *args, **kwargs: jobs.append(args))

    premature = batching.flush_ingestion_batch.fn("i1:n1")
    assert premature["deferred_ms"] > 0, "an early flush defers instead of processing"
    assert batching.queue_depth("i1:n1") == 1 and not jobs
    assert scheduled and scheduled[-1][0] == "i1:n1"

    fake.values.pop("eduground:ingestion_batch:i1:n1:deadline")  # quiet period elapsed
    monkeypatch.setattr(batching, "_prepared_texts", lambda ref, **kwargs: [])
    done = batching.flush_ingestion_batch.fn("i1:n1")
    assert done["files"] == 1 and len(jobs) == 1 and batching.queue_depth("i1:n1") == 0


def test_preprocessing_runs_in_parallel_across_files(monkeypatch):
    import threading

    fake = FakeRedis()
    monkeypatch.setattr(batching, "_redis", lambda: fake)
    refs = [{"job_id": f"j{i}", "source_id": f"s{i}", "source_version_id": f"v{i}",
             "institution_id": "i1"} for i in range(2)]
    for ref in refs:
        batching.buffer_reference("i1:n1", ref)
    barrier = threading.Barrier(2, timeout=5)  # only passes when both files preprocess concurrently

    def prepared(ref, **kwargs):
        barrier.wait()
        return []

    monkeypatch.setattr(batching, "_prepared_texts", prepared)
    monkeypatch.setattr(batching, "embed_texts", lambda texts, *, guard=None: [])
    monkeypatch.setattr(ingestion_jobs.process_source_ingestion_job, "fn", lambda *args, **kwargs: None)
    monkeypatch.setattr(batching, "schedule_flush", lambda key, delay_ms=0: None)
    monkeypatch.setattr(batching.settings, "ingestion_batch_workers", 2)

    summary = batching.flush_ingestion_batch.fn("i1:n1")

    assert summary["files"] == 2
    assert summary["skipped"] == 0, "both files must preprocess concurrently (barrier reached)"


def test_embedding_batch_session_reuses_vectors_without_provider(monkeypatch):
    session = EmbeddingBatchSession()
    session.store(["hello", "world"], [[1.0, 2.0], [3.0, 4.0]])

    def explode(*args, **kwargs):
        raise AssertionError("provider must not be called for pre-warmed vectors")

    monkeypatch.setattr(embeddings, "_embed_with_openrouter", explode)
    with embedding_batch_session(session):
        assert embeddings.embed_texts(["hello", "world"]) == [[1.0, 2.0], [3.0, 4.0]]
    with pytest.raises(AssertionError, match="provider must not be called"):
        embeddings.embed_texts(["not-pre-warmed"])
