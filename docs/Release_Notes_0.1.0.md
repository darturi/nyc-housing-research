# Release notes: 0.1.0 local-distribution candidate

Updated: 2026-09-20 (unreleased candidate)

This candidate changes the primary product from a maintainer-hosted PostgreSQL/S3
web service to a clone-and-run local application with optional user-supplied API
credentials.

## Changes since the initial candidate

- Native desktop confirmation for backup-protected workspace upgrades, automatic
  database restoration on ordinary migration failure, and a separate recovery-copy
  offer if automatic recovery fails. The launch lock remains held during shutdown
  until startup/migration work finishes.
- The 55-question retrieval regression now uses a frozen synthetic miniature
  corpus with real parsing, FTS, citations, ranking, and filters. Retrieval reports
  identify the selected generation and explicitly describe exact/keyword scope.
- Answer evaluation checks citations actually used in answer prose, records full
  evidence and corpus/profile/prompt provenance, and stops when the corpus changes.
  `--report PATH` explicitly saves a private report without overwriting a file.
- `review-answers` creates pending human-review templates and checks completed
  reviews against the exact report hash. Accuracy, qualifications/missing facts,
  citation support, and unsupported claims are reviewed separately. Synthetic,
  incomplete, or technically failing runs cannot receive answer acceptance.
- Corpus schema 3 shares full-text rows across generations; state schema 2 adds
  saved research. HPD connector v3 includes undated rows in unfiltered queries.
- Personal resources, saved matters, source comparisons, partial rent-regulation
  packs, property dossiers, and review-labeled English/Spanish/help catalogs are
  available. Scanned-PDF OCR, additional dossier datasets, and managed offline
  extensions remain unavailable.
- Code/documentation licensing is Apache-2.0. A GitHub remote is configured;
  final-artifact CI, dependency notices, and source redistribution remain separate
  checks. See [current readiness](Release_Readiness/2026-09-20_Beta_Batch.md).

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

No bulk HPD snapshot/citywide analytics, reviewed managed local model, production
OCR, background OS scheduler, direct legacy-S3 exporter, automatic updater, or
maintainer-hosted service is included. Research persists only when explicitly
saved/exported. The Dockerfile targets the optional legacy hosted app; it is not
a Docker distribution of the local desktop product.

## Compatibility

- Application: Python `>=3.12,<3.13`.
- Local workspace format: 1; corpus schema: 3; state schema: 2.
- Canonical corpus bundle: 1; workspace backup: 1; HPD connector:
  `hpd-soda21-v3`.
- Downgrade across an unsupported schema is refused. Backup into a new destination
  before changing release lines.
- Legacy vectors without full profile/preprocessing provenance are excluded and
  must be deliberately re-embedded.

## Release blockers

Final-artifact platform CI, bounded real-provider answer evaluation, legal-domain
review, controlled PostgreSQL retrieval parity, an independent clean-machine
journey, and a signed/notarized public desktop build still require release
evidence. The code license and repository remote are already present. Dependency
license/notices review remains separate from the code license. Source redistribution also remains
unapproved, so official source downloads—not a bundled corpus—are the default.
See the L1 acceptance report and the requirement-by-requirement audit.
