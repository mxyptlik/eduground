import subprocess
from types import SimpleNamespace

import pytest

from app.services import embeddings, vectorstore
from app.services.parse_process import parse_isolated
from app.services.prepared import PreparedSource, prepared_sources, take_prepared
from app.settings import settings


def test_embedding_request_count_is_bounded_and_order_preserved(monkeypatch):
    monkeypatch.setattr(settings, "embedding_max_inputs", 2)
    monkeypatch.setattr(settings, "embedding_max_input_bytes", 100)
    calls = []
    def embed(texts, **_):
        calls.append(texts)
        return [[float(t)] for t in texts]
    monkeypatch.setattr(embeddings, "_embed_with_openrouter", embed)
    assert embeddings.embed_texts(["1", "2", "3", "4", "5"]) == [[1.], [2.], [3.], [4.], [5.]]
    assert calls == [["1", "2"], ["3", "4"], ["5"]]


def test_oversize_chunk_stops_before_provider_dispatch(monkeypatch):
    monkeypatch.setattr(settings, "embedding_max_input_bytes", 10)
    monkeypatch.setattr(embeddings, "_embed_with_openrouter", lambda *_: pytest.fail("must not call provider"))
    with pytest.raises(embeddings.EmbeddingProviderError, match="rechunk"):
        embeddings.embed_texts(["a" * 20])


def test_fallback_reembeds_whole_document_without_mixing_spaces(monkeypatch):
    monkeypatch.setattr(settings, "embedding_max_inputs", 1)
    monkeypatch.setattr(settings, "gemini_fallback_enabled", True)
    def primary(texts):
        if texts == ["2"]:
            raise embeddings.EmbeddingProviderError("fail")
        return [[1.]]
    monkeypatch.setattr(embeddings, "_embed_with_openrouter", primary)
    monkeypatch.setattr(embeddings, "_embed_with_gemini", lambda texts: [[9.] for _ in texts])
    assert embeddings.embed_texts(["1", "2"]) == [[9.], [9.]]


def test_vector_upsert_bounds_and_waits_for_each_write(monkeypatch):
    monkeypatch.setattr(settings, "vector_upsert_max_points", 2)
    calls = []
    monkeypatch.setattr(vectorstore, "_request", lambda method, url, payload: calls.append((url, payload)))
    vectorstore.upsert_points([{"id": i} for i in range(5)])
    assert [len(payload["points"]) for _, payload in calls] == [2, 2, 1]
    assert all(url.endswith("?wait=true") for url, _ in calls)


def test_partial_vector_write_attempts_cleanup(monkeypatch):
    monkeypatch.setattr(settings, "vector_upsert_max_points", 2)
    deleted = []
    def request(method, url, payload):
        if len(payload.get("points", [])) == 1 and method == "PUT":
            raise vectorstore.VectorStoreError("last group failed")
    monkeypatch.setattr(vectorstore, "_request", request)
    monkeypatch.setattr(vectorstore, "delete_points", lambda ids: deleted.extend(ids))
    with pytest.raises(vectorstore.VectorStoreError):
        vectorstore.upsert_points([{"id": i} for i in range(3)])
    assert deleted == ["0", "1"]


def test_preparation_consumed_once_and_never_leaks_to_another_batch():
    source = PreparedSource(SimpleNamespace(), [], {})
    with prepared_sources({"v1": source}):
        assert take_prepared("v2") is None
        assert take_prepared("v1") is source
        assert take_prepared("v1") is None
    assert take_prepared("v1") is None


def test_isolated_parser_preserves_text_and_locators():
    document = parse_isolated(b"Page 2\nPhotosynthesis uses sunlight.", "sample.txt", 30)
    assert "Photosynthesis" in document.text
    assert document.segments


def test_parser_timeout_is_explicit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired("parser", 1)))
    with pytest.raises(TimeoutError, match="exceeded"):
        parse_isolated(b"text", "sample.txt", 1)
