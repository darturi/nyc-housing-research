# Setup and first run

## Supported release-candidate environment

- Python 3.12 exactly; `.python-version`, package metadata, and CI agree.
- `uv` 0.11.14 is used by the checked-in CI workflow.
- macOS arm64 is locally verified. Linux and Windows jobs are configured but are
  not advertised as verified until the GitHub workflow has run successfully.
- A modern browser with cookies and JavaScript enabled.
- About 2 GB free space is the diagnostic minimum and leaves ample staging room.
  A 2026-09-14 live text-only core install occupied about 70.3 MiB; the optional
  10,000 × 1,536-vector synthetic benchmark used a 92.7 MB SQLite corpus.

No PostgreSQL server, object store, app server, or maintainer account is needed.

## Install from a clone

```bash
uv sync --locked --extra credentials
uv run nyc-housing setup
```

`setup` is idempotent. It creates two local SQLite databases, an artifact tree,
exports/backups directories, and nonsecret JSON settings. In an interactive
terminal it explains and offers the resumable five-source, text-only download.
That download contacts official publishers but never contacts a model provider
or spends money. Choose `--skip-core` to initialize without the offer; scripts
and JSON callers use `--install-core` when they want the download explicitly.

Interactive setup also explains and offers optional model-backed operation. If
accepted, it selects the packaged OpenAI answer and embedding profiles and either
uses the available OS credential store, offers the explicit owner-only file, or
prints the environment-variable route. Credential entry is hidden. Configuration
does not validate the key, contact the provider, build embeddings, or spend money.

The same command can also prompt for an OpenAI credential without echo and can
explicitly start the resumable free source installation:

```bash
uv run nyc-housing setup --configure-openai --install-core
```

The OS keyring is used by default. Add `--credential-storage file` only after
deliberately choosing the owner-only workspace fallback. `--configure-openai`
selects the packaged OpenAI profiles unless explicit profile arguments override
them. `--install-core` never builds embeddings and never makes a paid provider
request.

Default retention is 30 days for operational job/log metadata, 30 days for HPD
property cache, and 12 calendar months for completed usage attempts. Configure
these independently at setup or later in the browser Settings view:

```bash
uv run nyc-housing setup \
  --operational-retention-days 30 \
  --property-cache-retention-days 30 \
  --usage-retention-months 12 \
  --max-paid-concurrency 1 \
  --answer-deadline-seconds 60
```

To use a workspace somewhere else:

```bash
uv run nyc-housing --data-dir "/path/with spaces/workspace" setup
```

An explicit `--data-dir` and `--config` win over `NYC_HOUSING_DATA_DIR` and
`NYC_HOUSING_CONFIG`, which win over OS defaults. Local mode deliberately ignores
legacy `DATABASE_URL`, S3, and legacy provider settings from `.env`.

Default data locations are:

- macOS: `~/Library/Application Support/nyc-housing-rag`
- Windows: `%LOCALAPPDATA%\nyc-housing-rag` (configuration under `%APPDATA%`)
- Linux: `$XDG_DATA_HOME/nyc-housing-rag` or
  `~/.local/share/nyc-housing-rag` (configuration under `$XDG_CONFIG_HOME`)

Avoid network/cloud-synchronized folders for live SQLite databases. `doctor`
detects common path names, but cannot identify every sync or network filesystem.

## Install public sources and search without a key

```bash
uv run nyc-housing corpus install core --text-only
uv run nyc-housing corpus verify
uv run nyc-housing sources
uv run nyc-housing search "What does RPAPL section 711 cover?"
uv run nyc-housing evaluate --offline
```

Installation downloads current documents from their official publishers,
validates required anchors, creates immutable content-addressed artifacts, builds
generation-scoped FTS5 search, and activates only a validated generation. A
failed update leaves the previous active generation usable.

The browser Sources view can also install/update the core, display durable job
progress, cancel or resume source jobs, verify the active generation, and roll
back. It can estimate and start semantic indexing too; a paid profile requires a
configured credential plus confirmation of the displayed hard ceiling. Installing
one module into an incomplete corpus requires a separate partial-activation
confirmation.

The default fake profiles are synthetic test/demo adapters, not substantive
answer models. Their output is labeled `synthetic_demo`; select the OpenAI
profiles and configure a credential to enable real model-backed answers.

The Research view offers Auto, Search sources, Answer, and Property modes. Auto
conservatively chooses only between free local source search and a typed property
lookup; it never starts a model charge. Choose Answer explicitly for generation,
or correct an automatic choice with the mode selector. Questions containing a
legal citation stay in legal search even if they also mention a building ID.

## Configure a user-supplied OpenAI key

```bash
uv run nyc-housing profiles select answer openai-answer-luna-v1
uv run nyc-housing profiles select embedding openai-embedding-3-small-v1
uv run nyc-housing credentials set openai
uv run nyc-housing credentials validate openai --approve-cost --max-cost-usd 0.000001
uv run nyc-housing corpus index --estimate-only
uv run nyc-housing corpus index --approve-cost --max-cost-usd 1.00
uv run nyc-housing ask "What are an owner's repair duties?"
```

The key prompt is hidden. The OS credential store is preferred. If it is absent,
rerun with `--storage file`; this creates an owner-readable `credentials.json` in
that workspace. Environment-only resolution is also supported with
`NYC_HOUSING_OPENAI_API_KEY`. Do not put a key in normal settings or arguments.

The embedding estimate is read-only and reports reusable chunks, estimated input
tokens, and projected spend. Paid credential validation and bulk indexing require
`--approve-cost` and `--max-cost-usd`; the effective ceiling cannot exceed the
workspace operation cap.

## Advanced OpenAI-compatible endpoints

An advanced installation can override the endpoint for either selected OpenAI
profile. The override keeps that profile's declared model and request contract;
it is not a generic arbitrary-model adapter. Supply the endpoint's actual prices,
the price source/date, its declared response-retention behavior, and a credential
slot. For example:

```bash
uv run nyc-housing profiles configure-endpoint answer \
  https://gateway.example/v1/responses \
  --auth-slot answer-gateway \
  --input-price 0.30 --output-price 1.50 \
  --price-effective-date 2026-09-14 \
  --price-source https://gateway.example/pricing \
  --stores-response no
uv run nyc-housing credentials set answer-gateway
uv run nyc-housing profiles check answer --estimate-only
uv run nyc-housing profiles check answer \
  --approve-cost --max-cost-usd 0.01
```

Repeat with `embedding`, an endpoint ending in the compatible embeddings route,
and `--input-price`; omit `--output-price`. A second `--auth-slot` provides a
separate key. Environment-managed slots use
`NYC_HOUSING_AUTH_ANSWER_GATEWAY`-style names. HTTPS is mandatory except for a
loopback HTTP service, URLs cannot embed credentials/query strings, and secrets
are still entered only through hidden input or environment/keyring storage.

The configuration receives a derived effective profile ID. This prevents vectors
from a different endpoint configuration being silently reused. Every new or
changed override is blocked until its explicitly metered compatibility check
passes; no fallback endpoint/model is attempted. Run `profiles list --json` to
inspect effective IDs and check state, and `profiles clear-endpoint KIND` to
return to the packaged profile. Restart the browser app after any profile change.

If the endpoint operator does not publish usable prices, replace all price flags
in the configuration command with `--pricing-unknown`. Partial price metadata is
rejected. The compatibility check then requires `--allow-unknown-cost`. The same
flag is required for each deliberate CLI `ask` or `debug answer`; the browser
shows an equivalent confirmation before an Answer or property-summary request.
These calls are labeled `cost_known: false` and are outside the application's USD
caps. Unknown-priced profiles cannot build a semantic index or run the answer
evaluation suite.

After the index is ready, a technical answer-suite estimate/run is available:

```bash
uv run nyc-housing evaluate --answers --estimate-only --json
uv run nyc-housing evaluate --answers --approve-cost --max-cost-usd 1.00 --json
```

The second command can make multiple provider calls. Its JSON contains the
questions, expected propositions and missing facts, returned sources/citations,
answers, actual local ledger deltas, and an explicit `domain_review_required`
status. It is not a substitute for legal review.

## Start the browser application

```bash
uv run nyc-housing serve
```

The launcher binds only to loopback, creates a five-minute one-use credential,
places it in the URL fragment, exchanges it for an HttpOnly same-site session,
and removes the fragment. For a remote terminal or browser-launch failure:

```bash
uv run nyc-housing serve --no-browser
```

Enter the printed one-time code on the local page. Restarting the application
invalidates old browser sessions. LAN/public binding is intentionally unsupported.

## Property research

Property lookup queries the official filtered HPD violations API and does not
download the citywide dataset:

```bash
uv run nyc-housing property search \
  --house-number "10" --street-name "Court St" --borough BK \
  --violation-class C --status open \
  --inspection-date-from 2026-01-01 --inspection-date-to 2026-12-31 \
  --limit 50
```

An optional Socrata app token can be stored with
`nyc-housing credentials set socrata`. It is not an HPD bulk snapshot and does
not enable model answers. A bounded complete export is explicit:

```bash
uv run nyc-housing property export \
  --building-id 12345 --max-pages 20 --deadline 120
```

The browser and CLI expose building/registration ID, structured address, ZIP,
class, simplified status, and inclusive inspection-date bounds. `open` maps to
the source value `Open`; `closed` maps to `Close`. Original status fields remain
in every returned/exported row. Responses report acquisition start/end,
nullable total count, `has_more`, and a request-bound cursor. The source-update
time is null when NYC Open Data does not provide one for the identical result.

The browser offers Refresh, Next page, an official-source link, loaded-page CSV,
and a durable, cancellable export capped at 20 pages. The latter reports when
the page/deadline bound makes it partial.

## Offline mode

Place `--offline` before the command. Installed legal search and cached property
pages work; source downloads, live HPD lookups, update checks, and remote model
operations are blocked before HTTP construction.

```bash
uv run nyc-housing --offline status
uv run nyc-housing --offline search "RPAPL 711"
```
