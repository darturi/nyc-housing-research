# Contributing

## Development setup

Ordinary users start with `sh start.sh` / `start.ps1`; see [Setup](Setup.md).
Those scripts include keyring support and retain extra packages in an existing
environment. Contributor commands below intentionally install the developer tools.

```bash
uv sync --locked --extra dev
uv run ruff check .
uv run pytest -q
uv build --no-sources
uv run python -m app.maintenance.release_audit dist/*.whl dist/*.tar.gz
```

The ordinary suite skips the real-browser journey. After installing a Chromium
browser for Playwright, run it explicitly:

```bash
uv run playwright install chromium
NYC_HOUSING_BROWSER_E2E=1 uv run pytest tests/e2e/test_local_browser.py -q
```

CI runs this fixture-backed journey in a dedicated Chromium job. It exercises
the local launch exchange, first-run source install, profile/budget and write-only
credential setup, citation evidence, failed-job resume, property
disambiguation/pagination/refresh, and conservative automatic routing; it makes
no live source or paid-provider request.

Use Python 3.12. Tests hard-override the legacy database, provider, artifact, and
local-workspace environment before application imports. Never weaken that guard
to make a test use a developer database or real key.

The native local path is `app.cli.main` → explicit `WorkspaceContext` → local
services. The original `app.main`/PostgreSQL code is a legacy compatibility path.
Do not import `app.db.session` or cloud storage from local startup. Network work
must pass through `NetworkPolicy`; paid work must pass through `ProviderGateway`
and the append-only `UsageLedger`.

## Required checks by change type

- Storage: real temporary SQLite files, FK/WAL/version and failure-injection tests.
- Retrieval: exact citation, punctuation, pre-limit filters, generation/profile
  isolation, 50-case gate, and 10k benchmark where relevant.
- HTTP/UI: Host/origin/session/CSRF checks, safe external-text rendering, no CORS,
  keyboard/narrow-screen behavior, and no secrets in responses.
- Provider: fake/mock transport only in ordinary CI; reservation/settlement/
  uncertainty and offline-before-send assertions.
- HPD: frozen fixture transport, positive/zero/ambiguous/pagination/rate-limit and
  stale-cache cases. Live checks must be separately invoked and bounded.
- Packaging: locked build plus wheel and source-distribution execution outside
  the checkout, including paths with spaces and Unicode and schema preflight.

No default test receives real `DATABASE_URL`, cloud credentials, or provider keys.
Do not commit workspace SQLite files, `.env`, downloaded source artifacts, cache,
exports, private migration bundles, or diagnostic material containing addresses.

## Release discipline

Public repository creation, remote changes, commits, pushes, release publication,
corpus redistribution, hosted-system deletion, and credential rotation require
explicit maintainer actions. A code license is still required before an open-source
release. Keep unsupported-platform, legal-review, and live-contract gaps visible
in the acceptance report rather than converting them to inspection-only passes.
