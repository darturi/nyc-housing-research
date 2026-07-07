# Work Log: Phase 2 Password-Protected Access

Date: 2026-07-01

## Summary

Implemented Phase 2 of the NYC Housing Law RAG MVP based on
`docs/MVP Implementation Breakdown/Phase_2_Password_Protected_Access.md`.

This phase added private account authentication, server-side sessions,
password hashing, login/logout/current-user endpoints, admin-only
authorization support, user creation CLI commands, and tests.

## Files Added

-   `app/auth/__init__.py`
-   `app/auth/password.py`
-   `app/auth/sessions.py`
-   `app/auth/dependencies.py`
-   `app/api/auth.py`
-   `app/cli/__init__.py`
-   `app/cli/users.py`
-   `app/schemas/__init__.py`
-   `app/schemas/auth.py`
-   `app/models/user.py`
-   `app/models/session.py`
-   `app/db/migrations/versions/20260701_0002_add_users_sessions.py`
-   `tests/test_auth.py`
-   `tests/test_user_cli.py`

## Files Updated

-   `.env.example`
-   `README.md`
-   `pyproject.toml`
-   `uv.lock`
-   `app/main.py`
-   `app/core/config.py`
-   `app/db/session.py`
-   `app/db/migrations/env.py`
-   `app/models/__init__.py`
-   `tests/conftest.py`

## Implementation Details

### User Model

Added a `users` table with:

-   UUID primary key
-   Normalized unique email
-   Password hash
-   Admin flag
-   Active flag
-   Created/updated timestamps
-   Last login timestamp

### Session Model

Added a `sessions` table with:

-   UUID primary key
-   User foreign key
-   Hashed session token
-   Expiration timestamp
-   Revocation timestamp
-   Last-seen timestamp
-   User agent
-   IP address

The application stores only a hash of the session token. The raw token is
sent to the browser in an HTTP-only cookie.

### Password Handling

-   Added Argon2 password hashing through `argon2-cffi`.
-   Added password verification.
-   Added minimum password length validation.
-   Plaintext passwords are not stored.

### Authentication Endpoints

Added:

-   `POST /auth/login`
-   `POST /auth/logout`
-   `GET /auth/me`

Login normalizes the email, verifies the password, creates a server-side
session, and sets an HTTP-only cookie.

Logout revokes the server-side session and clears the cookie.

`/auth/me` returns the current user only when a valid session is present.

### Authorization Dependencies

Added:

-   `get_current_user`
-   `require_admin`

These reject missing, expired, revoked, or inactive-user sessions.

### CLI User Creation

Added:

``` text
uv run python -m app.cli.users create-admin --email admin@example.com
uv run python -m app.cli.users create-user --email user@example.com
```

The CLI prompts for a password without echoing it and rejects duplicate
emails.

### Test Database Setup

Updated tests to use an in-memory SQLite database. This keeps unit tests
independent from the local Docker PostgreSQL container while still testing
the application behavior.

## Verification

Commands run:

``` text
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run alembic upgrade head
uv run alembic current
```

Results:

-   `pytest`: passed, 16 tests.
-   `ruff check`: passed.
-   `alembic upgrade head`: applied
    `20260701_0001 -> 20260701_0002`.
-   `alembic current`: confirmed `20260701_0002 (head)`.

## Known Note

The test suite still emits a dependency warning:

``` text
StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated
```

This warning comes from the installed FastAPI/Starlette test client stack,
not from application code. It does not currently block the MVP.

## Next Step

Manually validate the new auth flow against the running PostgreSQL-backed
app:

``` text
uv run python -m app.cli.users create-admin --email admin@example.com
uv run uvicorn app.main:app --reload
```

Then test login, `/auth/me`, and logout with curl or a REST client.

Phase 3 can begin once the authentication flow is manually confirmed.
