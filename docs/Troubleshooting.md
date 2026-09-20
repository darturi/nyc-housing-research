# Troubleshooting and recovery

## One-command launch

Start by rerunning `sh start.sh` (macOS/Linux) or
`powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1` (Windows).
The launcher preserves settings and installed publications and retries eligible
unfinished free source installation. Read the stage label above any error.

- **Runtime or dependency download fails:** check internet/proxy access to Astral,
  GitHub (Python/runtime downloads) and Python package indexes, then rerun. No
  workspace source installation starts until dependencies are ready. On a system
  without curl or wget, install one using the operating system's package manager.
- **Windows policy blocks the script:** use the full PowerShell command above;
  it selects the policy for that invocation only. Organization-managed policies
  may require your administrator's help.
- **Sources fail to download:** the app still opens. Use Sources to retry/resume,
  or stop with Ctrl+C and repeat the launch command. After a forced termination,
  an old worker lease can take up to five minutes to expire. Downloads can repeat.
- **The app is already running:** use the existing browser and terminal, or stop
  that terminal process with Ctrl+C and launch again. Do not delete lock files.
- **Browser did not open:** open the printed local address and paste the printed
  one-time code. Use `--no-browser` when you want this behavior deliberately.
- **Port conflict:** the default automatically falls back to a free local port.
  Remove an explicit `--port` option or choose another port.
- **Offline dependencies are missing:** first use requires internet; rerun without
  `--offline`. Offline cannot install uncached runtimes, dependencies or sources.
- **Incompatible/incomplete workspace:** keep it intact. Use its matching app
  version or restore a verified backup into a new workspace. The desktop app
  offers a confirmed backup-and-upgrade flow for supported older schemas. CLI
  users can run `migrate preflight` and, when available, `migrate apply`.
  Unknown/newer schemas are never silently migrated. See [Updating](Updating.md).

For deeper diagnostics in an existing uv environment, start with:

```bash
uv run nyc-housing status --json
uv run nyc-housing doctor --json
```

`doctor` is read-only and makes no network or paid call. Add `--online` only when
you want a bounded, non-paid NYC Open Data schema check.

CLI exit categories are stable for scripts: `2` invalid configuration, `3`
unavailable dependency/service, `4` budget or explicit cost-ceiling denial, `5`
cancelled/deadline-interrupted work, and `6` failed validation or acceptance
threshold. Human-readable errors use the matching category prefix.

## Common states

**Workspace is not initialized.** Run `nyc-housing setup`, with the same global
`--data-dir`/`--config` options you intend to use later.

**Python check fails.** Use Python 3.12 and rerun `uv sync --locked`. Other minors
are intentionally rejected for the first release line.

**No active legal corpus.** Run `corpus install core --text-only`. If a publisher
is unavailable, retry later or import a canonical data-only bundle obtained under
an appropriate redistribution decision. A failed staging generation does not
replace the active generation.

**Source verification fails.** Do not delete the active corpus. Inspect `sources`,
retry the affected official source, or `corpus rollback` if a retained generation
is available. Hash, missing artifact, FTS-count, citation, and embedding-profile
errors fail closed.

If automated download repeatedly fails but you can obtain the official artifact
yourself, import it without weakening provenance checks:

```bash
uv run nyc-housing corpus import-artifact ny-rpapl ./RPA.pdf \
  --source-url "https://legislation.nysenate.gov/pdf/laws/RPA?full=true"
```

The file is size-bounded, validated with the packaged parser/anchors, stored by
content hash, and merged with currently active modules. On a new empty workspace,
activating only one module additionally requires `--allow-partial`.

**No OS keyring.** Install with `--extra credentials`, configure an OS backend, or
explicitly use `credentials set openai --storage file`. On Unix the fallback file
must be owner-only.

**Model provider unavailable or budget denied.** Evidence search remains usable.
Check `usage`, credential presence, selected profiles, offline mode, and configured
monthly/per-operation caps. Lost responses remain “uncertain” rather than being
silently retried and potentially double-billed.

Use `credentials validate openai --approve-cost --max-cost-usd N` for an explicit
minimal capability check. It is metered and may charge; credential presence alone
does not prove validity. Use `debug answer --question TEXT --json` to inspect the
generation, selected profiles, retrieved evidence, operation ID, and ledger events
without exposing the key or hidden provider prompt.

**Custom endpoint has not passed its compatibility check.** Confirm its selected
base profile, endpoint route, credential slot, declared prices (or explicit
unknown-pricing state), price-source URL, and retention flag with
`profiles list --json`. Store the slot's key and run
`profiles check KIND --estimate-only`. Approve a known-price check with
`--approve-cost --max-cost-usd N`; approve an unknown-price one-off with
`--allow-unknown-cost`, which is outside USD caps. The check validates the
Responses or embeddings shape and embedding dimensions; it never falls back. Any
override change clears the result. Use `profiles clear-endpoint KIND` to restore
the packaged endpoint.

**Unknown-priced profile will not index or evaluate.** This is intentional:
automatic and batch paid work requires defensible price metadata. Add complete
current pricing with `profiles configure-endpoint`, or keep keyless/keyword search
and use `ask --allow-unknown-cost` only for deliberate one-off answers. Inspect
separate attempt counts with `usage --json`; use the provider dashboard for the
actual charge.

**Property lookup unavailable.** A fresh or up-to-30-day stale successful cached
page can still be returned. Verified empty results expire after one hour and
transport/schema failures are not negative-cached. Narrow ambiguous searches with
a borough, ZIP, or selected building ID.

**Workspace metadata or cache is growing.** Preview the configured retention
policy, inspect the protected/candidate counts, then apply it explicitly:

```bash
uv run nyc-housing maintenance prune --json
uv run nyc-housing maintenance prune --apply --json
```

Applied cleanup refuses active jobs/provider calls and leaves unresolved spend,
current-month usage, nonterminal jobs, and pinned cache entries intact. If file
deletion failures are nonzero, check workspace permissions and rerun; database
records have already been safely detached, so a failed artifact is only orphaned.

**Browser session rejected.** Restart `serve`, use the newly opened fragment or
one-time code, and do not reuse it. Restarts deliberately invalidate prior
sessions. The host must remain loopback.

**A browser answer result expired.** Answer payloads intentionally live only in
memory and expire after 30 minutes of inactivity. The durable job returns
`expired_result` and requires explicit resubmission; it will not replay a paid
request automatically.

**A maintenance job was interrupted.** Run `jobs list --json`, then explicitly
`jobs resume JOB_ID` or `jobs cancel JOB_ID`. Corpus install/update/index and
complete property-export jobs retain bounded resume state. Interactive answers do
not persist their payload and therefore cannot be resumed after process loss.

**Settings changed but a running job uses old values.** Saved settings apply to
new processes. Restart `serve` before launching provider or maintenance jobs after
changing profiles, budgets, retention, or offline mode in the browser.

**SQLite locking or corruption warning.** Stop other app/CLI writers. Move the
workspace off network/cloud-sync storage. Create no manual partial database copy;
use the maintenance-barrier backup command.

## Backup and restore

```bash
uv run nyc-housing backup ./workspace.zip
uv run nyc-housing backup ./workspace-with-cache.zip --include-property-cache
uv run nyc-housing restore ./workspace.zip --destination /new/workspace/path
```

Backup refuses active jobs, pauses new jobs/cache writes/paid calls behind a
maintenance barrier, takes online SQLite snapshots, sanitizes session state,
copies referenced immutable artifacts, and hashes every member. Restore requires
a destination that does not exist and stages/verifies before renaming it into
place. Corrupt archives leave no partial destination.

Credentials must be reconfigured after restore. Default backups exclude property
cache and all backups exclude exports and in-memory transcripts.

## Reporting a problem

Create the default non-network, non-paid support report at a path you choose:

```bash
uv run nyc-housing diagnostics export ./nyc-housing-diagnostics.json
```

The export includes application/runtime/schema/readiness metadata while replacing
workspace and home-directory paths. It excludes credentials, session/bootstrap
values, questions, addresses, prompts, and provider response bodies. Include this
report, the command exit category, and steps to reproduce. Do not attach
`credentials.json`, API keys, researched addresses, workspace databases, private
bundles, or raw provider responses.

Sanitized metadata-only diagnostic logs are under the workspace `logs` directory.
They contain event/path/status/error-type metadata, not request bodies, questions,
addresses, credentials, session values, or provider responses. Apply the separate
operational-retention policy when removing old logs.
