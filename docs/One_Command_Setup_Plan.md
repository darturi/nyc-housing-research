# One-command setup and launch

Date: 2026-09-18. Scope: the cloned repository, without a preinstalled Python or
uv, for a single local user. OpenAI key provision remains optional in Settings.

Status: implemented in `start.sh`, `start.ps1`, `app/launcher.py`, and the `start`
CLI command. Native cross-platform CI is configured; its remote results are not
claimed here.

## User contract

From the repository, run `sh start.sh` on macOS/Linux or
`powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1` on Windows.
The same command handles first use and subsequent launches. It prepares the
runtime, dependencies, local workspace and free official-source search, then
opens the browser. Keep the terminal open; Ctrl+C stops the app.

Executing this command authorizes the explained free software/source downloads.
It does not validate API keys, generate answers, buy embeddings, change models,
or run paid evaluations. Existing sources are reused, not updated at each start.

## Implementation work

1. **Bootstrap scripts.** Resolve the checkout independently of the current
   directory. Reuse the tested uv version, otherwise install it privately under
   `.bootstrap` through Astral's versioned HTTPS installer. Do not require sudo,
   edit shell profiles, or replace a user-managed uv. Let uv obtain Python 3.12,
   install the committed lock with the credentials extra, and preserve extra
   contributor packages. Forward arguments and failures intact. Help must work
   before any downloads. Offline applies to both bootstrap and application.
2. **Application command.** Add `nyc-housing start`, with optional workspace,
   config, port, no-browser, skip-core, setup-only and offline arguments. Initialize
   missing storage without invoking the interactive `setup` prompts. Check both
   existing database schemas before initialization or settings writes. Preserve
   existing configuration, credentials, partial corpora and custom profiles.
3. **Source preparation.** On an empty online workspace, install the five official
   sources using existing durable jobs and validation. Display stages/progress.
   Retry eligible failed/interrupted free installation jobs on the next run;
   never resume unrelated or paid work. A publisher failure should leave an
   accessible browser recovery path and a clear incomplete status. Setup-only
   must report failure to scripts. Offline and skip-core intentionally omit it.
4. **Reliable launch.** Hold a per-workspace OS lock throughout startup and
   serving so a second launcher cannot invalidate browser sessions. Bind the
   configured loopback port before creating a session; automatically select a
   free port when the default is occupied. An explicitly requested port fails
   with an actionable error. Open the browser only after server readiness and
   print the one-time code when browser launch fails. Keep Ctrl+C graceful.
5. **Documentation.** Make the two platform commands the main quickstart. Explain
   downloads, repeat launches, where data lives, optional OpenAI configuration,
   setup-only and offline behavior, and recovery. Keep individual CLI commands
   documented for contributors and advanced operators.
6. **Verification.** Exercise fresh/repeated/partial/offline setup, failed source
   retry, settings preservation, incompatible schemas, occupied ports, duplicate
   launches, browser failure, and startup readiness with isolated workspaces and
   fixtures. Test actual shell argument forwarding and bootstrap failure handling.
   Add native CI smoke coverage for scripts, including fresh dependency setup.

## Recorded verification

- Local macOS arm64: a temporary checkout with no virtual environment, no uv on
  PATH, and empty isolated runtime/cache directories downloaded uv 0.11.14,
  CPython 3.12.13 and 36 locked packages through the single launch command.
- The same run acquired all five live official sources, activated and verified
  741 passages, and exited successfully with `--setup-only`. No API key or paid
  request was used.
- An offline repeat reused the installed library and dependencies, selected a
  free loopback port when 8000 was occupied, and served HTTP 200. An exact/keyword
  query returned RPAPL section 711. The temporary server was stopped afterward.
- Full local regression suite: 325 passed, one opt-in browser test skipped;
  four existing dependency deprecation warnings. The new live-launch test covers
  authenticated browser startup and shutdown separately from that skipped test.
- POSIX wrapper tests cover missing/existing runtime, help without downloads,
  offline bootstrap refusal, dependency failure, and paths containing spaces and
  Unicode. Ruff, shell syntax and whitespace checks pass.
- Windows PowerShell and Linux native execution remain to be confirmed by CI;
  their fresh-launch job intentionally omits setup-uv/setup-python and uses an
  empty managed-Python directory and dependency cache.

## Acceptance and limits

- A clean supported computer needs only the checkout, its standard shell, internet
  and a browser (Unix bootstrap additionally needs curl or wget).
- No terminal questions are asked; successful free setup ends at the research UI.
- Repeating the command preserves sources, settings and credentials.
- Bootstrap errors identify the failed stage and allow a repeat of the same command.
- A source outage does not block opening the recovery UI; it cannot be presented
  as a successful source installation.
- Existing security, spending, source-validation and schema protections remain.
- No public release, hosted service, native installer, OS background service or
  automatic code update is included. Python downloads and package caches use uv's
  normal user directories; app data remains in its existing OS-specific workspace.
- Windows/Linux support still requires their native CI and clean-machine runs;
  fixture tests and local macOS runs do not establish those platform results.

Bootstrap follows Astral's documented [versioned installation](https://docs.astral.sh/uv/getting-started/installation/),
[unmanaged installation](https://docs.astral.sh/uv/reference/installer/), and
[automatic Python downloads](https://docs.astral.sh/uv/guides/install-python/).
