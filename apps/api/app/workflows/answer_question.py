from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.observability import counter, histogram, traced_operation
from app.integrations.embeddings.base import OpenRouterEmbeddingProvider
from app.integrations.llm.base import OpenRouterLLMProvider
from app.integrations.rerank.openrouter_adapter import OpenRouterRerankerProvider, RerankerProviderError
from app.integrations.vectorstore.base import VectorStoreError
from app.integrations.vectorstore.qdrant_adapter import QdrantClientConfig, QdrantVectorStore
from app.models.content import Chunk, Source
from app.models.learning import Note
from app.models.enums import AnswerType, ChatRole, MessageStatus, NoteVisibility, RetrievalMode, SourceStatus
from app.models.tutoring import AssistantAnswer, ChatMessage, Citation, RetrievalTrace, RetrievalTraceItem
from app.policies.citation_policy import CitationPolicy
from app.policies.response_mode_policy import resolve_policy_instructions
from app.policies.scoping import build_retrieval_filters
from app.repositories.chat import ChatRepository
from app.schemas.chat import AnswerResponse, CitationResponse, RetrievalTraceItemResponse, RetrievalTraceResponse
from app.services.chat_retrieval_cache import CachedCandidate, ChatRetrievalCacheStore

logger = get_logger("app.retrieval")


@dataclass(slots=True)
class CandidateRecord:
    chunk: Chunk
    source: Source
    score: float
    rank_before: int
    rank_after: int
    rerank_score: float | None = None


@dataclass(slots=True)
class NoteContextRecord:
    note_id: str
    title: str
    content_markdown: str
    score: float


@dataclass(slots=True)
class PreparedGroundedAnswer:
    session_id: str
    notebook_id: str
    query_text: str
    filters: dict
    system_prompt: str
    context: str
    candidates: list[CandidateRecord]
    note_contexts: list[NoteContextRecord]
    retrieval_started_at: float


class AnswerQuestionWorkflow:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.chats = ChatRepository(db)
        self.llm = OpenRouterLLMProvider()
        self.embeddings = OpenRouterEmbeddingProvider()
        self.vector_store = QdrantVectorStore(
            QdrantClientConfig(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key,
                collection_name=settings.qdrant_collection_name,
                timeout_seconds=60.0,
                vector_size=None,
                distance="Cosine",
            )
        )
        self.reranker = OpenRouterRerankerProvider() if settings.reranker_enabled else None
        self.citation_policy = CitationPolicy(minimum_coverage=1.0)
        self.retrieval_cache = ChatRetrievalCacheStore()

    def run(self, *, session, user_message, selected_source_ids: list[str], selected_module_ids: list[str]) -> AnswerResponse:
        prepared = self.prepare_grounded_answer(
            session=session,
            user_message=user_message,
            selected_source_ids=selected_source_ids,
            selected_module_ids=selected_module_ids,
        )
        answer_markdown = self.generate_answer_text(prepared=prepared, conversation_history=[])
        return self.persist_answer(session=session, user_message=user_message, prepared=prepared, answer_markdown=answer_markdown)

    def prepare_grounded_answer(
        self,
        *,
        session,
        user_message,
        selected_source_ids: list[str],
        selected_module_ids: list[str],
    ) -> PreparedGroundedAnswer:
        started = perf_counter()
        with traced_operation(
            "workflow.answer_question.retrieve",
            attributes={"chat_session_id": session.id, "notebook_id": session.notebook_id},
            metric_name="eduground_workflow",
            metric_labels={"workflow": "answer_question_retrieve"},
        ):
            filters = build_retrieval_filters(
                session.notebook_id,
                source_ids=selected_source_ids,
                module_ids=selected_module_ids,
            )
            policy = resolve_policy_instructions(session.policy_mode_snapshot)
            candidates = self._get_cached_candidates(session.id, user_message.content_markdown, filters)
            if candidates is None:
                candidates = self._retrieve_candidate_chunks(filters, user_message.content_markdown)
                if candidates:
                    self.retrieval_cache.set(
                        session_id=session.id,
                        query_text=user_message.content_markdown,
                        filters=filters,
                        candidates=[
                            CachedCandidate(
                                chunk_id=item.chunk.id,
                                source_id=item.source.id,
                                score=item.score,
                                rank_before=item.rank_before,
                                rank_after=item.rank_after,
                                rerank_score=item.rerank_score,
                            )
                            for item in candidates
                        ],
                    )
            note_contexts = self._retrieve_note_context(
                notebook_id=session.notebook_id,
                user_id=session.user_id,
                query_text=user_message.content_markdown,
            )
            context = self._build_context(candidates, note_contexts)
        return PreparedGroundedAnswer(
            session_id=session.id,
            notebook_id=session.notebook_id,
            query_text=user_message.content_markdown,
            filters=filters,
            system_prompt="\n".join(policy.system_rules),
            context=context,
            candidates=candidates,
            note_contexts=note_contexts,
            retrieval_started_at=started,
        )

    def generate_answer_text(self, *, prepared: PreparedGroundedAnswer, conversation_history: list[Any]) -> str:
        from app.services.chat_runtime import build_prompt_messages

        prompt_messages = build_prompt_messages(
            system_prompt=prepared.system_prompt,
            history_messages=conversation_history,
            context=prepared.context,
            question=prepared.query_text,
        )
        return self.llm.generate_messages(prompt_messages).strip()

    def stream_answer_text(self, *, prepared: PreparedGroundedAnswer, conversation_history: list[Any]):
        from app.services.chat_runtime import build_prompt_messages

        prompt_messages = build_prompt_messages(
            system_prompt=prepared.system_prompt,
            history_messages=conversation_history,
            context=prepared.context,
            question=prepared.query_text,
        )
        yield from self.llm.stream_messages(prompt_messages)

    def persist_answer(self, *, session, user_message, prepared: PreparedGroundedAnswer, answer_markdown: str) -> AnswerResponse:
        latency_ms = int((perf_counter() - prepared.retrieval_started_at) * 1000)
        histogram(
            "eduground_retrieval_latency_ms",
            latency_ms,
            labels={"workflow": "answer_question"},
            description="Grounded answer retrieval latency in milliseconds",
        )
        logger.info(
            "Completed grounded answer retrieval",
            extra={
                "extra_json": {
                    "chat_session_id": session.id,
                    "notebook_id": session.notebook_id,
                    "candidate_count": len(prepared.candidates),
                    "latency_ms": latency_ms,
                    "reranker_enabled": self.reranker is not None,
                }
            },
        )

        assistant_message = ChatMessage(
            chat_session_id=session.id,
            role=ChatRole.ASSISTANT,
            content_markdown=answer_markdown,
            status=MessageStatus.COMPLETE,
            created_at=datetime.now(UTC),
        )
        assistant_message = self.chats.create_message(assistant_message)

        reranker_used = any(item.rerank_score is not None for item in prepared.candidates)
        trace = RetrievalTrace(
            chat_message_id=assistant_message.id,
            query_text=prepared.query_text,
            query_embedding_model=settings.openrouter_embedding_model,
            retrieval_mode=RetrievalMode.DENSE,
            top_k_requested=5,
            top_k_used=len(prepared.candidates),
            filters_json=prepared.filters,
            reranker_used=reranker_used,
            latency_ms=latency_ms,
            created_at=datetime.now(UTC),
        )
        trace = self.chats.save_trace(trace)
        trace_items = self.chats.save_trace_items(
            [
                RetrievalTraceItem(
                    retrieval_trace_id=trace.id,
                    chunk_id=item.chunk.id,
                    initial_score=item.score,
                    rank_before=item.rank_before,
                    rank_after=item.rank_after,
                    rerank_score=item.rerank_score,
                    was_used_in_context=True,
                )
                for item in prepared.candidates
            ]
        )

        validation = self.citation_policy.validate(
            factual_blocks=1 if prepared.candidates else 0,
            citation_count=len(prepared.candidates),
        )
        counter(
            "eduground_citation_validation_total",
            labels={"result": "pass" if validation.is_valid else "fail"},
            description="Citation validation outcomes for grounded answers",
        )
        answer_type = AnswerType.GROUNDED_ANSWER if prepared.candidates and validation.is_valid else AnswerType.INSUFFICIENT_EVIDENCE
        assistant_answer = AssistantAnswer(
            chat_message_id=assistant_message.id,
            answer_type=answer_type,
            citation_coverage_ratio=validation.coverage_ratio,
            had_refusal=not prepared.candidates,
            created_at=datetime.now(UTC),
        )
        assistant_answer = self.chats.save_answer(assistant_answer)
        citations = self.chats.save_citations(
            [
                Citation(
                    assistant_answer_id=assistant_answer.id,
                    source_id=item.source.id,
                    chunk_id=item.chunk.id,
                    source_segment_id=None,
                    page_start=item.chunk.start_page,
                    page_end=item.chunk.end_page,
                    slide_start=item.chunk.start_slide,
                    slide_end=item.chunk.end_slide,
                    quote_text=item.chunk.text[:400],
                    display_label=self._build_display_label(item.source, item.chunk),
                    created_at=datetime.now(UTC),
                )
                for item in prepared.candidates
            ]
        ) if prepared.candidates else []
        return AnswerResponse(
            answer_type=answer_type,
            content_markdown=answer_markdown,
            citations=[
                CitationResponse(
                    id=item.id,
                    source_id=item.source_id,
                    chunk_id=item.chunk_id,
                    source_segment_id=item.source_segment_id,
                    page_start=item.page_start,
                    page_end=item.page_end,
                    slide_start=item.slide_start,
                    slide_end=item.slide_end,
                    quote_text=item.quote_text,
                    display_label=item.display_label,
                )
                for item in citations
            ],
            refusal_reason=None if prepared.candidates else "No indexed evidence was retrieved inside the selected scope.",
            retrieval_trace=RetrievalTraceResponse(
                id=trace.id,
                query_text=trace.query_text,
                retrieval_mode=trace.retrieval_mode.value,
                filters_json=trace.filters_json,
                top_k_requested=trace.top_k_requested,
                top_k_used=trace.top_k_used,
                reranker_used=trace.reranker_used,
                latency_ms=trace.latency_ms,
                items=[
                    RetrievalTraceItemResponse(
                        chunk_id=item.chunk_id,
                        source_id=prepared.candidates[index].source.id if index < len(prepared.candidates) else "",
                        initial_score=float(item.initial_score),
                        rank_before=item.rank_before,
                        rank_after=item.rank_after,
                        was_used_in_context=item.was_used_in_context,
                    )
                    for index, item in enumerate(trace_items)
                ],
            ),
        )

    def build_citation_preview(self, candidate: CandidateRecord) -> dict[str, Any]:
        return {
            "source_id": candidate.source.id,
            "chunk_id": candidate.chunk.id,
            "display_label": self._build_display_label(candidate.source, candidate.chunk),
            "page_start": candidate.chunk.start_page,
            "page_end": candidate.chunk.end_page,
            "slide_start": candidate.chunk.start_slide,
            "slide_end": candidate.chunk.end_slide,
            "quote_text": candidate.chunk.text[:240],
        }

    def _get_cached_candidates(self, session_id: str, query_text: str, filters: dict[str, Any]) -> list[CandidateRecord] | None:
        cached = self.retrieval_cache.get(session_id=session_id, query_text=query_text, filters=filters)
        if not cached:
            return None
        hydrated: list[CandidateRecord] = []
        for item in cached:
            chunk = self.db.get(Chunk, item.chunk_id)
            source = self.db.get(Source, item.source_id)
            if chunk is None or source is None or source.status != SourceStatus.INDEXED:
                return None
            hydrated.append(
                CandidateRecord(
                    chunk=chunk,
                    source=source,
                    score=item.score,
                    rank_before=item.rank_before,
                    rank_after=item.rank_after,
                    rerank_score=item.rerank_score,
                )
            )
        return hydrated

    def _retrieve_candidate_chunks(self, filters: dict, query_text: str) -> list[CandidateRecord]:
        with traced_operation(
            "workflow.answer_question.retrieve_candidates",
            metric_name="eduground_retrieval_stage",
            metric_labels={"stage": "retrieve_candidates"},
        ):
            query_vectors = self.embeddings.embed([query_text])
        if not query_vectors:
            return []
        try:
            search_results = self.vector_store.search(query_vectors[0], filters, top_k=5)
        except VectorStoreError as exc:
            logger.warning(
                "Vector search unavailable, continuing with LLM fallback",
                extra={
                    "extra_json": {
                        "error": str(exc),
                        "notebook_scope": filters.get("notebook_id"),
                    }
                },
            )
            counter(
                "eduground_provider_failures_total",
                labels={"provider": "qdrant", "operation": "search"},
                description="Provider failures by provider and operation",
            )
            return []
        candidates: list[CandidateRecord] = []
        for result in search_results:
            payload = result.get("payload") or {}
            chunk_id = payload.get("chunk_id")
            source_id = payload.get("source_id")
            if not chunk_id or not source_id:
                continue
            chunk = self.db.get(Chunk, chunk_id)
            source = self.db.get(Source, source_id)
            if chunk is None or source is None or source.status != SourceStatus.INDEXED:
                continue
            candidates.append(
                CandidateRecord(
                    chunk=chunk,
                    source=source,
                    score=float(result.get("score") or 0.0),
                    rank_before=len(candidates) + 1,
                    rank_after=len(candidates) + 1,
                    rerank_score=None,
                )
            )
        if not candidates or self.reranker is None:
            return candidates
        try:
            rerank_count = min(settings.reranker_top_n, len(candidates))
            rerank_inputs = [
                {
                    "chunk": item.chunk,
                    "source": item.source,
                    "score": item.score,
                    "rank_before": item.rank_before,
                    "rank_after": item.rank_after,
                    "rerank_score": item.rerank_score,
                }
                for item in candidates[:rerank_count]
            ]
            reranked_items = self.reranker.rerank(query_text, rerank_inputs)
            reranked = [
                CandidateRecord(
                    chunk=item["chunk"],
                    source=item["source"],
                    score=float(item["score"]),
                    rank_before=int(item.get("rank_before") or index + 1),
                    rank_after=int(item.get("rank_after") or index + 1),
                    rerank_score=float(item["rerank_score"]) if item.get("rerank_score") is not None else None,
                )
                for index, item in enumerate(reranked_items)
            ]
            combined = reranked + candidates[rerank_count:]
            for index, item in enumerate(combined, start=1):
                item.rank_after = index
            return combined
        except RerankerProviderError:
            counter(
                "eduground_provider_failures_total",
                labels={"provider": "openrouter", "operation": "rerank"},
                description="Provider failures by provider and operation",
            )
            return candidates

    def _build_display_label(self, source: Source, chunk: Chunk) -> str:
        if chunk.start_slide:
            return f"{source.title}, slide {chunk.start_slide}"
        if chunk.start_page:
            return f"{source.title}, page {chunk.start_page}"
        return source.title

    def _build_context(
        self,
        candidates: list[CandidateRecord],
        note_contexts: list[NoteContextRecord],
    ) -> str:
        parts: list[str] = []
        for item in candidates:
            parts.append(item.chunk.text)
        for note in note_contexts:
            parts.append(f"Notebook note: {note.title}\n{note.content_markdown}")
        return "\n\n".join(part for part in parts if part.strip())

    def _retrieve_note_context(self, *, notebook_id: str, user_id: str, query_text: str) -> list[NoteContextRecord]:
        query_terms = self._extract_query_terms(query_text)
        if not query_terms:
            return []
        notes = (
            self.db.query(Note)
            .filter(Note.notebook_id == notebook_id)
            .filter(Note.converted_source_id.is_(None))
            .all()
        )
        ranked: list[NoteContextRecord] = []
        for note in notes:
            if note.visibility == NoteVisibility.PRIVATE and note.author_user_id != user_id:
                continue
            text = f"{note.title}\n{note.content_markdown}".strip()
            if not text:
                continue
            score = self._score_note_match(text, query_terms, query_text)
            if score <= 0:
                continue
            ranked.append(
                NoteContextRecord(
                    note_id=note.id,
                    title=note.title,
                    content_markdown=text[:1_200],
                    score=score,
                )
            )
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:3]

    def _extract_query_terms(self, query_text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]{3,}", query_text.lower())
            if token not in {"what", "when", "where", "which", "with", "from", "that", "this", "about", "would", "could"}
        }

    def _score_note_match(self, note_text: str, query_terms: set[str], query_text: str) -> float:
        normalized_note = note_text.lower()
        overlap = sum(1 for term in query_terms if term in normalized_note)
        if overlap == 0:
            return 0.0
        phrase_bonus = 1.5 if query_text.strip().lower() in normalized_note else 0.0
        density_bonus = min(len(normalized_note), 1_200) / 1_200
        return overlap + phrase_bonus + density_bonus
