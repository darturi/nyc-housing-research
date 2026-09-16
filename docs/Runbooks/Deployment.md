# Deployment Runbook

> Historical hosted-mode runbook. The supported local-distribution workflow does
> not require deployment; see [Setup](../Setup.md).

## Purpose

Deploy the private NYC Housing RAG MVP with production-safe configuration,
database migrations, HTTPS-only access, and post-deploy smoke tests.

## Required Production Settings

Configure these in the hosting provider secret manager or environment settings:

``` text
APP_ENV=production
DATABASE_URL=...
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=lax
RATE_LIMIT_ENABLED=true
ARTIFACT_STORAGE_BACKEND=local
ARTIFACT_STORAGE_PATH=...
EMBEDDING_PROVIDER=openai
EMBEDDING_API_KEY=...
ANSWER_LLM_PROVIDER=openai
ANSWER_LLM_API_KEY=...
```

For temporary private testing with fake providers, set this explicitly:

``` text
ALLOW_FAKE_PROVIDERS_IN_PRODUCTION=true
```

Fake providers are deterministic test tools and do not provide production
answer quality.

Do not commit `.env`, database credentials, API keys, object storage
credentials, monitoring DSNs, or copied production environment output.

## HTTPS Requirements

-   Serve the app only behind HTTPS in production.
-   Redirect HTTP to HTTPS at the hosting provider or reverse proxy layer.
-   Set `SESSION_COOKIE_SECURE=true`.
-   Keep `SESSION_COOKIE_SAMESITE=lax` unless a stricter setting is tested with
    the deployed login flow.
-   Do not expose the app directly over an unencrypted public port.
-   Do not use fake model providers in production unless the deployment is an
    explicitly limited technical smoke test.

## Release Procedure

1.  Confirm the target commit or deployment artifact.
2.  Confirm production secrets are configured.
3.  Confirm database backups are enabled.
4.  Build the app image or deploy artifact.
5.  Run migrations before serving traffic:

    ``` text
    uv run alembic upgrade head
    ```

6.  Start the app:

    ``` text
    uvicorn app.main:app --host 0.0.0.0 --port $PORT
    ```

7.  Run the smoke test checklist below.
8.  Record deployment timestamp, app version, and Alembic revision.

For platforms with release commands, run `uv run alembic upgrade head` as the
release command before web traffic moves to the new app process.

## Smoke Test Checklist

-   `GET /health` returns `200`.
-   Anonymous `POST /search` returns `401`.
-   Anonymous `POST /answer` returns `401`.
-   Admin user can log in.
-   Authenticated `GET /auth/me` returns the current user.
-   Authenticated `/search` returns results after ingestion and embeddings.
-   Authenticated `/answer` returns a disclaimer and source-backed citations.
-   Repeated failed login attempts eventually return `429`.
-   Oversized request returns `413`.
-   `uv run alembic current` reports the expected head.

## Production User Setup

Create the first admin user:

``` text
uv run python -m app.cli.users create-admin --email admin@example.com
```

Create private users:

``` text
uv run python -m app.cli.users create-user --email user@example.com
```

The commands prompt for passwords without echoing them. Do not pass passwords
as command-line arguments.

## Deployment Record Template

``` text
Date:
Operator:
App version/commit:
Alembic revision:
Backup confirmed:
Smoke tests passed:
Notes:
```
