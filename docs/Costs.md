# Provider costs and local budgets

Keyless exact-citation/keyword search, corpus verification, cached-property reads,
exports, backup, restore, and diagnostics are local and make no paid model call.
Official-source and NYC Open Data access may still be subject to their own service
limits, but the application does not charge for them.

## Current versioned OpenAI profiles

Prices were verified on 2026-09-14 and are snapshots, not a promise that provider
pricing will remain unchanged.

| Profile | Model | Input / 1M tokens | Output / 1M tokens |
| --- | --- | ---: | ---: |
| `openai-embedding-3-small-v1` | `text-embedding-3-small` | $0.02 | n/a |
| `openai-answer-luna-v1` | `gpt-5.6-luna` | $0.20 | $1.20 |

Run `nyc-housing profiles list --json` to inspect the packaged price source and
effective date.

Custom OpenAI-compatible endpoints do not inherit assumed prices. Configuration
requires response-retention metadata plus either complete input/output prices,
an HTTPS price source, and an effective date, or `--pricing-unknown`. Known values
are user assertions recorded in the derived profile and every usage snapshot;
verify them against the endpoint operator. An endpoint change creates a new
effective profile identity; any override change clears compatibility approval.

Unknown pricing is deliberately narrow. It blocks semantic indexing, answer
evaluation, and every other automatic/batch paid operation. A one-off
compatibility check, answer, debug answer, or browser property summary proceeds
only after `--allow-unknown-cost` or the equivalent browser confirmation. Its
result says `cost_known: false`, `cost_usd: null`, and “outside USD budget caps.”
The ledger counts these attempts and still enforces paid-call concurrency, but a
zero monetary sentinel is not represented as a zero-dollar charge. Check the
provider's own billing system for the actual amount.

The packaged `fake-*` profiles are deterministic synthetic test adapters. They
make no network call or charge, and their output is labeled `synthetic_demo`;
they are not substantive model-backed answers or a supported legal-quality
profile.

## Guardrails

- Default installation monthly cap: USD 15.
- Default per-operation cap: USD 2.
- At most two paid provider calls may be admitted across processes; Settings or
  `setup --max-paid-concurrency 1` can lower this to one.
- Remote answers use a 60-second total deadline by default; Settings or
  `setup --answer-deadline-seconds N` can select 5–300 seconds.
- Every request reserves projected spend before sending, then appends a settlement
  from provider usage. A lost response remains conservatively “uncertain.”
- Corpus embedding is never automatic. `--estimate-only`, `--approve-cost`, and
  a required `--max-cost-usd` make paid bulk spend deliberate.
- Reused chunk/profile embeddings are not purchased again.
- Unknown-cost one-off calls are not covered by the monthly or per-operation USD
  limits; `usage --json` reports their counts separately.

Change budgets in Settings or during setup; values are exact decimals. The local
ledger accounts only for this installation. Other software using the same API key
is outside its cap, so the provider dashboard remains authoritative.

Completed attempts default to 12 calendar months of retention, independently of
the 30-day job/log policy. Retention cleanup never removes unresolved reservations
or uncertain attempts and always preserves the current budget month. After older
completed attempts are pruned, `usage --json` reports `history_pruned_before`.

```bash
uv run nyc-housing usage --json
uv run nyc-housing maintenance prune --json
uv run nyc-housing setup --monthly-budget 10.00
uv run nyc-housing corpus index --estimate-only
uv run nyc-housing evaluate --answers --estimate-only --json
```

The browser uses the same ledger and approval rules for credential validation,
semantic indexing, answers, and property summaries. A credential validation is a
minimal embedding request and is still potentially billable; its estimate and
hard ceiling are shown before confirmation.

`profiles check answer|embedding` is the corresponding explicit capability test
for a configured endpoint. Its estimate is free; execution calls only the
configured endpoint. Known-price checks require `--approve-cost` plus
`--max-cost-usd`. An unknown-price check instead requires
`--allow-unknown-cost` and cannot claim a reliable USD ceiling.

The 26-case answer-generation evaluation is intentionally separate from the free
55-case retrieval evaluation. Run its free conservative estimate first. A paid
run requires both `--approve-cost` and `--max-cost-usd`, shares one operation ID
across its calls, stops on a provider error, and reports settled and uncertain
cost. Automated citation/source checks do not replace housing-law review.

Restoring a backup into a new workspace restores the included ledger but cannot
know provider activity performed elsewhere. An old backup cannot overwrite an
existing workspace, which prevents it from silently resetting newer local usage.
