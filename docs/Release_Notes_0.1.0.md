# Release notes: 0.1.0 local-distribution candidate

Date: 2026-09-14

This candidate changes the primary product from a maintainer-hosted PostgreSQL/S3
web service to a clone-and-run local application with optional user-supplied API
credentials.

## Included

- Python 3.12 package, locked uv environment, `nyc-housing` launcher, and packaged
  browser assets with stable failure exit categories for automation.
- Interactive setup offers the free five-source text-only installation and an
  optional no-charge user-credential/profile configuration, while `--install-core`,
  `--skip-core`, and `--configure-openai` keep scripted setup deterministic.
- Explicit per-workspace paths/settings and separate versioned corpus/state SQLite
  stores with FTS5 and atomic active generations.
- Five-source official legal collection, content-addressed artifacts, staged
  install/update/verify/activate/rollback, canonical bundle import/export, and a
  read-only legacy exporter.
- Exact citation + weighted FTS5 + cached NumPy vector retrieval, with labeled
  keyword fallback and a packaged 55-case evaluation command.
- Optional OpenAI profiles, OS-keyring/file/environment credentials, preflight
  cost estimate/approval, exact append-only spend ledger, two-call shared limit,
  a user-selectable lower one-call limit, and no automatic replay of uncertain
  paid requests.
- Advanced OpenAI-compatible answer/embedding endpoints with independent
  credential slots, explicit price/retention provenance, derived profile
  identities, and a mandatory no-fallback compatibility check. Unknown-price
  overrides are batch-disabled and require labeled one-off approval outside USD
  caps.
- Optional hidden-input credential setup plus a minimally metered, explicitly
  approved credential capability check in the CLI and browser.
- A bounded 26-case answer-generation evaluation runner with a free conservative
  estimate, one traceable operation ID, cost deltas, reviewer inputs, and an
  unavoidable domain-review-required disposition.
- Evidence-first, nonpersistent answer jobs and Markdown/JSON exports with prompt,
  retrieval, publisher, source-check, and effective-date provenance; historical
  questions fail conservatively when the installed evidence cannot support the
  requested period, and incomplete provider responses are never presented as
  finished answers.
- Generic scoped `/api/v1/exports` compatibility contract for completed
  in-memory answers and fixed cached property pages.
- Browser Auto/Search/Answer/Property modes, conservative no-cost routing, source
  filters, expandable evidence, answer retry/cancellation, safe official-source
  links, and durable corpus install/update/status/cancel/resume/verify/rollback
  controls.
- Browser first-run guidance, semantic-index estimate/approval, deliberate
  per-source partial activation, workspace diagnostics, and settings for separate
  budgets, profiles, offline mode, cache, and retention.
- Typed live HPD property queries, identity candidates, status/class/ZIP/date
  filters, deterministic pagination, nullable totals, refresh controls,
  acquisition-interval/source-time provenance, freshness/stale cache, combined
  legal/property summaries, page CSV and tracked/cancellable bounded complete
  export.
- Loopback Host/origin/session/CSRF boundary, one-use launcher tokens, no CORS,
  restrictive response headers, and shared offline egress policy.
- Secret-free consistent backup/restore, redacted status/doctor and owner-only
  diagnostic export, manual metadata-only update checking, plus owner-readable
  metadata-only diagnostic logs.
- Independently configurable operational, property-cache, and completed-usage
  retention with preview-first cleanup and accounting-safe protected records.
- Read-only schema-migration preflight, native three-OS CI definition, a dedicated
  real-Chromium browser journey, clean-wheel smoke test, automated distribution
  content audit, cross-platform wheel/sdist installation smokes, and 10k-vector
  benchmark.
- Live-verified five-source acquisition (741 chunks, about 70 MiB text-only),
  semantic unchanged-source reuse, ETag/Last-Modified conditional refresh where
  the source shape supports it, and bounded live HPD contract/query/export.

## Deliberate exclusions

No bulk HPD snapshot/citywide analytics, Docker image, saved transcript history,
fully local model, background OS scheduler, direct legacy-S3 exporter, automatic
updater, or maintainer-hosted service is included.

## Compatibility

- Application: Python `>=3.12,<3.13`.
- Local workspace format: 1; corpus schema: 1; state schema: 1.
- Canonical corpus bundle: 1; workspace backup: 1; HPD connector:
  `hpd-soda21-v2`.
- Downgrade across an unsupported schema is refused. Backup into a new destination
  before changing release lines.
- Legacy vectors without full profile/preprocessing provenance are excluded and
  must be deliberately re-embedded.

## Release blockers

This is not yet publishable as a completed open-source L1 release: a code license,
verified GitHub remote/CI runs, bounded paid answer evaluation, legal-domain
review, controlled PostgreSQL parity comparison, and an independent clean-machine
journey remain external release evidence. Source redistribution also remains
unapproved, so official source downloads—not a bundled corpus—are the default.
See the L1 acceptance report and the requirement-by-requirement audit.
