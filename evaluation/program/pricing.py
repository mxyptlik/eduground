"""Offline, versioned prices and external-provider call costing.

No prices are bundled here: a run must load a dated, sourced price registry.
Token totals include their cached/reasoning subsets. The calculator charges
uncached and cached tokens separately, and never charges reasoning tokens again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping


PRICE_SCHEMA_VERSION = "1.0"
USAGE_UNITS = frozenset(
    {
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "cached_output_tokens",
        "requests",
        "pages",
        "input_characters",
        "output_characters",
    }
)


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite non-negative decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field} must be a finite non-negative decimal")
    return result


def _count(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer or null")
    return value


def _timestamp(value: str | datetime, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO 8601 timestamp with a timezone") from exc
    else:
        raise ValueError(f"{field} must be an ISO 8601 timestamp with a timezone")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must have a timezone")
    return parsed.astimezone(timezone.utc)


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _fields(data: Mapping[str, Any], required: set[str], optional: set[str], label: str) -> None:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be an object")
    missing = required - data.keys()
    extra = data.keys() - required - optional
    if missing or extra:
        raise ValueError(f"{label} fields invalid: missing={sorted(missing)}, extra={sorted(extra)}")


@dataclass(frozen=True)
class UnitPrice:
    """Price for ``quantity`` usage units; e.g. USD 0.25 per 1,000,000 tokens."""

    unit: str
    amount: Decimal
    quantity: int

    def __post_init__(self) -> None:
        if not isinstance(self.unit, str) or self.unit not in USAGE_UNITS:
            raise ValueError(f"unsupported price unit: {self.unit}")
        object.__setattr__(self, "amount", _decimal(self.amount, "amount"))
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UnitPrice:
        _fields(data, {"unit", "amount", "quantity"}, set(), "unit price")
        return cls(unit=data["unit"], amount=data["amount"], quantity=data["quantity"])

    def to_dict(self) -> dict[str, Any]:
        return {"unit": self.unit, "amount": str(self.amount), "quantity": self.quantity}


@dataclass(frozen=True)
class PriceSchedule:
    provider: str
    model: str
    operation: str
    effective_from: datetime
    currency: str
    source: str
    prices: tuple[UnitPrice, ...]

    def __post_init__(self) -> None:
        for field in ("provider", "model", "operation", "source"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        object.__setattr__(self, "effective_from", _timestamp(self.effective_from, "effective_from"))
        if not isinstance(self.currency, str) or len(self.currency) != 3 or not self.currency.isalpha() or not self.currency.isupper():
            raise ValueError("currency must be a three-letter uppercase ISO currency code")
        if not self.prices:
            raise ValueError("price schedule must have at least one unit price")
        units = [price.unit for price in self.prices]
        if len(set(units)) != len(units):
            raise ValueError("duplicate unit price")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PriceSchedule:
        _fields(
            data,
            {"provider", "model", "operation", "effective_from", "currency", "source", "prices"},
            set(),
            "price schedule",
        )
        prices = data["prices"]
        if not isinstance(prices, list):
            raise ValueError("prices must be a list")
        return cls(
            provider=data["provider"],
            model=data["model"],
            operation=data["operation"],
            effective_from=_timestamp(data["effective_from"], "effective_from"),
            currency=data["currency"],
            source=data["source"],
            prices=tuple(UnitPrice.from_dict(price) for price in prices),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "operation": self.operation,
            "effective_from": self.effective_from.isoformat(),
            "currency": self.currency,
            "source": self.source,
            "prices": [price.to_dict() for price in self.prices],
        }


@dataclass(frozen=True)
class PriceRegistry:
    version: str
    schedules: tuple[PriceSchedule, ...]

    def __post_init__(self) -> None:
        if self.version != PRICE_SCHEMA_VERSION:
            raise ValueError(f"unsupported price registry version: {self.version}")
        if any(not isinstance(entry, PriceSchedule) for entry in self.schedules):
            raise ValueError("schedules must contain price schedules")
        keys = [
            (entry.provider, entry.model, entry.operation, entry.effective_from)
            for entry in self.schedules
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate price schedule for provider/model/operation/effective_from")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PriceRegistry:
        _fields(data, {"version", "schedules"}, set(), "price registry")
        if not isinstance(data["schedules"], list):
            raise ValueError("schedules must be a list")
        return cls(
            version=data["version"],
            schedules=tuple(PriceSchedule.from_dict(entry) for entry in data["schedules"]),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> PriceRegistry:
        with Path(path).open("r", encoding="utf-8") as stream:
            data = json.load(stream, parse_float=Decimal)
        if not isinstance(data, dict):
            raise ValueError("price registry must be a JSON object")
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "schedules": [entry.to_dict() for entry in self.schedules]}

    def schedule_for(self, provider: str, model: str, operation: str, when: datetime) -> PriceSchedule | None:
        when = _timestamp(when, "when")
        matches = [
            entry
            for entry in self.schedules
            if entry.provider == provider
            and entry.model == model
            and entry.operation == operation
            and entry.effective_from <= when
        ]
        return max(matches, key=lambda entry: entry.effective_from, default=None)


@dataclass(frozen=True)
class CallUsage:
    """One external API attempt. Null means unknown, including for cached usage."""

    call_id: str
    provider: str
    model: str
    operation: str
    occurred_at: datetime
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    cached_output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    requests: int | None = None
    pages: int | None = None
    input_characters: int | None = None
    output_characters: int | None = None

    def __post_init__(self) -> None:
        for field in ("call_id", "provider", "model", "operation"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        object.__setattr__(self, "occurred_at", _timestamp(self.occurred_at, "occurred_at"))
        for field in (
            "input_tokens", "cached_input_tokens", "output_tokens", "cached_output_tokens",
            "reasoning_output_tokens", "requests", "pages", "input_characters", "output_characters",
        ):
            object.__setattr__(self, field, _count(getattr(self, field), field))
        for subset, total in (
            ("cached_input_tokens", "input_tokens"),
            ("cached_output_tokens", "output_tokens"),
            ("reasoning_output_tokens", "output_tokens"),
        ):
            subset_value = getattr(self, subset)
            total_value = getattr(self, total)
            if subset_value is not None and total_value is not None and subset_value > total_value:
                raise ValueError(f"{subset} cannot exceed {total}")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CallUsage:
        required = {"call_id", "provider", "model", "operation", "occurred_at"}
        optional = set(cls.__dataclass_fields__) - required
        _fields(data, required, optional, "call usage")
        values = dict(data)
        values["occurred_at"] = _timestamp(values["occurred_at"], "occurred_at")
        return cls(**values)


@dataclass(frozen=True)
class CallCost:
    call_id: str
    amount: Decimal | None
    known_subtotal: Decimal
    currency: str | None
    price_effective_from: datetime | None
    price_source: str | None
    unknown_reasons: tuple[str, ...]

    @property
    def status(self) -> str:
        return "unknown" if self.amount is None else "priced"


def _usage_units(usage: CallUsage, unit: str) -> int | None:
    if unit == "input_tokens":
        if usage.input_tokens is None or usage.cached_input_tokens is None:
            return None
        return usage.input_tokens - usage.cached_input_tokens
    if unit == "output_tokens":
        if usage.output_tokens is None or usage.cached_output_tokens is None:
            return None
        return usage.output_tokens - usage.cached_output_tokens
    return getattr(usage, unit)


def cost_call(usage: CallUsage, registry: PriceRegistry) -> CallCost:
    """Cost one attempt; missing usage or price produces an unknown total."""

    schedule = registry.schedule_for(usage.provider, usage.model, usage.operation, usage.occurred_at)
    if schedule is None:
        return CallCost(usage.call_id, None, Decimal(0), None, None, None, ("price_schedule_missing",))

    rates = {entry.unit: entry for entry in schedule.prices}
    unknown: list[str] = []
    subtotal = Decimal(0)
    for unit, rate in rates.items():
        count = _usage_units(usage, unit)
        if count is None:
            unknown.append(f"usage_missing:{unit}")
        else:
            subtotal += Decimal(count) * rate.amount / Decimal(rate.quantity)

    # Cached tokens are a subset of the published totals. If a provider charged
    # token usage but its cached tariff is absent, never assume the standard rate.
    for ordinary, cached in (
        ("input_tokens", "cached_input_tokens"),
        ("output_tokens", "cached_output_tokens"),
    ):
        if ordinary in rates and cached not in rates and getattr(usage, cached) not in (0,):
            unknown.append(f"price_or_usage_missing:{cached}")
        if cached in rates and ordinary not in rates and _usage_units(usage, ordinary) not in (0,):
            unknown.append(f"price_or_usage_missing:{ordinary}")

    return CallCost(
        call_id=usage.call_id,
        amount=None if unknown else subtotal,
        known_subtotal=subtotal,
        currency=schedule.currency,
        price_effective_from=schedule.effective_from,
        price_source=schedule.source,
        unknown_reasons=tuple(unknown),
    )


@dataclass(frozen=True)
class CostTotal:
    amount: Decimal | None
    known_subtotal: Decimal
    currency: str | None
    call_count: int
    unknown_call_ids: tuple[str, ...]


def sum_call_costs(costs: list[CallCost]) -> CostTotal:
    """Sum attempts, refusing duplicate IDs and mixed known currencies."""

    ids = [cost.call_id for cost in costs]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate call_id in cost total")
    currencies = {cost.currency for cost in costs if cost.currency is not None}
    if len(currencies) > 1:
        raise ValueError("cannot sum different currencies")
    unknown = tuple(cost.call_id for cost in costs if cost.amount is None)
    subtotal = sum((cost.known_subtotal for cost in costs), Decimal(0))
    return CostTotal(
        amount=None if unknown else subtotal,
        known_subtotal=subtotal,
        currency=next(iter(currencies), None),
        call_count=len(costs),
        unknown_call_ids=unknown,
    )
