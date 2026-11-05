"""The worker ledger and the evaluation ledger must operate on one shared file.

Drift between the two implementations would break the single-pilot cap, so
this test exercises the same SQLite database from both sides.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.ledger import BudgetLedger as WorkerLedger  # noqa: E402


def test_worker_and_evaluation_ledgers_share_one_cap_and_state(tmp_path):
    from evaluation.program.budget import BudgetLedger as EvaluationLedger

    path = tmp_path / "budget.sqlite3"
    worker = WorkerLedger(path, "10")
    worker.reserve("w1", "ingestion_embedding", "0.5")
    worker.settle("w1", "0.25")

    evaluation = EvaluationLedger(path, "10")
    state = evaluation.state()
    assert state.settled_usd == Decimal("0.25")
    assert state.by_role_usd["ingestion_embedding"] == Decimal("0.25")

    evaluation.reserve("e1", "answer", "1")
    evaluation.settle("e1", "0.4")
    assert worker.state().settled_usd == Decimal("0.65")

    # An unknown ingestion attempt blocks evaluation dispatch on the same file.
    worker.reserve("w2", "ocr", "0.1")
    worker.settle("w2", None)
    with pytest.raises(ValueError, match="unreconciled usage"):
        evaluation.reserve("e2", "answer", "0.1")

    # And the reconciliation path works from the evaluation side too.
    evaluation.settle("w2", "0.05")
    assert worker.state().unknown_attempts == 0
