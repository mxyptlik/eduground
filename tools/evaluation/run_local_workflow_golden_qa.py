from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import re
from time import perf_counter
from urllib import error, request
from uuid import uuid4


def configure_local_environment(database_path: Path, *, qdrant_url: str | None = None, qdrant_collection: str | None = None) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_url = f"sqlite:///{database_path.as_posix()}"
    os.environ["CURRICULUM_TUTOR_DATABASE_URL"] = sqlite_url
    os.environ["DATABASE_URL"] = sqlite_url
    os.environ.setdefault("CURRICULUM_TUTOR_CLERK_ENABLED", "false")
    os.environ.setdefault("CURRICULUM_TUTOR_AUTO_CREATE_SCHEMA", "true")
    os.environ.setdefault("CURRICULUM_TUTOR_REDIS_URL", "redis://localhost:0/0")
    if qdrant_url:
        os.environ["CURRICULUM_TUTOR_QDRANT_URL"] = qdrant_url
    if qdrant_collection:
        os.environ["CURRICULUM_TUTOR_QDRANT_COLLECTION_NAME"] = qdrant_collection


class LocalVectorStore:
    def __init__(self) -> None:
        self.points: list[dict] = []
        self.document_frequency: dict[str, int] = {}
        self.average_length = 0.0
        self.query_provider: HashingEmbeddingProvider | None = None

    def add(self, *, vector: list[float], payload: dict) -> None:
        tokens = tokenize(str(payload.get("chunk_text") or ""))
        term_counts: dict[str, int] = {}
        for token in tokens:
            term_counts[token] = term_counts.get(token, 0) + 1
        for token in set(tokens):
            self.document_frequency[token] = self.document_frequency.get(token, 0) + 1
        self.points.append(
            {
                "vector": vector,
                "payload": payload,
                "tokens": tokens,
                "term_counts": term_counts,
                "length": len(tokens),
            }
        )
        self.average_length = sum(point["length"] for point in self.points) / max(len(self.points), 1)

    def search(self, vector: list[float], filters: dict, top_k: int) -> list[dict]:
        scored: list[dict] = []
        query_text = self.query_provider.last_text if self.query_provider is not None else ""
        query_terms = tokenize(query_text)
        for point in self.points:
            payload = point["payload"]
            if payload.get("notebook_id") != filters.get("notebook_id"):
                continue
            if filters.get("status") and payload.get("status") != filters.get("status"):
                continue
            if filters.get("source_ids") and payload.get("source_id") not in set(filters["source_ids"]):
                continue
            if filters.get("module_ids") and payload.get("module_id") not in set(filters["module_ids"]):
                continue
            lexical_score = self._bm25_score(query_terms, point)
            vector_score = cosine_similarity(vector, point["vector"])
            scored.append(
                {
                    "point_id": payload["chunk_id"],
                    "score": lexical_score + (0.05 * vector_score),
                    "payload": payload,
                    "vector": None,
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]

    def _bm25_score(self, query_terms: list[str], point: dict) -> float:
        if not query_terms:
            return 0.0
        total_docs = max(len(self.points), 1)
        length = max(point["length"], 1)
        average_length = self.average_length or length
        k1 = 1.5
        b = 0.75
        score = 0.0
        for term in query_terms:
            frequency = point["term_counts"].get(term, 0)
            if frequency == 0:
                continue
            document_frequency = self.document_frequency.get(term, 0)
            idf = math.log(1 + ((total_docs - document_frequency + 0.5) / (document_frequency + 0.5)))
            denominator = frequency + k1 * (1 - b + b * (length / average_length))
            score += idf * ((frequency * (k1 + 1)) / denominator)
        payload = point["payload"]
        title_terms = set(tokenize(str(payload.get("source_title") or "")))
        topic_terms = set(tokenize(str(payload.get("topic") or "")))
        query_term_set = set(query_terms)
        score += 0.4 * len(query_term_set & title_terms)
        score += 0.6 * len(query_term_set & topic_terms)
        return score


class HybridVectorStore:
    def __init__(self, *, dense_store, lexical_store: LocalVectorStore, dense_weight: float = 0.05, lexical_weight: float = 0.95) -> None:
        self.dense_store = dense_store
        self.lexical_store = lexical_store
        self.dense_weight = dense_weight
        self.lexical_weight = lexical_weight

    def search(self, vector: list[float], filters: dict, top_k: int) -> list[dict]:
        candidate_count = max(top_k * 4, 20)
        dense_results = self.dense_store.search(vector, filters, top_k=candidate_count)
        lexical_results = self.lexical_store.search(vector, filters, top_k=candidate_count)
        return self._fuse(dense_results=dense_results, lexical_results=lexical_results, top_k=top_k)

    def _fuse(self, *, dense_results: list[dict], lexical_results: list[dict], top_k: int) -> list[dict]:
        fused: dict[str, dict] = {}
        max_dense = max([float(item.get("score") or 0.0) for item in dense_results] or [1.0])
        max_lexical = max([float(item.get("score") or 0.0) for item in lexical_results] or [1.0])
        for rank, item in enumerate(dense_results, start=1):
            key = str(item.get("point_id") or (item.get("payload") or {}).get("chunk_id"))
            if not key:
                continue
            if key not in fused:
                fused[key] = dict(item)
                fused[key]["score"] = 0.0
            normalized = (float(item.get("score") or 0.0) / max_dense) if max_dense else 0.0
            fused[key]["score"] = float(fused[key].get("score") or 0.0) + self.dense_weight * normalized + 1 / (60 + rank)
        for rank, item in enumerate(lexical_results, start=1):
            key = str(item.get("point_id") or (item.get("payload") or {}).get("chunk_id"))
            if not key:
                continue
            if key not in fused:
                fused[key] = dict(item)
                fused[key]["score"] = 0.0
            normalized = (float(item.get("score") or 0.0) / max_lexical) if max_lexical else 0.0
            fused[key]["score"] = float(fused[key].get("score") or 0.0) + self.lexical_weight * normalized + 1 / (60 + rank)
        results = list(fused.values())
        results.sort(key=lambda item: item.get("score") or 0.0, reverse=True)
        return results[:top_k]


class NoopRetrievalCache:
    def get(self, *, session_id: str, query_text: str, filters: dict):
        return None

    def set(self, **kwargs) -> None:
        return None


class HashingEmbeddingProvider:
    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions
        self.last_text = ""

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.last_text = texts[-1] if texts else ""
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize(text):
            index = hash(token) % self.dimensions
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class ExtractiveLLMProvider:
    def __init__(self) -> None:
        self.last_latency_ms = 0

    def generate(self, *, system_prompt: str, user_prompt: str, context: str) -> str:
        started = perf_counter()
        try:
            return self._answer(context=context, question=user_prompt)
        finally:
            self.last_latency_ms = int((perf_counter() - started) * 1000)

    def generate_messages(self, messages: list[dict]) -> str:
        text = "\n\n".join(str(message.get("content", "")) for message in messages)
        context, question = extract_context_and_question(text)
        started = perf_counter()
        try:
            return self._answer(context=context, question=question)
        finally:
            self.last_latency_ms = int((perf_counter() - started) * 1000)

    def stream_messages(self, messages: list[dict]):
        yield self.generate_messages(messages)

    def _answer(self, *, context: str, question: str) -> str:
        if not context.strip() or context.strip() == "No context available.":
            return "I could not find enough indexed evidence in the provided sources to answer this confidently."
        question_terms = set(tokenize(question))
        candidates = split_answer_candidates(context)
        if not candidates:
            return context.strip()[:700]
        ranked = sorted(candidates, key=lambda item: score_text(item, question_terms), reverse=True)
        selected: list[str] = []
        for item in ranked:
            if score_text(item, question_terms) <= 0 and selected:
                continue
            selected.append(item)
            if len(" ".join(selected).split()) >= 55 or len(selected) >= 3:
                break
        return " ".join(selected).strip()


def tokenize(text: str) -> list[str]:
    stopwords = {
        "about",
        "according",
        "after",
        "also",
        "and",
        "are",
        "can",
        "does",
        "for",
        "from",
        "how",
        "into",
        "its",
        "key",
        "make",
        "point",
        "source",
        "that",
        "the",
        "this",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
    return [token for token in re.findall(r"[a-z0-9]{3,}", text.lower()) if token not in stopwords]


def extract_context_and_question(text: str) -> tuple[str, str]:
    match = re.search(r"Context:\s*(.*?)\s*Question:\s*(.*)$", text, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return text, ""
    return match.group(1).strip(), match.group(2).strip()


def split_answer_candidates(context: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", context.replace("\u00a0", " ")).strip()
    pieces = re.split(r"(?<=[.!?])\s+|(?<=\w)\s{2,}|[\n\r]+", normalized)
    return [piece.strip(" -:;") for piece in pieces if len(piece.split()) >= 6]


def score_text(text: str, question_terms: set[str]) -> float:
    tokens = tokenize(text)
    if not tokens:
        return 0.0
    token_set = set(tokens)
    overlap = len(question_terms & token_set)
    density = overlap / max(len(token_set), 1)
    return overlap + density


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def source_type_for(path: str):
    from app.models.enums import SourceType

    suffix = Path(path).suffix.lower()
    return {
        ".pdf": SourceType.PDF,
        ".pptx": SourceType.PPTX,
        ".docx": SourceType.DOCX,
        ".txt": SourceType.TXT,
        ".md": SourceType.MD,
    }.get(suffix, SourceType.TXT)


def create_seed_data(db, dataset: list[dict]):
    from app.models.content import Chunk, Source, SourceVersion
    from app.models.curriculum import Notebook, NotebookMembership
    from app.models.enums import (
        AuthProvider,
        DeploymentMode,
        InstitutionRole,
        NotebookMembershipRole,
        NotebookVisibility,
        PolicyMode,
        PrivacyMode,
        SourceStatus,
        UserStatus,
    )
    from app.models.identity import Institution, InstitutionMembership, User

    now = datetime.now(UTC)
    institution = Institution(
        name="Eduground Local Evaluation",
        slug=f"eduground-local-eval-{uuid4().hex[:8]}",
        deployment_mode=DeploymentMode.CLOUD,
        privacy_mode=PrivacyMode.STANDARD,
    )
    user = User(
        institution=institution,
        email=f"eval-{uuid4().hex[:8]}@eduground.local",
        auth_provider=AuthProvider.LOCAL,
        external_subject_id=f"local-eval-{uuid4().hex}",
        display_name="Local Evaluator",
        status=UserStatus.ACTIVE,
    )
    db.add_all([institution, user])
    db.flush()
    db.add(InstitutionMembership(institution_id=institution.id, user_id=user.id, role=InstitutionRole.INSTRUCTOR, created_at=now))
    notebook = Notebook(
        institution_id=institution.id,
        title="Eduground Local Golden QA Evaluation",
        description="Local seeded notebook for offline evaluation.",
        owner_user_id=user.id,
        visibility=NotebookVisibility.PRIVATE,
        policy_mode=PolicyMode.TEACHING,
    )
    db.add(notebook)
    db.flush()
    db.add(NotebookMembership(notebook_id=notebook.id, user_id=user.id, role=NotebookMembershipRole.OWNER, granted_by_user_id=user.id, created_at=now))

    by_source_file: dict[str, list[dict]] = defaultdict(list)
    for sample in dataset:
        by_source_file[str(sample["source_file"])].append(sample)

    chunk_records: list[Chunk] = []
    for source_index, (source_file, samples) in enumerate(by_source_file.items(), start=1):
        first = samples[0]
        source = Source(
            notebook_id=notebook.id,
            module_id=None,
            source_type=source_type_for(source_file),
            title=str(first["source_label"])[:255],
            original_filename=Path(source_file).name,
            storage_key=f"local-eval/{source_index}/{Path(source_file).name}",
            mime_type="application/octet-stream",
            checksum_sha256=uuid4().hex + uuid4().hex,
            byte_size=sum(len(str(sample.get("source_context") or sample["retrieved_context"]).encode("utf-8")) for sample in samples),
            language_code="en",
            status=SourceStatus.INDEXED,
            created_by_user_id=user.id,
        )
        db.add(source)
        db.flush()
        source_version = SourceVersion(
            source_id=source.id,
            version_number=1,
            storage_key=source.storage_key,
            checksum_sha256=source.checksum_sha256,
            status=SourceStatus.INDEXED.value,
            created_at=now,
            created_by_user_id=user.id,
        )
        db.add(source_version)
        db.flush()
        for chunk_index, sample in enumerate(samples, start=1):
            text = str(sample.get("source_context") or sample["retrieved_context"])
            chunk = Chunk(
                source_id=source.id,
                source_version_id=source_version.id,
                module_id=None,
                chunk_index=chunk_index,
                token_count=len(text.split()),
                char_count=len(text),
                text=text,
                normalized_text=" ".join(text.lower().split()),
                start_page=parse_locator_number(sample.get("locator"), "page"),
                end_page=parse_locator_number(sample.get("locator"), "page"),
                start_slide=parse_locator_number(sample.get("locator"), "slide"),
                end_slide=parse_locator_number(sample.get("locator"), "slide"),
                heading_path=[str(sample.get("topic") or sample["source_label"])],
                tags={"sample_key": sample["sample_key"]},
                qdrant_point_id=f"local-{sample['sample_key']}",
                created_at=now,
            )
            chunk._local_source = source
            db.add(chunk)
            chunk_records.append(chunk)
    db.commit()
    return user, notebook, chunk_records


def parse_locator_number(locator: str | None, kind: str) -> int | None:
    if not locator:
        return None
    parts = str(locator).split()
    for index, part in enumerate(parts[:-1]):
        if part == kind and parts[index + 1].isdigit():
            return int(parts[index + 1])
    return None


def build_local_vector_store(chunk_records: list, embeddings_provider, batch_size: int, notebook_id: str) -> LocalVectorStore:
    from app.models.enums import SourceStatus

    store = LocalVectorStore()
    if isinstance(embeddings_provider, HashingEmbeddingProvider):
        store.query_provider = embeddings_provider
    for start in range(0, len(chunk_records), batch_size):
        batch = chunk_records[start : start + batch_size]
        vectors = embeddings_provider.embed([chunk.text for chunk in batch])
        for chunk, vector in zip(batch, vectors, strict=True):
            store.add(
                vector=vector,
                payload={
                    "notebook_id": notebook_id,
                    "source_id": chunk.source_id,
                    "module_id": chunk.module_id,
                    "chunk_id": chunk.id,
                    "status": SourceStatus.INDEXED.value,
                    "chunk_text": chunk.text,
                    "source_title": db_source_title(chunk),
                    "topic": " ".join(chunk.heading_path or []),
                },
            )
        print(json.dumps({"embedded_chunks": min(start + batch_size, len(chunk_records)), "total_chunks": len(chunk_records)}))
    return store


def reset_qdrant_collection(*, qdrant_url: str, collection_name: str) -> None:
    req = request.Request(
        f"{qdrant_url.rstrip('/')}/collections/{collection_name}",
        method="DELETE",
        headers={"Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            response.read()
    except error.HTTPError as exc:
        if exc.code != 404:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Could not reset Qdrant collection {collection_name!r}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Qdrant at {qdrant_url!r}: {exc.reason}") from exc


def create_qdrant_hnsw_collection(*, qdrant_url: str, collection_name: str, vector_size: int) -> None:
    payload = {
        "vectors": {"size": vector_size, "distance": "Cosine"},
        "hnsw_config": {
            "m": 16,
            "ef_construct": 100,
            "full_scan_threshold": 10,
            "on_disk": False,
        },
        "optimizers_config": {
            "indexing_threshold": 1,
        },
    }
    req = request.Request(
        f"{qdrant_url.rstrip('/')}/collections/{collection_name}",
        data=json.dumps(payload).encode("utf-8"),
        method="PUT",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            response.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Could not create Qdrant HNSW collection {collection_name!r}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Qdrant at {qdrant_url!r}: {exc.reason}") from exc


def build_qdrant_vector_store(
    chunk_records: list,
    embeddings_provider,
    batch_size: int,
    notebook_id: str,
    *,
    qdrant_url: str,
    collection_name: str,
    reset_collection: bool,
):
    from app.integrations.vectorstore.qdrant_adapter import QdrantClientConfig, QdrantVectorStore
    from app.models.enums import SourceStatus

    if reset_collection:
        reset_qdrant_collection(qdrant_url=qdrant_url, collection_name=collection_name)
        create_qdrant_hnsw_collection(
            qdrant_url=qdrant_url,
            collection_name=collection_name,
            vector_size=getattr(embeddings_provider, "dimensions", 384),
        )
    store = QdrantVectorStore(
        QdrantClientConfig(
            url=qdrant_url,
            api_key=None,
            collection_name=collection_name,
            timeout_seconds=60.0,
            vector_size=getattr(embeddings_provider, "dimensions", None),
            distance="Cosine",
        )
    )
    for start in range(0, len(chunk_records), batch_size):
        batch = chunk_records[start : start + batch_size]
        vectors = embeddings_provider.embed([chunk.text for chunk in batch])
        for chunk, vector in zip(batch, vectors, strict=True):
            store.upsert(
                point_id=chunk.id,
                vector=vector,
                payload={
                    "notebook_id": notebook_id,
                    "source_id": chunk.source_id,
                    "module_id": chunk.module_id,
                    "chunk_id": chunk.id,
                    "status": SourceStatus.INDEXED.value,
                    "chunk_text": chunk.text,
                    "source_title": db_source_title(chunk),
                    "topic": " ".join(chunk.heading_path or []),
                },
            )
        print(
            json.dumps(
                {
                    "qdrant_indexed_chunks": min(start + batch_size, len(chunk_records)),
                    "total_chunks": len(chunk_records),
                    "collection": collection_name,
                }
            )
        )
    return store


def build_hybrid_vector_store(
    chunk_records: list,
    embeddings_provider,
    batch_size: int,
    notebook_id: str,
    *,
    qdrant_url: str,
    collection_name: str,
    reset_collection: bool,
):
    dense_store = build_qdrant_vector_store(
        chunk_records,
        embeddings_provider,
        batch_size,
        notebook_id,
        qdrant_url=qdrant_url,
        collection_name=collection_name,
        reset_collection=reset_collection,
    )
    lexical_store = build_local_vector_store(chunk_records, embeddings_provider, batch_size, notebook_id)
    return HybridVectorStore(dense_store=dense_store, lexical_store=lexical_store)


def db_source_title(chunk) -> str:
    source = getattr(chunk, "_local_source", None)
    if source is not None:
        return source.title
    return ""


def run_evaluation(
    db,
    dataset: list[dict],
    user,
    notebook,
    vector_store,
    output_path: Path,
    limit: int | None,
    embeddings_provider,
    llm_provider=None,
) -> list[dict]:
    from app.models.enums import ChatRole, MessageStatus, PolicyMode
    from app.models.tutoring import ChatMessage, ChatSession
    from app.workflows.answer_question import AnswerQuestionWorkflow

    session = ChatSession(
        notebook_id=notebook.id,
        user_id=user.id,
        title="Local golden QA evaluation",
        policy_mode_snapshot=PolicyMode.TEACHING,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    workflow = AnswerQuestionWorkflow(db)
    workflow.vector_store = vector_store
    workflow.embeddings = embeddings_provider
    workflow.retrieval_cache = NoopRetrievalCache()
    if llm_provider is not None:
        workflow.llm = llm_provider
        workflow.reranker = None
        workflow.generate_answer_text = lambda *, prepared, conversation_history: llm_provider.generate(
            system_prompt=prepared.system_prompt,
            user_prompt=prepared.query_text,
            context=prepared.context,
        )
    selected = dataset[:limit] if limit else dataset
    completed: list[dict] = []
    for index, sample in enumerate(selected, start=1):
        user_message = ChatMessage(
            chat_session_id=session.id,
            role=ChatRole.USER,
            content_markdown=sample["question"],
            status=MessageStatus.COMPLETE,
            created_at=datetime.now(UTC),
        )
        db.add(user_message)
        db.commit()
        db.refresh(user_message)
        started = perf_counter()
        answer = workflow.run(session=session, user_message=user_message, selected_source_ids=[], selected_module_ids=[])
        end_to_end_latency_ms = int((perf_counter() - started) * 1000)
        citations = [citation.model_dump(mode="json") for citation in answer.citations]
        retrieved_context = "\n\n".join(citation["quote_text"] for citation in citations).strip()
        completed_sample = dict(sample)
        completed_sample.update(
            {
                "retrieved_context": retrieved_context,
                "model_answer": answer.content_markdown,
                "eduground_answer_type": answer.answer_type.value,
                "eduground_refusal_reason": answer.refusal_reason,
                "eduground_citations": citations,
                "eduground_retrieval_trace": answer.retrieval_trace.model_dump(mode="json") if answer.retrieval_trace else None,
                "eduground_end_to_end_latency_ms": end_to_end_latency_ms,
                "eduground_model_latency_ms": getattr(llm_provider, "last_latency_ms", None),
            }
        )
        completed.append(completed_sample)
        output_path.write_text(json.dumps(completed, indent=2, ensure_ascii=True), encoding="utf-8")
        print(json.dumps({"completed": index, "sample_key": sample["sample_key"], "answer_type": answer.answer_type.value}))
    return completed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Eduground golden QA locally with either an in-memory index or local Qdrant.")
    parser.add_argument("--dataset", type=Path, default=Path("apps/evaluator/app/datasets/eduground_100q.seed.json"))
    parser.add_argument("--output", type=Path, default=Path("apps/evaluator/app/datasets/eduground_100q.local.json"))
    parser.add_argument("--database", type=Path, default=Path("tools/evaluation/.local_eval/eduground_eval.sqlite"))
    parser.add_argument("--embedding-batch-size", type=int, default=10)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--vector-store", choices=("local", "qdrant", "hybrid"), default="local")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--qdrant-collection", default="eduground_eval_hnsw")
    parser.add_argument("--keep-qdrant-collection", action="store_true")
    parser.add_argument(
        "--external-ai",
        action="store_true",
        help="Use configured OpenRouter/Gemini providers. By default the runner stays fully offline.",
    )
    args = parser.parse_args()

    configure_local_environment(
        args.database,
        qdrant_url=args.qdrant_url if args.vector_store == "qdrant" else None,
        qdrant_collection=args.qdrant_collection if args.vector_store == "qdrant" else None,
    )
    import sys

    api_root = Path("apps/api").resolve()
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))

    from app.db.init_db import reset_database
    from app.db.session import SessionLocal
    from app.integrations.embeddings.base import OpenRouterEmbeddingProvider

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    reset_database()
    with SessionLocal() as db:
        user, notebook, chunks = create_seed_data(db, dataset)
        embeddings_provider = OpenRouterEmbeddingProvider() if args.external_ai else HashingEmbeddingProvider()
        if args.vector_store == "qdrant":
            store = build_qdrant_vector_store(
                chunks,
                embeddings_provider,
                args.embedding_batch_size,
                notebook.id,
                qdrant_url=args.qdrant_url,
                collection_name=args.qdrant_collection,
                reset_collection=not args.keep_qdrant_collection,
            )
        elif args.vector_store == "hybrid":
            store = build_hybrid_vector_store(
                chunks,
                embeddings_provider,
                args.embedding_batch_size,
                notebook.id,
                qdrant_url=args.qdrant_url,
                collection_name=args.qdrant_collection,
                reset_collection=not args.keep_qdrant_collection,
            )
        else:
            store = build_local_vector_store(chunks, embeddings_provider, args.embedding_batch_size, notebook.id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        completed = run_evaluation(
            db,
            dataset,
            user,
            notebook,
            store,
            args.output,
            args.limit,
            embeddings_provider,
            llm_provider=None if args.external_ai else ExtractiveLLMProvider(),
        )
    print(json.dumps({"output": str(args.output), "sample_count": len(completed), "database": str(args.database)}, indent=2))


if __name__ == "__main__":
    main()
