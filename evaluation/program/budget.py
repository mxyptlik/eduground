"""Atomic USD reservations for bounded external API attempts.

The caller must provide a defensible maximum for the exact request before
dispatch. An unpriced or unbounded call is not admitted. Unknown final usage
blocks further reservations until it is reconciled; a failed HTTP attempt may
still be billable and must be settled rather than silently released.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


class BudgetError(ValueError):
    pass


def _money(value: Decimal | str | int, *, positive: bool = False) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BudgetError("amount must be finite USD") from exc
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise BudgetError("amount must be finite non-negative USD")
    return amount


@dataclass(frozen=True)
class BudgetState:
    cap_usd: Decimal
    reserved_usd: Decimal
    settled_usd: Decimal
    unknown_attempts: int
    overrun_attempts: int
    by_role_usd: dict[str, Decimal]

    @property
    def committed_usd(self) -> Decimal:
        return self.reserved_usd + self.settled_usd


class BudgetLedger:
    """SQLite ledger shared by all workers of one run.

    The database path belongs inside the immutable run directory. It is
    distinct from RunStore's step journal so budget accounting can commit
    before an outbound call independently of answer/metric success.
    """

    def __init__(self, path: str | Path, cap_usd: Decimal | str | int):
        self.path = Path(path)
        self.cap_usd = _money(cap_usd, positive=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS budget_meta (cap_usd TEXT NOT NULL)")
            row = db.execute("SELECT cap_usd FROM budget_meta").fetchone()
            if row is None:
                db.execute("INSERT INTO budget_meta(cap_usd) VALUES (?)", (str(self.cap_usd),))
            elif Decimal(row[0]) != self.cap_usd:
                raise BudgetError("cannot reopen a budget ledger with a different cap")
            db.execute("""CREATE TABLE IF NOT EXISTS calls (
                call_id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                reserved_usd TEXT NOT NULL,
                actual_usd TEXT,
                status TEXT NOT NULL CHECK(status IN ('reserved','settled','unknown','not_dispatched'))
            )""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @staticmethod
    def _state(db: sqlite3.Connection, cap: Decimal) -> BudgetState:
        rows = db.execute("SELECT role,reserved_usd,actual_usd,status FROM calls").fetchall()
        reserved = Decimal(0)
        settled = Decimal(0)
        unknown = 0
        overruns = 0
        by_role: dict[str, Decimal] = {}
        for role, estimate, actual, status in rows:
            if status == "not_dispatched":
                continue
            if status == "settled":
                value = Decimal(actual)
                settled += value
                overruns += value > Decimal(estimate)
            else:
                value = Decimal(estimate)
                reserved += value
                unknown += status == "unknown"
            by_role[role] = by_role.get(role, Decimal(0)) + value
        return BudgetState(cap, reserved, settled, unknown, overruns, by_role)

    def state(self) -> BudgetState:
        with self._connect() as db:
            return self._state(db, self.cap_usd)

    def reserve(self, call_id: str, role: str, max_usd: Decimal | str | int) -> BudgetState:
        """Reserve a priced worst-case bound before the network request."""
        if not isinstance(call_id, str) or not call_id.strip() or not isinstance(role, str) or not role.strip():
            raise BudgetError("call_id and role are required")
        amount = _money(max_usd)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM calls WHERE call_id=?", (call_id,)).fetchone():
                raise BudgetError("duplicate call_id")
            state = self._state(db, self.cap_usd)
            if state.unknown_attempts:
                raise BudgetError("unreconciled usage blocks new calls")
            if state.overrun_attempts:
                raise BudgetError("a provider exceeded its reserved bound; new calls are blocked")
            if state.committed_usd + amount > self.cap_usd:
                raise BudgetError("USD cap would be exceeded")
            db.execute("INSERT INTO calls VALUES (?,?,?,NULL,'reserved')", (call_id, role, str(amount)))
            return self._state(db, self.cap_usd)

    def settle(self, call_id: str, actual_usd: Decimal | str | int | None) -> BudgetState:
        """Record actual billed cost; None means unknown and stops dispatch."""
        amount = None if actual_usd is None else _money(actual_usd)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM calls WHERE call_id=?", (call_id,)).fetchone()
            if row is None or row[0] not in ("reserved", "unknown"):
                raise BudgetError("call is not an unsettled reservation")
            db.execute(
                "UPDATE calls SET actual_usd=?, status=? WHERE call_id=?",
                (str(amount) if amount is not None else None, "settled" if amount is not None else "unknown", call_id),
            )
            return self._state(db, self.cap_usd)

    def mark_not_dispatched(self, call_id: str) -> BudgetState:
        """Release only a call proven to have stopped before network dispatch."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM calls WHERE call_id=?", (call_id,)).fetchone()
            if row is None or row[0] != "reserved":
                raise BudgetError("only a reserved call can be marked not dispatched")
            db.execute("UPDATE calls SET status='not_dispatched' WHERE call_id=?", (call_id,))
            return self._state(db, self.cap_usd)
