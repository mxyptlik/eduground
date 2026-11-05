from decimal import Decimal

import pytest

from evaluation.program.budget import BudgetError, BudgetLedger


def test_cap_and_individual_role_totals(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", "10")
    ledger.reserve("answer-1", "gemini_answer", "3")
    ledger.reserve("judge-1", "deepseek_judge", "6")
    with pytest.raises(BudgetError, match="cap"):
        ledger.reserve("author-1", "kimi_author", "2")
    ledger.settle("answer-1", "1.5")
    state = ledger.reserve("author-1", "kimi_author", "2")
    assert state.committed_usd == Decimal("9.5")
    assert state.by_role_usd == {
        "gemini_answer": Decimal("1.5"), "deepseek_judge": Decimal("6"),
        "kimi_author": Decimal("2"),
    }


def test_unknown_usage_blocks_more_calls(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", 10)
    ledger.reserve("a", "judge", "1")
    ledger.settle("a", None)
    with pytest.raises(BudgetError, match="unreconciled"):
        ledger.reserve("b", "answer", "1")
    ledger.settle("a", "0.4")
    ledger.reserve("b", "answer", "1")


def test_overrun_is_recorded_and_halts_next_call(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", 10)
    ledger.reserve("a", "judge", "9")
    state = ledger.settle("a", "11")
    assert state.settled_usd == 11
    assert state.overrun_attempts == 1
    with pytest.raises(BudgetError, match="reserved bound"):
        ledger.reserve("b", "answer", 0)


def test_release_only_when_not_dispatched_and_cap_immutable(tmp_path):
    path = tmp_path / "budget.sqlite3"
    ledger = BudgetLedger(path, 10)
    ledger.reserve("a", "judge", 10)
    ledger.mark_not_dispatched("a")
    assert ledger.state().committed_usd == 0
    with pytest.raises(BudgetError):
        ledger.mark_not_dispatched("a")
    with pytest.raises(BudgetError, match="different cap"):
        BudgetLedger(path, 12)


def test_unbounded_or_duplicate_attempt_rejected(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", 10)
    with pytest.raises(BudgetError):
        ledger.reserve("a", "judge", None)
    ledger.reserve("a", "judge", 1)
    with pytest.raises(BudgetError, match="duplicate"):
        ledger.reserve("a", "judge", 1)
