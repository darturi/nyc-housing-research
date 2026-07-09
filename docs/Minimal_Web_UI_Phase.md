# Minimal Web UI Phase

## Purpose

This phase adds the first browser-facing version of the NYC Housing RAG MVP.
Before this phase, the project was usable through CLI commands, API requests,
and FastAPI's generated docs. This phase creates a simple private web
workspace so a tester can log in, ask a question, and read the routed result in
a normal browser.

The goal is not a polished production frontend yet. The goal is to prove the
core private-user workflow:

1. Sign in with an existing account.
2. Ask a housing-law or property/HPD question.
3. See whether the system routed the question to legal answer generation or
   HPD violation lookup.
4. Review answer text, citations/source links, HPD violation rows, unsupported
   answer states, source coverage, and disclaimer text.

## What You Should See

Open the running local app:

```text
http://127.0.0.1:8010
```

If the server is not running, start it from the repo root:

```bash
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

The browser flow should look like this:

- `/` redirects to `/login` when you are not signed in.
- `/login` shows a private-access sign-in form.
- After signing in, the app redirects to `/app`.
- `/app` shows a research workspace with:
  - current user email in the header
  - sign-out button
  - one question text box
  - result area below the question box

Try these questions:

```text
What does the HMC say about heat and hot water?
```

Expected result: a legal answer routed through the RAG answer path, with at
least one Housing Maintenance Code citation/source link.

```text
How can a tenant report a housing complaint to HPD?
```

Expected result: a guidance-based answer with an HPD source link.

```text
Show HPD violations at 123 MAIN STREET
```

Expected result: a property/HPD route. If matching test or local data exists,
the UI renders an HPD violation count and a compact table. If no matching data
exists in your local database, the UI should show a zero-result state.

## What Was Added

This phase added a lightweight server-rendered UI inside the existing FastAPI
application:

- `app/web.py`: web routes for `/`, `/login`, and `/app`
- `app/templates/login.html`: login page
- `app/templates/app.html`: authenticated workspace page
- `app/static/web.css`: page styling
- `app/static/web.js`: login, logout, query submission, and result rendering
- `tests/test_web_ui.py`: route and static-asset tests

It also adds `jinja2` as a runtime dependency because FastAPI uses it to render
HTML templates.

## How It Works

The UI reuses the existing backend APIs rather than adding a separate frontend
service:

- Login form posts JSON to `POST /auth/login`.
- The session cookie is the same HTTP-only cookie already used by the API.
- The query form posts JSON to `POST /query`.
- `/query` decides whether the question is a legal/RAG question or an HPD
  property question.
- Legal answers render answer status, answer text, citations, source coverage,
  and disclaimer.
- HPD property results render count and violation rows.

No database schema changes are part of this phase.

## Current Limits

This is intentionally minimal:

- No separate React, Next.js, Tailwind, or frontend build pipeline.
- No account creation UI.
- No admin dashboard.
- No saved question history UI.
- No advanced filtering controls yet.
- Styling is functional and private-MVP oriented, not a final product design.

## Verification

Automated checks run for this phase:

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
```

Expected current result:

```text
136 passed
ruff clean
```

Manual browser check:

1. Start the server.
2. Open `http://127.0.0.1:8010`.
3. Sign in with your existing admin account.
4. Submit the sample questions above.
5. Confirm the result area updates without using Swagger, curl, or the CLI.

## Next Product Step

After this phase, the next useful MVP step is a real-provider smoke pass:

- configure production-like embedding and answer providers
- regenerate embeddings
- run the evaluator
- test the web UI with real model-backed answers
- inspect answer logs, retrieval logs, and provider errors

