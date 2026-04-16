from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ApiServiceError
from app.integrations.llm.base import LLMProviderError, OpenRouterLLMProvider
from app.models.curriculum import Notebook
from app.models.enums import NoteVisibility, PolicyMode, QuizGenerationMode, QuizItemType, QuizStatus
from app.models.learning import Note, Quiz, QuizItem
from app.policies.response_mode_policy import resolve_policy_instructions
from app.workflows.answer_question import AnswerQuestionWorkflow, CandidateRecord, NoteContextRecord, PreparedGroundedAnswer

MAX_QUIZ_GENERATION_ATTEMPTS = 3


@dataclass(slots=True)
class GeneratedQuizItem:
    prompt_text: str
    options: list[str]
    correct_answer: str
    rationale_markdown: str


class GenerateQuizWorkflow:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.llm = OpenRouterLLMProvider()

    def run(self, *, notebook_id: str, payload, user) -> tuple[Quiz, list[QuizItem]]:
        notebook = self._load_notebook(notebook_id)
        answer_workflow = self._build_answer_workflow()
        prepared = self._prepare_quiz_context(
            notebook=notebook,
            notebook_id=notebook_id,
            payload=payload,
            user=user,
            answer_workflow=answer_workflow,
        )
        if not prepared.candidates and not prepared.note_contexts:
            raise ApiServiceError(
                code="quiz_generation_no_context",
                message="No indexed notebook evidence was available to generate a grounded quiz inside the selected scope.",
                status_code=422,
                retryable=False,
            )

        generated_items = self._generate_quiz_items(payload=payload, prepared=prepared)
        if not generated_items:
            raise ApiServiceError(
                code="quiz_generation_empty",
                message="Quiz generation did not return any usable quiz items.",
                status_code=502,
                retryable=True,
            )

        created_at = datetime.now(UTC)
        quiz = Quiz(
            notebook_id=notebook_id,
            created_by_user_id=user.id,
            title=payload.title,
            difficulty=payload.difficulty,
            scope_json={
                "source_ids": list(getattr(payload, "source_ids", []) or []),
                "note_ids": list(getattr(payload, "note_ids", []) or []),
                "module_ids": list(getattr(payload, "module_ids", []) or []),
                "candidate_count": len(prepared.candidates),
                "note_context_count": len(prepared.note_contexts),
            },
            generation_mode=QuizGenerationMode.TUTOR_GENERATED,
            status=QuizStatus.DRAFT,
            created_at=created_at,
        )
        items = [
            QuizItem(
                quiz_id="",
                item_type=QuizItemType.MCQ,
                prompt_text=item.prompt_text,
                options_json=item.options,
                correct_answer_json={"choice": item.correct_answer},
                rationale_markdown=item.rationale_markdown,
                position=index,
                created_at=created_at,
            )
            for index, item in enumerate(generated_items, start=1)
        ]
        return quiz, items

    def _build_answer_workflow(self) -> AnswerQuestionWorkflow:
        return AnswerQuestionWorkflow(self.db)

    def _prepare_quiz_context(
        self,
        *,
        notebook,
        notebook_id: str,
        payload,
        user,
        answer_workflow: AnswerQuestionWorkflow,
    ) -> PreparedGroundedAnswer:
        selected_source_ids = list(getattr(payload, "source_ids", []) or [])
        selected_note_ids = list(getattr(payload, "note_ids", []) or [])
        selected_module_ids = list(getattr(payload, "module_ids", []) or [])
        retrieval_query = self._build_retrieval_query(payload)
        if selected_note_ids and not selected_source_ids and not selected_module_ids:
            note_contexts = self._load_selected_note_contexts(
                notebook_id=notebook_id,
                user_id=user.id,
                note_ids=selected_note_ids,
            )
            policy = resolve_policy_instructions(getattr(notebook, "policy_mode", PolicyMode.TEACHING))
            return PreparedGroundedAnswer(
                session_id=f"quiz:{notebook_id}:{user.id}",
                notebook_id=notebook_id,
                query_text=retrieval_query,
                filters={"notebook_id": notebook_id, "note_ids": selected_note_ids, "scope": "notes_only"},
                system_prompt="\n".join(policy.system_rules),
                context=self._build_context([], note_contexts),
                candidates=[],
                note_contexts=note_contexts,
                retrieval_started_at=0.0,
            )

        retrieval_session = SimpleNamespace(
            id=f"quiz:{notebook_id}:{user.id}",
            notebook_id=notebook_id,
            user_id=user.id,
            policy_mode_snapshot=getattr(notebook, "policy_mode", PolicyMode.TEACHING),
        )
        retrieval_message = SimpleNamespace(content_markdown=retrieval_query)
        prepared = answer_workflow.prepare_grounded_answer(
            session=retrieval_session,
            user_message=retrieval_message,
            selected_source_ids=selected_source_ids,
            selected_module_ids=selected_module_ids,
        )
        if selected_note_ids:
            selected_notes = self._load_selected_note_contexts(
                notebook_id=notebook_id,
                user_id=user.id,
                note_ids=selected_note_ids,
            )
            merged_note_contexts = self._merge_note_contexts(prepared.note_contexts, selected_notes)
            prepared = PreparedGroundedAnswer(
                session_id=prepared.session_id,
                notebook_id=prepared.notebook_id,
                query_text=prepared.query_text,
                filters={**prepared.filters, "note_ids": selected_note_ids},
                system_prompt=prepared.system_prompt,
                context=self._build_context(prepared.candidates, merged_note_contexts),
                candidates=prepared.candidates,
                note_contexts=merged_note_contexts,
                retrieval_started_at=prepared.retrieval_started_at,
            )
        return prepared

    def _load_notebook(self, notebook_id: str) -> Notebook | SimpleNamespace:
        if self.db is None:
            return SimpleNamespace(policy_mode=PolicyMode.TEACHING)
        notebook = self.db.get(Notebook, notebook_id)
        if notebook is None:
            return SimpleNamespace(policy_mode=PolicyMode.TEACHING)
        return notebook

    def _build_retrieval_query(self, payload) -> str:
        title = str(getattr(payload, "title", "")).strip() or "Notebook quiz"
        difficulty = str(getattr(payload, "difficulty", "mixed")).strip() or "mixed"
        item_count = int(getattr(payload, "item_count", 3) or 3)
        return (
            f"Key concepts, definitions, and assessable ideas for {title}. "
            f"Generate {item_count} grounded {difficulty} quiz questions from the strongest notebook evidence."
        )

    def _load_selected_note_contexts(self, *, notebook_id: str, user_id: str, note_ids: list[str]) -> list[NoteContextRecord]:
        if self.db is None or not note_ids:
            return []
        notes = (
            self.db.query(Note)
            .filter(Note.notebook_id == notebook_id)
            .filter(Note.id.in_(note_ids))
            .all()
        )
        ordered_notes = {note.id: note for note in notes}
        selected: list[NoteContextRecord] = []
        for note_id in note_ids:
            note = ordered_notes.get(note_id)
            if note is None:
                continue
            if note.visibility == NoteVisibility.PRIVATE and note.author_user_id != user_id:
                continue
            content = f"{note.title}\n{note.content_markdown}".strip()
            if not content:
                continue
            selected.append(
                NoteContextRecord(
                    note_id=note.id,
                    title=note.title,
                    content_markdown=content[:1_200],
                    score=999.0,
                )
            )
        return selected

    def _merge_note_contexts(
        self,
        existing: list[NoteContextRecord],
        selected: list[NoteContextRecord],
    ) -> list[NoteContextRecord]:
        merged: list[NoteContextRecord] = []
        seen: set[str] = set()
        for item in [*selected, *existing]:
            if item.note_id in seen:
                continue
            seen.add(item.note_id)
            merged.append(item)
        return merged

    def _generate_quiz_items(self, *, payload, prepared: PreparedGroundedAnswer) -> list[GeneratedQuizItem]:
        messages = self._build_generation_messages(payload=payload, prepared=prepared)
        requested_count = int(getattr(payload, "item_count", 3) or 3)
        for attempt in range(1, MAX_QUIZ_GENERATION_ATTEMPTS + 1):
            try:
                raw_response = self.llm.generate_messages(messages)
            except LLMProviderError as exc:
                raise ApiServiceError(
                    code="quiz_generation_provider_error",
                    message="Quiz generation provider request failed.",
                    status_code=exc.status_code,
                    retryable=exc.retryable,
                    provider=exc.provider,
                    details=exc.details,
                ) from exc
            items = self._parse_generated_items(raw_response)
            if len(items) >= requested_count:
                return items[:requested_count]
            messages = self._extend_retry_messages(
                messages=messages,
                requested_count=requested_count,
                actual_count=len(items),
                attempt=attempt,
            )
        raise ApiServiceError(
            code="quiz_generation_invalid_response",
            message="Quiz generation returned an invalid response format.",
            status_code=502,
            retryable=True,
        )

    def _build_generation_messages(self, *, payload, prepared: PreparedGroundedAnswer) -> list[dict[str, Any]]:
        evidence_blocks: list[str] = []
        for index, candidate in enumerate(prepared.candidates, start=1):
            evidence_blocks.append(self._format_candidate_block(index, candidate))
        for index, note in enumerate(prepared.note_contexts, start=1):
            evidence_blocks.append(self._format_note_block(index, note))
        evidence_text = "\n\n".join(evidence_blocks) if evidence_blocks else prepared.context
        system_prompt = (
            "You generate grounded multiple-choice quiz items for a study notebook. "
            "Only use the supplied evidence. Do not invent facts or outside examples. "
            "Keep the language clear, concise, and student-friendly. "
            "Return strict JSON only with the shape "
            '{"items":[{"prompt":"...","options":["...","...","...","..."],"correct_answer":"...","rationale":"..."}]}. '
            "Each item must have exactly four distinct options, one correct answer that exactly matches one option, "
            "and a short rationale grounded in the evidence. "
            "The options must be genuinely different from each other, not reworded duplicates. "
            "Vary the focus of the questions across the evidence when possible. "
            "Do not wrap the JSON in markdown fences."
        )
        if prepared.system_prompt.strip():
            system_prompt = f"{prepared.system_prompt}\n\n{system_prompt}"
        user_prompt = (
            f"Notebook quiz title: {payload.title}\n"
            f"Difficulty: {payload.difficulty}\n"
            f"Requested item count: {payload.item_count}\n\n"
            "Use the evidence below to write grounded quiz items that test understanding, application, and comparison, not trivia. "
            "Match the requested difficulty level. "
            "Ensure the correct answers are not predictable by position across the set.\n\n"
            f"Evidence:\n{evidence_text}"
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _extend_retry_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        requested_count: int,
        actual_count: int,
        attempt: int,
    ) -> list[dict[str, Any]]:
        return [
            *messages,
            {
                "role": "user",
                "content": (
                    f"The previous response was unusable because it produced {actual_count} valid items and {requested_count} are required. "
                    f"Retry attempt {attempt + 1}. Return JSON only. "
                    "Every item must have four distinct options and one exact correct_answer that matches one option."
                ),
            },
        ]

    def _format_candidate_block(self, index: int, candidate: CandidateRecord) -> str:
        label = candidate.source.title
        if candidate.chunk.start_page:
            label = f"{label}, page {candidate.chunk.start_page}"
        elif candidate.chunk.start_slide:
            label = f"{label}, slide {candidate.chunk.start_slide}"
        return f"[Source {index}] {label}\n{candidate.chunk.text[:900].strip()}"

    def _format_note_block(self, index: int, note: NoteContextRecord) -> str:
        return f"[Notebook Note {index}] {note.title}\n{note.content_markdown[:700].strip()}"

    def _parse_generated_items(self, raw_response: str) -> list[GeneratedQuizItem]:
        payload = self._coerce_json_payload(raw_response)
        raw_items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            return []
        items: list[GeneratedQuizItem] = []
        seen_prompts: set[str] = set()
        for raw_item in raw_items:
            parsed = self._normalize_item(raw_item)
            if parsed is None:
                continue
            prompt_key = parsed.prompt_text.casefold()
            if prompt_key in seen_prompts:
                continue
            seen_prompts.add(prompt_key)
            items.append(parsed)
        return items

    def _coerce_json_payload(self, raw_response: str) -> Any:
        text = raw_response.strip()
        fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        for candidate in (text, self._extract_json_object(text), self._extract_json_array(text)):
            if not candidate:
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return {}

    def _extract_json_object(self, text: str) -> str | None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        return text[start : end + 1]

    def _extract_json_array(self, text: str) -> str | None:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return None
        return text[start : end + 1]

    def _normalize_item(self, raw_item: Any) -> GeneratedQuizItem | None:
        if not isinstance(raw_item, dict):
            return None
        prompt_text = self._normalize_text(raw_item.get("prompt"))
        rationale = self._normalize_text(raw_item.get("rationale")) or "Grounded in the retrieved notebook evidence."
        raw_options = raw_item.get("options")
        if not prompt_text or not isinstance(raw_options, list):
            return None
        options = self._normalize_options(raw_options)
        if len(options) != 4:
            return None
        if len({option.casefold() for option in options}) != 4:
            return None
        correct_answer = self._match_correct_answer(raw_item.get("correct_answer"), options)
        if correct_answer is None:
            return None
        return GeneratedQuizItem(
            prompt_text=prompt_text,
            options=options,
            correct_answer=correct_answer,
            rationale_markdown=rationale,
        )

    def _normalize_options(self, raw_options: list[Any]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_options:
            option = self._normalize_text(item)
            if not option:
                continue
            key = option.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(option)
        return normalized[:4]

    def _match_correct_answer(self, raw_answer: Any, options: list[str]) -> str | None:
        answer = self._normalize_text(raw_answer)
        if not answer:
            return None
        for option in options:
            if option.casefold() == answer.casefold():
                return option
        return None

    def _normalize_text(self, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        normalized = re.sub(r"\s+", " ", value).strip()
        return normalized

    def _build_context(self, candidates: list[CandidateRecord], note_contexts: list[NoteContextRecord]) -> str:
        parts: list[str] = []
        for item in candidates:
            parts.append(item.chunk.text)
        for note in note_contexts:
            parts.append(f"Notebook note: {note.title}\n{note.content_markdown}")
        return "\n\n".join(part for part in parts if part.strip())
