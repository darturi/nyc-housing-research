# Privacy and data flow

## Local by default

The application has no maintainer telemetry, hosted account, cloud query history,
or automatic diagnostic upload. Corpus/state databases, artifacts, usage events,
property cache, and exports live in the selected workspace. Question, context,
and answer payloads remain only in process memory by default and expire after 30
minutes of inactivity or process exit.

The local security boundary protects against ordinary browser cross-origin and
DNS-rebinding requests. It does not protect data from malware or another process
already running with the same OS-user privileges.

## External destinations

| Destination | Data sent | User action |
| --- | --- | --- |
| Official legal publishers | Public document request and normal HTTP metadata | Corpus install/update |
| NYC Open Data | Structured property identifier, address/filter fields, optional app token | Live property lookup/refresh |
| OpenAI | Question plus selected legal excerpts; selected HPD rows for property summaries | Model answer/summary |
| OpenAI embeddings | Eligible legal chunk text during approved indexing; query text for semantic retrieval | Index or semantic answer/search path |
| User-configured OpenAI-compatible endpoint | The same selected answer or embedding payload sent to that endpoint only | Explicit advanced profile configuration and compatibility check |
| GitHub API | Repository release request and normal HTTP metadata | Manual `update-check` |

OpenAI answer calls explicitly send `store: false`; that is an API request option,
not a claim about every aspect of provider-side abuse monitoring or retention.
Review the provider's current policy before use. A Socrata token is optional and
is treated as a secret.

User-provided resources are stored in the local workspace and begin with model
use disabled. Local exact and keyword search still work. Semantic indexing and
generated answers exclude that resource until its per-resource provider-use
setting is explicitly enabled; disabling it again removes it from subsequent
provider-backed retrieval and indexing work.

For a custom endpoint, `stores_response` and any pricing metadata are supplied by
the operator and are not independently verified by this application. The endpoint
must use HTTPS except for loopback development services. Changing it creates a
new effective profile, clears compatibility approval, and never falls back to the
packaged OpenAI endpoint.

An endpoint may instead be marked as having unknown pricing. Automatic and batch
model work is then blocked. Each allowed one-off manual request requires explicit
confirmation, is labeled `cost_known: false`, and is counted separately in the
local ledger. Because no defensible currency estimate exists, those requests are
outside the local USD caps and the endpoint's own billing record is authoritative.

## Secrets

Resolution order is environment, OS keyring, then the explicitly selected
owner-readable secret file. Protected APIs disclose only presence and storage
source. Keys are excluded from ordinary settings, status output, logs, canonical
corpus bundles, workspace backups, browser storage, exports, and command arguments.
Advanced credential slots are workspace-scoped in the same stores and can use
environment names such as `NYC_HOUSING_AUTH_ANSWER_GATEWAY`.

## Persistent metadata

Durable jobs store operation IDs, sanitized state/progress/errors, and no raw
interactive question. Usage events store operation/provider/profile, token counts,
price snapshots, cost-known state, numeric amount or an explicitly labeled
unknown-cost sentinel, and status. Property-cache request metadata can reveal a
researched address; clear it from Settings, the API, or
`nyc-housing property cache-clear`.

Operational job/log records and property-cache entries default to 30-day
retention; completed usage attempts default to 12 calendar months. The controls
are independent and configurable in Settings. Preview with
`nyc-housing maintenance prune --json`; deletion requires the explicit `--apply`
flag (or the separate Apply button in Settings). Cleanup never deletes
nonterminal jobs, pinned cache entries, unresolved reserve/uncertain usage
attempts, or attempts in the current budget month. If completed usage history is
pruned, usage/status output records the earliest history boundary so the local
estimate does not imply complete historical billing. The provider dashboard
remains authoritative.

The explicit `diagnostics export` command performs no network or paid check and
replaces workspace/home paths while omitting secrets, sessions, questions,
addresses, prompts, and provider response bodies.

Default backups exclude property cache unless `--include-property-cache` is
explicit. They always exclude credentials, local sessions, launcher tokens,
memory-only transcripts, and unrelated exports. Research exports intentionally
contain the question/evidence or property rows the user selected. Workspace
backups include user-provided resources so restore is complete. Portable corpus
bundles contain official core sources only and exclude user resources.

## Offline mode

`--offline` is enforced by the shared network policy for source downloads, HPD,
providers, and update checks. Loopback serving remains available. Tests verify
that blocked work is rejected before transport/provider accounting is invoked.
