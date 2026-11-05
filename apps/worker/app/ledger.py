"""Shared-budget enforcement for evaluation ingestion calls (embeddings, OCR).

This module is active only when ``EDUGROUND_EVAL_BUDGET_LEDGER`` is set for the
worker process; otherwise ingestion behaves exactly like the product default.
The SQLite file is the *same* ledger used by ``evaluation/program/budget.py``
(the schema is mirrored intentionally and cross-checked by tests), so every
role in one evaluation pilot shares one cap: Gemini/Kimi/DeepSeek/reranker,
query embeddings, ingestion embeddings and OCR.

Rules enforced here:
- An unpriced or unbounded call is refused before dispatch.
- A provider response must identify its model and report usage before the call
  is recorded as settled; anything unverifiable settles as ``unknown`` and
  blocks further reservations until reconciled.
- A failed dispatch keeps its reservation as ``unknown`` because a failed HTTP
  attempt may still be billable.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4


LEDGER_ENV = "EDUGROUND_EVAL_BUDGET_LEDGER"
CAP_ENV = "EDUGROUND_EVAL_MAX_API_SPEND_USD"
REGISTRY_ENV = "EDUGROUND_EVAL_PRICE_REGISTRY"

# Service keys used in price-registry entries for worker ingestion operations.
UNSTRUCTURED_PROVIDER = "unstructured"
UNSTRUCTURED_SERVICE_ID = "hosted-api"
OCR_OPERATION = "ocr"
INGESTION_EMBEDDING_OPERATION = "ingestion_embedding"
VISUAL_UNDERSTANDING_OPERATION = "visual_understanding"

_BOUND_UNITS = frozenset({"input_tokens", "cached_input_tokens", "output_tokens",
                          "cached_output_tokens", "requests", "pages"})


class LedgerError(RuntimeError):
    """A guarded call cannot be reserved, verified or settled safely."""


class LedgerConfigurationError(LedgerError):
    """The evaluation ledger environment is incomplete or inconsistent."""


def _money(value: Decimal | str | int, *, positive: bool = False) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LedgerError("amount must be finite USD") from exc
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise LedgerError("amount must be finite non-negative USD")
    return amount


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerError("effective_from must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LedgerError("effective_from must have a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class UnitPrice:
    unit: str
    amount: Decimal
    quantity: int


@dataclass(frozen=True)
class PriceSchedule:
    provider: str
    model: str
    operation: str
    effective_from: datetime
    currency: str
    source: str
    prices: tuple[UnitPrice, ...]

    def rates(self) -> dict[str, Decimal]:
        return {price.unit: price.amount / Decimal(price.quantity) for price in self.prices}


def _load_schedule(entry: Mapping[str, Any]) -> PriceSchedule:
    try:
        prices = tuple(
            UnitPrice(unit=price["unit"], amount=Decimal(str(price["amount"])), quantity=int(price["quantity"]))
            for price in entry["prices"]
        )
        schedule = PriceSchedule(
            provider=str(entry["provider"]), model=str(entry["model"]), operation=str(entry["operation"]),
            effective_from=_timestamp(str(entry["effective_from"])), currency=str(entry["currency"]),
            source=str(entry["source"]), prices=prices,
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise LedgerConfigurationError("price registry entry is malformed") from exc
    if any(unit not in _BOUND_UNITS for unit in schedule.rates()):
        unsupported = sorted(set(schedule.rates()) - _BOUND_UNITS)
        raise LedgerConfigurationError(f"unsupported price units for the worker guard: {', '.join(unsupported)}")
    if any(price.quantity <= 0 or price.amount < 0 or not price.amount.is_finite() for price in prices):
        raise LedgerConfigurationError("price amounts must be finite and non-negative with a positive quantity")
    return schedule


class PriceRegistry:
    """Minimal reader for the shared dated price registry JSON format."""

    def __init__(self, schedules: tuple[PriceSchedule, ...]) -> None:
        self.schedules = schedules

    @classmethod
    def from_json(cls, path: str | Path) -> PriceRegistry:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LedgerConfigurationError(f"price registry cannot be read: {path}") from exc
        if not isinstance(payload, dict) or payload.get("version") != "1.0" or not isinstance(payload.get("schedules"), list):
            raise LedgerConfigurationError("price registry must be version 1.0 with a schedules list")
        return cls(tuple(_load_schedule(entry) for entry in payload["schedules"]))

    def schedule_for(self, provider: str, model: str, operation: str, when: datetime) -> PriceSchedule | None:
        matches = [
            entry for entry in self.schedules
            if entry.provider == provider and entry.model == model and entry.operation == operation
            and entry.effective_from <= when
        ]
        return max(matches, key=lambda entry: entry.effective_from, default=None)


def maximum_cost(schedule: PriceSchedule, *, max_input_tokens: int = 0, max_output_tokens: int = 0,
                 requests: int = 0, pages: int = 0) -> Decimal:
    """Conservative worst-case bound over the same units the guard settles."""
    for label, value in (("max_input_tokens", max_input_tokens), ("max_output_tokens", max_output_tokens),
                         ("requests", requests), ("pages", pages)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise LedgerError(f"{label} must be a non-negative integer")
    rates = schedule.rates()
    if not rates:
        raise LedgerError("price schedule has no billable units")
    input_rate = max(rates.get("input_tokens", Decimal(0)), rates.get("cached_input_tokens", Decimal(0)))
    output_rate = max(rates.get("output_tokens", Decimal(0)), rates.get("cached_output_tokens", Decimal(0)))
    return (Decimal(max_input_tokens) * input_rate
            + Decimal(max_output_tokens) * output_rate
            + Decimal(requests) * rates.get("requests", Decimal(0))
            + Decimal(pages) * rates.get("pages", Decimal(0)))


def settlement_cost(schedule: PriceSchedule, usage: Mapping[str, int | None]) -> Decimal:
    """Exact cost from verified usage; a missing priced unit raises (unknown)."""
    total = Decimal(0)
    for unit, rate in schedule.rates().items():
        count = usage.get(unit)
        if count is None:
            raise LedgerError(f"usage does not report the priced unit {unit}")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise LedgerError(f"usage for {unit} must be a non-negative integer")
        total += Decimal(count) * rate
    return total


@dataclass(frozen=True)
class LedgerState:
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
    """Worker-side twin of evaluation/program/budget.py over the same schema."""

    def __init__(self, path: str | Path, cap_usd: Decimal | str | int) -> None:
        self.path = Path(path)
        self.cap_usd = _money(cap_usd, positive=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS budget_meta (cap_usd TEXT NOT NULL)")
            row = db.execute("SELECT cap_usd FROM budget_meta").fetchone()
            if row is None:
                db.execute("INSERT INTO budget_meta(cap_usd) VALUES (?)", (str(self.cap_usd),))
            elif Decimal(row[0]) != self.cap_usd:
                raise LedgerError("cannot reopen a budget ledger with a different cap")
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
    def _state(db: sqlite3.Connection, cap: Decimal) -> LedgerState:
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
        return LedgerState(cap, reserved, settled, unknown, overruns, by_role)

    def state(self) -> LedgerState:
        with self._connect() as db:
            return self._state(db, self.cap_usd)

    def reserve(self, call_id: str, role: str, max_usd: Decimal | str | int) -> LedgerState:
        if not isinstance(call_id, str) or not call_id.strip() or not isinstance(role, str) or not role.strip():
            raise LedgerError("call_id and role are required")
        amount = _money(max_usd)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM calls WHERE call_id=?", (call_id,)).fetchone():
                raise LedgerError("duplicate call_id")
            state = self._state(db, self.cap_usd)
            if state.unknown_attempts:
                raise LedgerError("unreconciled usage blocks new calls")
            if state.overrun_attempts:
                raise LedgerError("a provider exceeded its reserved bound; new calls are blocked")
            if state.committed_usd + amount > self.cap_usd:
                raise LedgerError("USD cap would be exceeded")
            db.execute("INSERT INTO calls VALUES (?,?,?,NULL,'reserved')", (call_id, role, str(amount)))
            return self._state(db, self.cap_usd)

    def settle(self, call_id: str, actual_usd: Decimal | str | int | None) -> LedgerState:
        amount = None if actual_usd is None else _money(actual_usd)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM calls WHERE call_id=?", (call_id,)).fetchone()
            if row is None or row[0] not in ("reserved", "unknown"):
                raise LedgerError("call is not an unsettled reservation")
            db.execute(
                "UPDATE calls SET actual_usd=?, status=? WHERE call_id=?",
                (str(amount) if amount is not None else None, "settled" if amount is not None else "unknown", call_id),
            )
            return self._state(db, self.cap_usd)


@dataclass(frozen=True)
class GuardedCall:
    call_id: str
    reserved_usd: Decimal
    actual_usd: Decimal
    request_id: str | None


class LedgerGuard:
    """Reserve -> dispatch -> verify identity/usage -> settle, for one run cap."""

    def __init__(self, ledger: BudgetLedger, registry: PriceRegistry) -> None:
        self.ledger = ledger
        self.registry = registry
        self.calls: list[GuardedCall] = []

    def schedule(self, *, provider: str, model: str, operation: str) -> PriceSchedule:
        schedule = self.registry.schedule_for(provider, model, operation, datetime.now(timezone.utc))
        if schedule is None or schedule.currency != "USD":
            raise LedgerConfigurationError(
                f"dated USD price schedule missing for {provider}/{model}/{operation}")
        return schedule

    def invoke(
        self, *, role: str, provider: str, model: str, operation: str,
        bounds: Mapping[str, int], dispatch: Callable[[], Any],
        extract: Callable[[Any], tuple[str | None, Mapping[str, int | None], Decimal | None]],
    ) -> Any:
        schedule = self.schedule(provider=provider, model=model, operation=operation)
        maximum = maximum_cost(schedule, **bounds)
        call_id = uuid4().hex
        self.ledger.reserve(call_id, role, maximum)
        try:
            raw = dispatch()
        except Exception:
            # A failed attempt may still be billed; keep the reservation as unknown.
            self.ledger.settle(call_id, None)
            raise
        try:
            response_model, usage, reported_cost = extract(raw)
            if response_model != model:
                raise LedgerError(
                    f"provider response identified {response_model!r}, not the pinned {model!r}")
            actual = reported_cost if reported_cost is not None else settlement_cost(schedule, usage)
        except LedgerError:
            self.ledger.settle(call_id, None)
            raise
        except Exception as exc:
            self.ledger.settle(call_id, None)
            raise LedgerError("provider response lacks verifiable model or usage") from exc
        state = self.ledger.settle(call_id, actual)
        if actual > maximum:
            raise LedgerError("provider cost exceeded the reserved per-call bound")
        if state.committed_usd > state.cap_usd:
            raise LedgerError("provider cost exceeded the reserved bound and run cap")
        self.calls.append(GuardedCall(call_id, maximum, actual, None))
        return raw


def load_ledger_guard(environ: Mapping[str, str] | None = None) -> LedgerGuard | None:
    """Build the guard from the process environment, or None when not opted in.

    A partially configured environment fails loudly: spending money under a
    claimed cap with missing prices or cap would be worse than failing a job.
    """
    source = os.environ if environ is None else environ
    ledger_path = str(source.get(LEDGER_ENV, "")).strip()
    if not ledger_path:
        return None
    cap = str(source.get(CAP_ENV, "")).strip()
    if not cap:
        raise LedgerConfigurationError(f"{CAP_ENV} is required when {LEDGER_ENV} is set")
    registry_path = str(source.get(REGISTRY_ENV, "")).strip()
    if not registry_path:
        raise LedgerConfigurationError(f"{REGISTRY_ENV} is required when {LEDGER_ENV} is set")
    registry = PriceRegistry.from_json(registry_path)
    return LedgerGuard(BudgetLedger(ledger_path, cap), registry)


def count_pdf_pages(raw_bytes: bytes) -> int | None:
    """Exact page count for bounded OCR pricing; None when not a readable PDF."""
    try:
        from io import BytesIO

        from pypdf import PdfReader

        count = len(PdfReader(BytesIO(raw_bytes)).pages)
    except Exception:
        return None
    return count if count > 0 else None
