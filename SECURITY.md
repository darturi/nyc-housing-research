# Security Policy

NYC Housing Research is a local application that handles provider credentials,
research questions, imported documents, property queries, and saved workspace
data. Please report security problems privately and avoid including real secrets
or personal housing information in a report.

## Supported versions

Until the first stable release, security fixes are made on the current `main`
branch. Older commits, development artifacts, and untagged builds are not
independently supported.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting from this repository's **Security**
tab. Do not open a public issue with exploit details, credentials, addresses,
private documents, or other sensitive information.

If private vulnerability reporting is unavailable, open a minimal issue titled
`Security contact request` without technical or personal details. The maintainer
can then establish a private channel before you share the report.

If you believe a real API key or other credential was exposed, revoke or rotate
it with the provider before reporting it. A useful report includes:

- The affected version or commit.
- The operating system and installation method.
- The security impact and affected data.
- Minimal reproduction steps using synthetic data.
- Any known mitigation or workaround.

## Security-sensitive areas

Reports are especially useful for problems involving:

- Credential storage, redaction, or unintended disclosure.
- Loopback access, launch codes, sessions, CSRF, or origin validation.
- Archive extraction, file import, path handling, backup, or restore.
- Offline-mode bypasses or unexpected outbound network requests.
- Provider-cost approval, usage accounting, or budget enforcement.
- Leakage of questions, addresses, imported files, diagnostics, or saved matters.

Incorrect or outdated legal information is a quality and safety concern rather
than a software vulnerability. It may be reported through a normal issue, but do
not include personal case details or information that should remain private.

## Disclosure and response

This is an independently maintained project, so response times are best effort.
The maintainer will verify reports, coordinate a fix when appropriate, and ask
reporters to avoid public disclosure until affected users have a reasonable
opportunity to update.
