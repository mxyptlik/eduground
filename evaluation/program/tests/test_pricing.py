"""Known-answer and failure tests for offline external API costing."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from evaluation.program.pricing import (
    CallUsage,
    PriceRegistry,
    cost_call,
    sum_call_costs,
)


WHEN = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)


def registry(*, rates=None, effective="2026-09-01T00:00:00Z"):
    if rates is None:
        rates = [
            {"unit": "input_tokens", "amount": "1", "quantity": 1_000_000},
            {"unit": "cached_input_tokens", "amount": "0.25", "quantity": 1_000_000},
            {"unit": "output_tokens", "amount": "2", "quantity": 1_000_000},
            {"unit": "cached_output_tokens", "amount": "1", "quantity": 1_000_000},
        ]
    return PriceRegistry.from_dict(
        {
            "version": "1.0",
            "schedules": [
                {
                    "provider": "fixture-provider",
                    "model": "fixture-model",
                    "operation": "generation",
                    "effective_from": effective,
                    "currency": "USD",
                    "source": "fixture://pricing-test",
                    "prices": rates,
                }
            ],
        }
    )


def usage(**changes):
    fields = dict(
        call_id="attempt-1",
        provider="fixture-provider",
        model="fixture-model",
        operation="generation",
        occurred_at=WHEN,
        input_tokens=1_000_000,
        cached_input_tokens=200_000,
        output_tokens=100_000,
        cached_output_tokens=10_000,
        reasoning_output_tokens=50_000,
    )
    fields.update(changes)
    return CallUsage(**fields)


class PricingTests(unittest.TestCase):
    def test_known_cost_excludes_cached_and_reasoning_subsets_from_regular_rates(self):
        cost = cost_call(usage(), registry())
        self.assertEqual(cost.status, "priced")
        self.assertEqual(cost.amount, Decimal("1.04"))
        self.assertEqual(cost.currency, "USD")
        self.assertEqual(cost.price_source, "fixture://pricing-test")

    def test_missing_usage_is_unknown_even_when_known_subtotal_is_zero(self):
        cost = cost_call(usage(input_tokens=None), registry())
        self.assertEqual(cost.status, "unknown")
        self.assertIsNone(cost.amount)
        self.assertIn("usage_missing:input_tokens", cost.unknown_reasons)

    def test_missing_cached_tariff_is_unknown_when_cached_tokens_are_used(self):
        rate = [{"unit": "input_tokens", "amount": "1", "quantity": 1_000_000}]
        cost = cost_call(usage(), registry(rates=rate))
        self.assertIsNone(cost.amount)
        self.assertIn("price_or_usage_missing:cached_input_tokens", cost.unknown_reasons)

    def test_explicit_zero_cached_usage_allows_regular_rate(self):
        rate = [{"unit": "input_tokens", "amount": "1", "quantity": 1_000_000}]
        cost = cost_call(usage(cached_input_tokens=0), registry(rates=rate))
        self.assertEqual(cost.amount, Decimal("1"))

    def test_zero_explicit_usage_and_zero_price_are_real_zero(self):
        rate = [{"unit": "requests", "amount": "0", "quantity": 1}]
        cost = cost_call(usage(requests=1), registry(rates=rate))
        self.assertEqual(cost.amount, Decimal("0"))

    def test_missing_or_not_yet_effective_price_is_unknown(self):
        cost = cost_call(usage(), registry(effective="2026-10-01T00:00:00Z"))
        self.assertIsNone(cost.amount)
        self.assertEqual(cost.unknown_reasons, ("price_schedule_missing",))

    def test_newest_effective_schedule_selected_and_registry_roundtrips(self):
        older = registry(effective="2026-01-01T00:00:00Z").to_dict()["schedules"][0]
        newer = registry().to_dict()["schedules"][0]
        older["prices"][0]["amount"] = "100"
        data = {"version": "1.0", "schedules": [older, newer]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            loaded = PriceRegistry.from_json(path)
        self.assertEqual(cost_call(usage(), loaded).amount, Decimal("1.04"))
        self.assertEqual(loaded.to_dict(), data)

    def test_retry_attempts_are_separate_and_unknown_poison_total(self):
        prices = registry()
        first = cost_call(usage(), prices)
        retry = cost_call(usage(call_id="attempt-2"), prices)
        total = sum_call_costs([first, retry])
        self.assertEqual(total.call_count, 2)
        self.assertEqual(total.amount, Decimal("2.08"))
        unknown = cost_call(usage(call_id="attempt-3", output_tokens=None), prices)
        partial = sum_call_costs([first, unknown])
        self.assertIsNone(partial.amount)
        self.assertEqual(partial.unknown_call_ids, ("attempt-3",))
        with self.assertRaisesRegex(ValueError, "duplicate call_id"):
            sum_call_costs([first, first])

    def test_request_and_page_billing_need_only_their_own_usage(self):
        rate = [
            {"unit": "requests", "amount": "0.05", "quantity": 1},
            {"unit": "pages", "amount": "0.10", "quantity": 1},
        ]
        call = usage(input_tokens=None, output_tokens=None, requests=1, pages=3)
        self.assertEqual(cost_call(call, registry(rates=rate)).amount, Decimal("0.35"))

    def test_mixed_currency_total_rejected(self):
        a = cost_call(usage(), registry())
        data = registry().to_dict()
        data["schedules"][0]["currency"] = "EUR"
        b = cost_call(usage(call_id="attempt-2"), PriceRegistry.from_dict(data))
        with self.assertRaisesRegex(ValueError, "different currencies"):
            sum_call_costs([a, b])

    def test_malformed_records_rejected(self):
        with self.assertRaisesRegex(ValueError, "cached_input_tokens cannot exceed input_tokens"):
            usage(input_tokens=2, cached_input_tokens=3)
        with self.assertRaisesRegex(ValueError, "reasoning_output_tokens cannot exceed output_tokens"):
            usage(output_tokens=2, reasoning_output_tokens=3, cached_output_tokens=0)
        with self.assertRaisesRegex(ValueError, "input_tokens must be"):
            usage(input_tokens=-1)
        with self.assertRaisesRegex(ValueError, "input_tokens must be"):
            usage(input_tokens=True)
        with self.assertRaisesRegex(ValueError, "timezone"):
            CallUsage.from_dict({
                "call_id": "x", "provider": "x", "model": "x", "operation": "x",
                "occurred_at": "2026-09-30T12:00:00",
            })
        with self.assertRaisesRegex(ValueError, "duplicate unit price"):
            registry(rates=[
                {"unit": "requests", "amount": "1", "quantity": 1},
                {"unit": "requests", "amount": "2", "quantity": 1},
            ])
        with self.assertRaisesRegex(ValueError, "finite non-negative"):
            registry(rates=[{"unit": "requests", "amount": "NaN", "quantity": 1}])
        with self.assertRaisesRegex(ValueError, "unsupported price registry version"):
            PriceRegistry.from_dict({"version": "2.0", "schedules": []})
        with self.assertRaisesRegex(ValueError, "duplicate price schedule"):
            schedule = registry().to_dict()["schedules"][0]
            PriceRegistry.from_dict({"version": "1.0", "schedules": [schedule, schedule]})
        with self.assertRaisesRegex(ValueError, "must be an object"):
            PriceRegistry.from_dict({"version": "1.0", "schedules": ["invalid"]})


if __name__ == "__main__":
    unittest.main()
