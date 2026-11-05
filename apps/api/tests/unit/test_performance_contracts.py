from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import content, curriculum, identity, learning, operations, tutoring  # noqa: F401
from app.core.config import settings
from app.integrations import connections
from app.integrations.llm.base import _request_json, LLMProviderError
from app.integrations.vectorstore.qdrant_adapter import QdrantVectorStore, QdrantClientConfig
from app.models.enums import ChatRole, MessageStatus, PolicyMode, SourceStatus, SourceType
from app.models.content import Source, SourceVersion, Chunk
from app.models.tutoring import ChatMessage, AssistantAnswer, RetrievalTrace
from app.repositories.chat import ChatRepository
from app.repositories.sources import SourceRepository
from app.services.chat_memory import ChatMemoryStore, StoredChatMessage
from app.services.chat_runtime import TutorChatRuntime
from app.workflows.answer_question import AnswerQuestionWorkflow, PreparedGroundedAnswer


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def prepared():
    return PreparedGroundedAnswer("s", "n", PolicyMode.TEACHING, "q", {}, "system", "", [], [], 0, 17)


def workflow(db):
    return AnswerQuestionWorkflow(db, llm=Mock(), embeddings=Mock(), vector_store=Mock(), reranker=None, retrieval_cache=Mock())


def test_retrieval_time_excludes_generation_and_answer_graph_commits_once(db):
    commits = []
    event.listen(db, "after_commit", lambda _: commits.append(1))
    result = workflow(db).persist_answer(session=SimpleNamespace(id="s", notebook_id="n"),
                                        user_message=None, prepared=prepared(), answer_markdown="No evidence")
    assert result.retrieval_trace.latency_ms == 17
    assert commits == [1]
    assert db.query(ChatMessage).count() == db.query(AssistantAnswer).count() == 1


def test_answer_graph_rolls_back_on_late_failure(db, monkeypatch):
    wf = workflow(db)
    monkeypatch.setattr(wf.chats, "save_answer", Mock(side_effect=RuntimeError("disk failure")))
    with pytest.raises(RuntimeError, match="disk failure"):
        wf.persist_answer(session=SimpleNamespace(id="s", notebook_id="n"),
                          user_message=None, prepared=prepared(), answer_markdown="answer")
    assert db.query(ChatMessage).count() == db.query(RetrievalTrace).count() == 0


def test_slow_provider_runs_without_database_transaction(db):
    db.query(ChatMessage).all()
    assert db.in_transaction()
    wf = workflow(db)
    def generate(_):
        assert not db.in_transaction()
        return "response"
    wf.llm.generate_messages.side_effect = generate
    assert wf.generate_answer_text(prepared=prepared(), conversation_history=[]) == "response"


def test_pending_writes_are_never_discarded_to_release_a_connection(db):
    db.add(ChatMessage(chat_session_id="s", role=ChatRole.USER, content_markdown="new",
                       status=MessageStatus.COMPLETE, created_at=datetime.now(UTC)))
    with pytest.raises(RuntimeError, match="pending writes"):
        workflow(db).release_read_transaction()
    assert len(db.new) == 1


def test_hydration_query_count_does_not_grow_with_candidate_count(db):
    queries = []
    event.listen(db.bind, "before_cursor_execute", lambda *args: queries.append(args[2]))
    workflow(db)._load_candidates([f"c{i}" for i in range(100)], [f"s{i}" for i in range(100)])
    assert len(queries) == 2


def test_recent_history_sql_is_bounded_and_chronological(db):
    now = datetime.now(UTC)
    for i in range(100):
        db.add(ChatMessage(id=f"{i:04}", chat_session_id="s", role=ChatRole.USER,
                           content_markdown=str(i), status=MessageStatus.COMPLETE, created_at=now))
    db.commit()
    assert [m.content_markdown for m in ChatRepository(db).list_recent_messages_for_session("s", 3)] == ["97", "98", "99"]


def test_history_char_and_message_limits_preserve_latest(monkeypatch):
    monkeypatch.setattr(settings, "chat_history_max_messages", 2)
    monkeypatch.setattr(settings, "chat_history_max_chars", 5)
    store = ChatMemoryStore(Mock(), redis_client=Mock())
    result = store._bounded([StoredChatMessage("human", "old"), StoredChatMessage("ai", "abc"), StoredChatMessage("human", "xyz")])
    assert [m.content for m in result] == ["bc", "xyz"]


def test_provider_pool_reuses_client_and_preserves_error_contract(monkeypatch):
    seen = []
    def respond(req):
        seen.append(req)
        return httpx.Response(200 if len(seen) == 1 else 429, json={"answer": "ok"})
    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(connections, "_http", client)
    assert connections.http_client() is connections.http_client()
    assert _request_json("https://example.test", {}, {}, 1)["answer"] == "ok"
    with pytest.raises(LLMProviderError) as exc:
        _request_json("https://example.test", {}, {}, 1)
    assert exc.value.retryable
    client.close()


def test_search_does_not_fetch_collection_metadata(monkeypatch):
    from app.integrations.vectorstore import qdrant_adapter as adapter
    calls = []
    monkeypatch.setattr(adapter, "_request_json", lambda method, *a, **kw: calls.append(method) or {"result": []})
    store = QdrantVectorStore(QdrantClientConfig("http://vector", None, "chunks", 1, None, "Cosine"))
    store.search([1.0], {"notebook_id": "n"}, 5)
    store.search([1.0], {"notebook_id": "n"}, 5)
    assert calls == ["POST", "POST"]


def _source(db, *, notebook="n1", version=1):
    source = Source(id="source1", notebook_id=notebook, source_type=SourceType.PDF,
                    title="Biology", storage_key="test", mime_type="application/pdf",
                    checksum_sha256="a" * 64, byte_size=1, version_number=version,
                    status=SourceStatus.INDEXED, created_by_user_id="user")
    db.add(source)
    db.add(SourceVersion(id="version1", source_id=source.id, version_number=1,
                         storage_key="test", checksum_sha256="a" * 64,
                         status="indexed", created_at=datetime.now(UTC), created_by_user_id="user"))
    db.add(Chunk(id="chunk1", source_id=source.id, source_version_id="version1",
                 chunk_index=0, token_count=1, char_count=4, text="cell",
                 normalized_text="cell", qdrant_point_id="point1", created_at=datetime.now(UTC)))
    db.commit()
    return source


def test_cached_candidate_respects_notebook_scope_and_active_version(db):
    source = _source(db)
    wf = workflow(db)
    chunks, sources = wf._load_candidates(["chunk1"], ["source1"])
    assert wf._candidate_in_scope(chunks["chunk1"], sources["source1"], {"notebook_id": "n1"})
    assert not wf._candidate_in_scope(chunks["chunk1"], sources["source1"], {"notebook_id": "n2"})
    source.version_number = 2
    db.commit()
    chunks, _ = wf._load_candidates(["chunk1"], ["source1"])
    assert chunks == {}


def test_cache_key_changes_when_corpus_changes(db):
    wf = workflow(db)
    first = wf._cache_filters({"notebook_id": "n1"})["_corpus"]
    _source(db)
    second = wf._cache_filters({"notebook_id": "n1"})["_corpus"]
    assert first != second


def test_context_budget_only_cites_included_evidence(monkeypatch, db):
    monkeypatch.setattr(settings, "answer_context_max_chars", 1000)
    wf = workflow(db)
    candidates = [SimpleNamespace(chunk=SimpleNamespace(text="a" * 600)),
                  SimpleNamespace(chunk=SimpleNamespace(text="b" * 600))]
    chosen, notes = wf._select_context_records(candidates, [])
    assert chosen == [candidates[0]]
    assert notes == []


def test_grounded_answer_releases_read_connection_and_preserves_citation(db):
    _source(db)
    wf = workflow(db)
    wf.embeddings.embed.return_value = [[0.1, 0.2]]
    wf.vector_store.search.return_value = [
        {"score": 0.9, "payload": {"chunk_id": "chunk1", "source_id": "source1"}}
    ]
    wf.retrieval_cache.get.return_value = None

    def generate(_messages):
        assert not db.in_transaction()
        return "A cell is the unit of life."

    wf.llm.generate_messages.side_effect = generate
    chat_session = SimpleNamespace(id="session1", notebook_id="n1", user_id="user",
                                   policy_mode_snapshot=PolicyMode.TEACHING)
    user_message = SimpleNamespace(content_markdown="What is a cell?")
    answer = wf.run(session=chat_session, user_message=user_message,
                    selected_source_ids=[], selected_module_ids=[])

    assert answer.content_markdown == "A cell is the unit of life."
    assert len(answer.citations) == 1
    assert answer.citations[0].chunk_id == "chunk1"
    assert answer.citations[0].quote_text == "cell"
    assert db.query(RetrievalTrace).one().top_k_used == 1


def test_current_question_is_not_duplicated_in_history(db):
    memory = Mock()
    memory.get_session_history.return_value.messages = [
        StoredChatMessage("human", "earlier"),
        StoredChatMessage("ai", "earlier answer"),
        StoredChatMessage("human", "current question"),
    ]
    runtime = TutorChatRuntime(workflow(db), memory)
    assert [row.content for row in runtime._prior_history("s", "current question")] == [
        "earlier", "earlier answer"
    ]


def test_source_pages_are_bounded_and_do_not_overlap(db):
    db.add_all([
        Source(id=f"source-{i}", notebook_id="notebook", source_type=SourceType.PDF,
               title=f"Document {i}", storage_key=f"file-{i}", mime_type="application/pdf",
               checksum_sha256=f"{i:064x}", byte_size=1, version_number=1,
               status=SourceStatus.INDEXED, created_by_user_id="user")
        for i in range(5)
    ])
    db.commit()
    repository = SourceRepository(db)
    first = repository.list_for_notebook("notebook", limit=3, offset=0)
    second = repository.list_for_notebook("notebook", limit=3, offset=3)
    assert len(first) == 3
    assert len(second) == 2
    assert {item.id for item in first}.isdisjoint(item.id for item in second)
