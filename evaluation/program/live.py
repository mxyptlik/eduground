"""Live wiring for one controlled collection run.

Construction registers adapters and verifies frozen source versions; it makes
no provider request. Every outbound call is dispatched through the shared
budget ledger guards, so a run cannot silently exceed its USD cap or route to
an unpinned model.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from evaluation.program.budget import BudgetLedger
from evaluation.program.budgeted_call import invoke_budgeted_role
from evaluation.program.budgeted_embeddings import build_budgeted_embedding_provider
from evaluation.program.budgeted_rerank import build_budgeted_reranker
from evaluation.program.collector import CITATION_MARKER_INSTRUCTION, rerank_order
from evaluation.program.conditions import Candidate, RerankOrder
from evaluation.program.config import EvaluationSettings
from evaluation.program.contracts import CourseDataset
from evaluation.program.pricing import PriceRegistry
from evaluation.program.providers import build_role_provider
from evaluation.program.runner import GenerationOutcome, RetrievalFailure, RunnerError


DEFAULT_ANSWER_MAX_OUTPUT_TOKENS = 1536

_CREDENTIAL_ENV = {
    "answer": "CURRICULUM_TUTOR_GEMINI_API_KEY",
    "query_embedding": "CURRICULUM_TUTOR_OPENROUTER_API_KEY",
    "rerank": "CURRICULUM_TUTOR_OPENROUTER_API_KEY",
}


def _api_root() -> Path:
    return Path(__file__).resolve().parents[2] / "apps" / "api"


def _ensure_api_path() -> None:
    root = str(_api_root())
    if root not in sys.path:
        sys.path.insert(0, root)


@dataclass
class LiveCollection:
    dataset: CourseDataset
    grounded_system_prompt: str
    retrieve: Callable[[str], tuple[Sequence[Any], Mapping[str, str] | None]] | None
    rerank: Callable[[str, Sequence[Candidate]], RerankOrder] | None
    generate: Callable[[list[dict[str, str]]], GenerationOutcome]
    close: Callable[[], None]


def verify_frozen_sources(session: Any, dataset: CourseDataset) -> dict[str, str]:
    """Return current indexed source-version IDs matching the frozen manifest.

    Any missing, changed, non-indexed or inconsistent source raises instead of
    letting a run collect answers against a corpus that drifted since freeze.
    """
    _ensure_api_path()
    from app.models.content import Source, SourceVersion
    from app.models.enums import SourceStatus

    versions: dict[str, str] = {}
    for manifest in dataset.source_manifest:
        source = session.get(Source, manifest.source_id)
        if source is None or getattr(source, "deleted_at", None) is not None or source.status != SourceStatus.INDEXED:
            raise RunnerError(f"source {manifest.source_id} is not currently indexed")
        if source.notebook_id != dataset.notebook_id:
            raise RunnerError(f"source {manifest.source_id} is outside the evaluation notebook")
        if str(source.checksum_sha256).lower() != manifest.sha256.lower():
            raise RunnerError(f"source {manifest.source_id} checksum changed since freeze")
        if manifest.version_number is not None and source.version_number != manifest.version_number:
            raise RunnerError(f"source {manifest.source_id} version changed since freeze")
        version = (
            session.query(SourceVersion)
            .filter(SourceVersion.source_id == source.id,
                    SourceVersion.version_number == source.version_number)
            .one_or_none()
        )
        if (version is None or str(version.checksum_sha256).lower() != str(source.checksum_sha256).lower()
                or version.status != SourceStatus.INDEXED.value):
            raise RunnerError(f"source {manifest.source_id} current version is missing or inconsistent")
        versions[source.id] = version.id
    return versions


def collection_preflight(
    settings: EvaluationSettings, *, need_retrieval: bool, need_rerank: bool,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Return every blocker for a live collection run without making a call."""
    issues: list[str] = []
    source = os.environ if environ is None else environ
    try:
        settings.preflight(require_paid_calls=True,
                           require_ingestion_embedding=need_retrieval,
                           require_relevance_embedding=False)
    except ValueError as exc:
        issues.append(str(exc))
    if need_retrieval and settings.embedding_provider != "openrouter":
        issues.append("controlled query embeddings currently require a pinned OpenRouter model")
    needed = ["answer"]
    if need_retrieval:
        needed.append("query_embedding")
        if not settings.embedding_model:
            issues.append("pin EDUGROUND_EVAL_EMBEDDING_MODEL for retrieval conditions")
    if need_rerank:
        needed.append("rerank")
        if not settings.reranker_model:
            issues.append("pin EDUGROUND_EVAL_RERANKER_MODEL for the rerank condition")
    missing = sorted({_CREDENTIAL_ENV[name] for name in needed if not str(source.get(_CREDENTIAL_ENV[name], "")).strip()})
    if missing:
        issues.append("missing credential environment variables (values not shown): " + ", ".join(missing))
    return issues


def build_live_collection(
    settings: EvaluationSettings, dataset: CourseDataset, *, ledger: BudgetLedger,
    prices: PriceRegistry, environ: Mapping[str, str] | None = None,
    verify_versions: bool = True, need_retrieval: bool = True, need_rerank: bool = False,
) -> LiveCollection:
    """Construct the ledger-guarded retrieve/rerank/generate callables."""
    if settings.max_api_spend_usd is None or Decimal(str(settings.max_api_spend_usd)) != ledger.cap_usd:
        raise RunnerError("run settings and budget ledger must share the explicit USD cap")
    source = os.environ if environ is None else environ
    issues = collection_preflight(settings, need_retrieval=need_retrieval, need_rerank=need_rerank, environ=source)
    if issues:
        raise RunnerError("collection preflight failed: " + "; ".join(issues))
    _ensure_api_path()

    from app.db.session import SessionLocal
    from app.integrations.vectorstore.qdrant_adapter import QdrantClientConfig, QdrantVectorStore
    from app.models.content import Chunk, Source
    from app.models.enums import SourceStatus
    from app.policies.scoping import build_retrieval_filters
    from app.models.enums import PolicyMode
    from app.services.prompt_library import compose_tutor_system_prompt
    from app.core.config import settings as api_settings

    version_map: dict[str, str] | None = None
    if verify_versions:
        with SessionLocal() as session:
            version_map = verify_frozen_sources(session, dataset)

    source_ids = [manifest.source_id for manifest in dataset.source_manifest]
    open_clients: list[Any] = []

    retrieve = None
    if need_retrieval:
        embeddings, _requester = build_budgeted_embedding_provider(
            settings, operation="query_embedding", ledger=ledger, prices=prices, environ=source)
        open_clients.append(embeddings)
        vector_store = QdrantVectorStore(QdrantClientConfig(
            url=api_settings.qdrant_url,
            api_key=api_settings.qdrant_api_key,
            collection_name=api_settings.qdrant_collection_name,
            timeout_seconds=60.0,
            vector_size=None,
            distance="Cosine",
        ))
        filters = build_retrieval_filters(dataset.notebook_id, source_ids=source_ids, module_ids=[])

        def retrieve(question: str) -> tuple[Sequence[Any], Mapping[str, str] | None]:
            try:
                vectors = embeddings.embed([question])
            except Exception as exc:
                raise RetrievalFailure("embedding_provider_failed") from exc
            if not vectors:
                raise RetrievalFailure("query_embedding_empty")
            try:
                results = vector_store.search(vectors[0], filters, top_k=settings.candidate_k)
            except Exception as exc:
                raise RetrievalFailure("vector_store_failed") from exc
            records: list[Any] = []
            with SessionLocal() as session:
                for result in results:
                    payload = result.get("payload") or {}
                    chunk_id = payload.get("chunk_id")
                    source_id = payload.get("source_id")
                    if not chunk_id or not source_id:
                        continue
                    chunk = session.get(Chunk, chunk_id)
                    source_row = session.get(Source, source_id)
                    if (chunk is None or source_row is None
                            or source_row.status != SourceStatus.INDEXED
                            or source_row.notebook_id != dataset.notebook_id):
                        continue
                    records.append(SimpleNamespace(
                        chunk=chunk, source=source_row, score=float(result.get("score") or 0.0),
                        rank_before=len(records) + 1, rank_after=len(records) + 1, rerank_score=None,
                    ))
            return records, version_map

    rerank = None
    if need_rerank:
        max_input_tokens = (3000 * settings.candidate_k + 4096) // 3 + 1024
        provider = build_budgeted_reranker(settings, ledger=ledger, prices=prices,
                                           max_input_tokens=max_input_tokens, environ=dict(source))
        open_clients.append(provider)

        def rerank(question: str, candidates: Sequence[Candidate]) -> RerankOrder:
            records = [
                SimpleNamespace(
                    chunk=SimpleNamespace(id=candidate.chunk_id, text=candidate.text),
                    source=SimpleNamespace(id=candidate.source_id),
                    score=candidate.initial_score, rank_before=candidate.rank_before,
                    rank_after=None, rerank_score=None,
                )
                for candidate in candidates
            ]
            return rerank_order(question, records, provider)

    answer_provider = build_role_provider(settings, "answer", environ=source)
    open_clients.append(answer_provider)
    configured_output = getattr(getattr(answer_provider, "config", None), "max_output_tokens", None)
    max_output_tokens = configured_output if isinstance(configured_output, int) and configured_output > 0 else DEFAULT_ANSWER_MAX_OUTPUT_TOKENS

    def generate(messages: list[dict[str, str]]) -> GenerationOutcome:
        max_input_tokens = max(
            len(json.dumps(messages, ensure_ascii=False).encode("utf-8")),
            len(json.dumps(messages, ensure_ascii=True).encode("utf-8")),
        ) + 2048
        call = invoke_budgeted_role(
            settings, "answer", answer_provider, messages, ledger=ledger, prices=prices,
            max_input_tokens=max_input_tokens, max_output_tokens=max_output_tokens,
        )
        result = call.result
        return GenerationOutcome(
            text=result.text,
            model_id=getattr(result, "response_model", None),
            usage=dict(result.usage) if isinstance(getattr(result, "usage", None), dict) else None,
            cost_usd=str(call.cost.amount) if call.cost is not None and call.cost.amount is not None else None,
            reserved_usd=str(call.reserved_usd),
            request_id=getattr(result, "request_id", None),
        )

    def close() -> None:
        for client in open_clients:
            closer = getattr(client, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass

    grounded_system_prompt = compose_tutor_system_prompt(PolicyMode.TEACHING) + CITATION_MARKER_INSTRUCTION
    return LiveCollection(dataset=dataset, grounded_system_prompt=grounded_system_prompt,
                          retrieve=retrieve, rerank=rerank if need_rerank else None,
                          generate=generate, close=close)
