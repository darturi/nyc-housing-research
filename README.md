# NYC Housing Research

NYC Housing Research is a self-host-free, local browser application for cited
New York City housing-law research and bounded NYC HPD violation lookups. The
application, SQLite databases, legal-source artifacts, cache, and usage ledger
run on your machine. You may use keyless source search or supply your own model
API key for embeddings and generated answers.

This is general legal-information software, not legal advice. Verify sources and
current law before relying on a result.

## What works

- Downloads and locally indexes five official legal/guidance modules.
- Searches citations and full text without an API key.
- Optionally builds a local semantic index and generates evidence-cited answers
  with a user-supplied OpenAI key.
- Looks up filtered property violations from the official NYC Open Data HPD
  dataset without downloading its roughly 11-million-row table.
- Caches property pages for offline reuse, creates scoped exports, and supports
  secret-free workspace backup/restore.
- Enforces loopback-only access, one-use launch credentials, session/CSRF checks,
  exact local spend reservations, a user-lowerable one-or-two-call concurrency
  limit, and an offline egress policy.

Citywide HPD analytics, a complete offline HPD snapshot, fully local models,
saved research history, and Docker are independent optional extensions—not L1
requirements.

## Quick start from a clone

Prerequisites: Git and [uv](https://docs.astral.sh/uv/), plus Python 3.12 (uv can
install it). From the cloned repository:

```bash
uv sync --locked
uv run nyc-housing setup
uv run nyc-housing serve
```

Setup creates an OS-appropriate local workspace and, in an interactive terminal,
offers both the five-source text-only corpus and optional user-supplied OpenAI
configuration. It explains the data/cost boundary before prompting; storing the
credential and selecting the packaged profiles makes no network request or charge.
If an OS keyring is unavailable, setup offers an explicit owner-only file or
environment-managed route. Use `--skip-core` to initialize without the corpus
offer, or `--install-core` in scripts and other non-interactive environments.
`serve` binds only to `127.0.0.1` and opens a browser; use `--no-browser` to print
a one-time launch code instead.

The browser opens the Sources view when the corpus is absent and walks through
source installation, provider/budget settings, optional credential setup, and
semantic-index estimation. In scripts, `--configure-openai` requests the hidden
credential prompt and selects the packaged OpenAI profiles unless explicit profile
arguments say otherwise; it does not validate the key or incur a charge.

Keyless search is available after the corpus installs:

```bash
uv run nyc-housing search "RPAPL section 711"
```

To enable real model-backed answers and semantic search:

```bash
uv run nyc-housing profiles select answer openai-answer-luna-v1
uv run nyc-housing profiles select embedding openai-embedding-3-small-v1
uv run nyc-housing credentials set openai
uv run nyc-housing credentials validate openai --approve-cost --max-cost-usd 0.000001
uv run nyc-housing corpus index --estimate-only
uv run nyc-housing corpus index --approve-cost --max-cost-usd 1.00
```

The credential command prompts without echoing. If no OS keyring is available,
install the `credentials` extra or deliberately choose the owner-readable local
fallback with `--storage file`. Never pass a key as a command-line argument.
Credential validation and corpus indexing can incur provider charges and both
require a displayed estimate, explicit approval, and a hard ceiling. The browser
offers the same estimate/approval workflow.

Advanced installations can point either selected OpenAI profile at an
OpenAI-compatible endpoint and give answer/embedding traffic separate credential
slots. Custom endpoints require retention metadata and either complete price
provenance or an explicit unknown-price declaration. They remain disabled until
`profiles check KIND` succeeds without fallback. Unknown-priced endpoints are
never eligible for automatic indexing/evaluation and require a separate one-off
approval for each manual answer or summary; those requests are labeled as outside
USD caps. See [Setup](docs/Setup.md#advanced-openai-compatible-endpoints).

## Operational commands

```bash
uv run nyc-housing status --json
uv run nyc-housing sources --json
uv run nyc-housing doctor --json
uv run nyc-housing diagnostics export ./nyc-housing-diagnostics.json --json
uv run nyc-housing migrate preflight --json
uv run nyc-housing usage --json
uv run nyc-housing evaluate --answers --estimate-only --json
uv run nyc-housing debug answer --question "What does RPAPL 711 cover?" --json
uv run nyc-housing maintenance prune --json
uv run nyc-housing backup ./workspace-backup.zip
uv run nyc-housing restore ./workspace-backup.zip --destination ./restored-workspace
```

Use `--data-dir PATH` before the command to select a workspace, and `--offline`
to block application-managed remote HTTP before requests are made.
Retention cleanup is preview-only unless `maintenance prune --apply` is supplied.
It preserves active/nonterminal jobs, unresolved spend, current-month accounting,
and pinned property-cache entries.
The diagnostics export is local, redacted by default, owner-readable, and makes
no network or paid-provider request.

The answer-evaluation estimate is free. A real answer-suite run requires
`--answers --approve-cost --max-cost-usd N`; its automated checks are only a
technical screen and the report remains `domain_review_required`.

## Documentation

- [Setup and first run](docs/Setup.md)
- [Coverage and limitations](docs/Coverage.md)
- [Privacy and data flow](docs/Privacy_and_Data_Flow.md)
- [Costs and budgets](docs/Costs.md)
- [Updating](docs/Updating.md)
- [Troubleshooting and recovery](docs/Troubleshooting.md)
- [Legacy migration](docs/Migration.md)
- [Contributing](docs/Contributing.md)
- [Third-party and source review inventory](docs/Third_Party_and_Source_Review.md)
- [L1 acceptance evidence](docs/Release_Readiness/2026-09-14_L1_Acceptance.md)
- [L1 requirement-by-requirement audit](docs/Release_Readiness/2026-09-15_L1_Requirement_Audit.md)

The original hosted PostgreSQL/S3 application remains in the tree as a legacy
entry path. Its runbooks are marked historical and are not the local quickstart.

## Release status and licensing

The local implementation is a release candidate. macOS arm64 and a built wheel
have been exercised locally; the repository includes native Linux, macOS, and
Windows CI, but those remote jobs cannot be claimed until a GitHub remote exists
and runs them. The five-source installer and bounded HPD connector have been
live-verified; paid-answer, domain-review, parity, and independent clean-machine
gates are recorded transparently in the acceptance report.

No code license has yet been selected. Public visibility alone does not grant
reuse rights; the maintainer must add an appropriate `LICENSE` before presenting
this as an open-source release.
