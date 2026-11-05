# External API price registry and per-call cost

`pricing.py` is an offline calculator. It never fetches or assumes provider prices. A run supplies its own versioned registry, sourced from provider pricing information captured for that run. The registry stores a price effective timestamp, currency, source, and one or more billing units for each exact provider/model/operation. Keep the registry with the immutable run artifacts.

The JSON shape is:

```json
{
  "version": "1.0",
  "schedules": [
    {
      "provider": "<exact provider>",
      "model": "<exact model ID>",
      "operation": "generation",
      "effective_from": "<ISO 8601 timestamp with timezone>",
      "currency": "<ISO currency code>",
      "source": "<provider pricing page or billing record reference>",
      "prices": [
        {
          "unit": "input_tokens",
          "amount": "<decimal price>",
          "quantity": 1000000
        }
      ]
    }
  ]
}
```

That example shows the shape, not a usable price table. Capture **all applicable billable units**, including output and cached token rates. Supported units are `input_tokens`, `cached_input_tokens`, `output_tokens`, `cached_output_tokens`, `requests`, `pages`, `input_characters`, and `output_characters`. A price of zero is valid when a provider explicitly charges zero for that unit. A missing price means unknown.

One `CallUsage` represents one external attempt, including a failed attempt if it incurred usage. Retries use distinct `call_id` values. `input_tokens` and `output_tokens` are totals **including** their cached subsets. `reasoning_output_tokens` is an informational subset of output tokens and is never added again. Adapters must record cache counts as zero only when they know zero was used; unavailable usage remains `None`.

`cost_call` returns a `CallCost`. Its `amount` is `None` if a needed usage value or price is missing; `known_subtotal` is diagnostic only and must not be reported as the complete cost. `sum_call_costs` refuses duplicate IDs and mixed known currencies. If any call has unknown cost, the total is unknown. The selected price source and effective date are retained in each per-call result. Provider invoice/billing data should be reconciled separately when available.

OpenRouter's [embedding response schema](https://openrouter.ai/docs/api/api-reference/embeddings/submit-an-embedding-request) exposes the returned model and prompt-token usage, but its documented example does not supply a billed dollar amount or cache-token breakdown. The controlled embedding wrapper bills all returned prompt tokens at the dated standard rate as a conservative **estimate**, labelled separately from provider-reported cost. It refuses missing model or token usage. Reconcile these estimates against account billing before reporting actual expense.

## Worker ingestion operations

When the dedicated evaluation worker runs with `EDUGROUND_EVAL_BUDGET_LEDGER` set, its paid ingestion calls use the same registry and ledger:

- `openrouter` / `<pinned embedding model>` / `ingestion_embedding` — priced from input tokens; the response must identify the model and report usage. A silent Gemini fallback is refused under the ledger because it would change embedding identity.
- `unstructured` / `hosted-api` / `ocr` — priced by `pages` (the exact page count of the file is reserved and settled; a count that cannot be determined refuses dispatch) or by `requests` (one request per file). A schedule with neither unit refuses dispatch.
