from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from evaluation.program.artifacts import RunStore, StepKey
from evaluation.program.author_run import evidence_hash, run_authoring_batches
from evaluation.program.blueprints import parse_blueprint
from evaluation.program.budget import BudgetLedger
from evaluation.program.config import EvaluationSettings, configuration_hash
from evaluation.program.pricing import PriceRegistry, PriceSchedule, UnitPrice


def _packet():
    return {"course_id": "physics", "notebook_id": "n1", "sources": [
        {"source_id": "s1", "source_label": "Lecture", "checksum_sha256": "a" * 64,
         "version_number": 1, "chunks": [
             {"chunk_id": "c1", "text": "Force equals mass times acceleration.", "start_page": 2},
             {"chunk_id": "c2", "text": "Acceleration changes velocity over time.", "start_page": 3}]}]}


def _blueprint(packet):
    return parse_blueprint({"course_id": "physics", "batches": [
        {"count": 2, "question_type": "factual", "difficulty": "easy",
         "split": "development", "chunk_ids": ["c1"]},
        {"count": 1, "question_type": "factual", "difficulty": "hard",
         "split": "held_out", "chunk_ids": ["c2"]}]}, packet)


def _prices(settings):
    return PriceRegistry("1.0", (PriceSchedule(
        "openrouter", settings.author_model, "author", datetime(2026, 1, 1, tzinfo=timezone.utc),
        "USD", "fixture-only", (UnitPrice("input_tokens", Decimal("1"), 1_000_000),
                               UnitPrice("output_tokens", Decimal("1"), 1_000_000)),
    ),))


class FakeProvider:
    config = SimpleNamespace(max_output_tokens=128)

    def __init__(self):
        self.calls = 0

    def generate_messages_with_result(self, messages):
        self.calls += 1
        if self.calls == 1:
            items = [{"question": "How are force and mass related to acceleration?",
                      "reference_answer": "Force equals mass times acceleration.",
                      "evidence": [{"source_id": "s1", "chunk_id": "c1",
                                    "quote": "Force equals mass times acceleration."}]}]
        else:
            items = []
        return SimpleNamespace(gateway="openrouter", response_model="moonshotai/kimi-k2.6",
                               text=json.dumps({"items": items}), usage={
                                   "prompt_tokens": 100, "completion_tokens": 50, "cost": 0.001})


def test_budgeted_authoring_resumes_without_second_provider_call(tmp_path):
    settings = EvaluationSettings(_env_file=None, max_api_spend_usd=10)
    packet = _packet()
    store = RunStore.create(tmp_path, configuration_hash=configuration_hash(settings.public_snapshot()),
                            dataset_hash=evidence_hash(packet))
    ledger = BudgetLedger(store.path / "budget.sqlite", 10)
    provider = FakeProvider()
    kwargs = dict(store=store, ledger=ledger, prices=_prices(settings), settings=settings,
                  provider=provider, blueprint=_blueprint(packet), evidence_packet=packet,
                  dataset_id="draft-physics", version="1")
    first = run_authoring_batches(**kwargs)
    assert provider.calls == 2
    assert first.deficits == ({"requested": 2, "valid": 1, "deficit": 1},
                              {"requested": 1, "valid": 0, "deficit": 1})
    assert [q.sample_id for q in first.dataset.samples]
    assert first.dataset.samples[0].integrity == "verified"
    assert ledger.state().by_role_usd == {"author": Decimal("0.002")}
    second = run_authoring_batches(**kwargs)
    assert provider.calls == 2 and second.artifact == first.artifact
    assert store.get_step(StepKey("author_batch", "physics-0001")).attempts == 1


def test_mismatched_evidence_run_does_not_dispatch(tmp_path):
    settings = EvaluationSettings(_env_file=None, max_api_spend_usd=10)
    packet = _packet()
    store = RunStore.create(tmp_path, configuration_hash=configuration_hash(settings.public_snapshot()),
                            dataset_hash="0" * 64)
    provider = FakeProvider()
    with pytest.raises(Exception, match="evidence packet differs"):
        run_authoring_batches(store=store, ledger=BudgetLedger(store.path / "budget.sqlite", 10),
            prices=_prices(settings), settings=settings, provider=provider,
            blueprint=_blueprint(packet), evidence_packet=packet, dataset_id="draft", version="1")
    assert provider.calls == 0
