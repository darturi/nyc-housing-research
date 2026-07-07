# Phase 2: Password-Protected Access Implementation

## Goal

Add private, password-protected access to the MVP. Phase 2 should create
application-managed users and sessions, protect all non-health application
routes, support login/logout, and provide an admin-only way to create users.

The MVP should not have public signup. Every account should be created by
an admin or bootstrap command.

## Deliverables

-   `users` database table
-   `sessions` database table
-   Password hashing utilities
-   Session token generation and hashing
-   Login endpoint
-   Logout endpoint
-   Current-user endpoint
-   Authentication dependency or middleware
-   Admin-only user creation command
-   Tests for authentication, sessions, expiration, and access control
-   Updated environment variables and README instructions

## Design Decisions

### Access Model

Use private accounts, not a shared password.

Reasons:

-   Individual users can be revoked
-   Per-user rate limits can work in Phase 6
-   Access can be audited
-   Session compromise affects one account, not the whole MVP

### Session Model

Use opaque server-side sessions stored in PostgreSQL.

Flow:

1.  User submits email and password.
2.  Server verifies the password hash.
3.  Server creates a random session token.
4.  Server stores only a hash of the session token.
5.  Browser receives the raw token in an HTTP-only secure cookie.
6.  Each request hashes the cookie token and looks up a valid session.
7.  Logout deletes the session.

Do not use JWTs for this MVP. Server-side sessions are easier to revoke,
audit, and expire.

## Suggested Files

``` text
app/
├── api/
│   └── auth.py
├── auth/
│   ├── __init__.py
│   ├── dependencies.py
│   ├── password.py
│   └── sessions.py
├── cli/
│   ├── __init__.py
│   └── users.py
├── models/
│   ├── __init__.py
│   ├── session.py
│   └── user.py
└── schemas/
    ├── __init__.py
    └── auth.py
tests/
├── test_auth.py
└── test_user_cli.py
```

## Database Schema

### `users`

Columns:

-   `id`: UUID primary key
-   `email`: unique, indexed, case-normalized
-   `password_hash`: string, required
-   `is_admin`: boolean, default false
-   `is_active`: boolean, default true
-   `created_at`: timezone-aware timestamp
-   `updated_at`: timezone-aware timestamp
-   `last_login_at`: nullable timezone-aware timestamp

Constraints:

-   Unique email
-   Non-empty password hash

Notes:

-   Store normalized lowercase email for lookup.
-   Do not store plaintext passwords.
-   Do not log password values.

### `sessions`

Columns:

-   `id`: UUID primary key
-   `user_id`: foreign key to `users.id`
-   `token_hash`: unique, indexed string
-   `created_at`: timezone-aware timestamp
-   `expires_at`: timezone-aware timestamp
-   `revoked_at`: nullable timezone-aware timestamp
-   `last_seen_at`: nullable timezone-aware timestamp
-   `user_agent`: nullable string
-   `ip_address`: nullable string

Constraints:

-   Unique `token_hash`
-   Foreign key cascade delete or explicit cleanup behavior

Session validity:

-   User must be active
-   Session must not be revoked
-   `expires_at` must be in the future

## Environment Variables

Add these to `.env.example`:

``` text
SESSION_COOKIE_NAME=nyc_housing_session
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=lax
SESSION_TTL_HOURS=12
PASSWORD_MIN_LENGTH=12
```

Production requirements:

-   `SESSION_COOKIE_SECURE=true`
-   `SESSION_COOKIE_SAMESITE=lax` or `strict`
-   Session cookies must be HTTP-only

## Step 1: Add Password Hashing

Use Argon2id if practical. Bcrypt is acceptable if Argon2 dependencies are
unwanted.

Recommended dependency:

-   `argon2-cffi`

Implement `app/auth/password.py`.

Required functions:

-   `hash_password(password: str) -> str`
-   `verify_password(password: str, password_hash: str) -> bool`
-   `validate_password_strength(password: str) -> None`

Minimum password policy:

-   At least 12 characters
-   Reject empty or whitespace-only passwords

Do not add complex composition rules in the MVP. Length and secure hashing
matter more.

Acceptance criteria:

-   Same password verifies against its hash
-   Wrong password fails verification
-   Plaintext password is never stored

## Step 2: Add User and Session Models

Create SQLAlchemy models:

-   `app/models/user.py`
-   `app/models/session.py`

Update `app/models/__init__.py` or `app/db/base.py` so Alembic can discover
the models.

Create an Alembic migration that adds:

-   `users`
-   `sessions`
-   indexes and constraints

Acceptance criteria:

-   `uv run alembic revision --autogenerate` produces the expected schema
-   `uv run alembic upgrade head` applies cleanly
-   `uv run alembic downgrade -1` rolls back cleanly during local testing

## Step 3: Add Session Utilities

Implement `app/auth/sessions.py`.

Required functions:

-   `create_session(db, user, request) -> str`
-   `hash_session_token(token: str) -> str`
-   `get_session_by_token(db, token: str)`
-   `revoke_session(db, token: str) -> None`
-   `delete_expired_sessions(db) -> int`

Token requirements:

-   Use `secrets.token_urlsafe(32)` or stronger
-   Store only a SHA-256 hash of the token
-   Compare hashes using constant-time comparison where relevant

Acceptance criteria:

-   Raw session token is never stored
-   Revoked sessions stop authenticating
-   Expired sessions stop authenticating

## Step 4: Add Auth Schemas

Implement `app/schemas/auth.py`.

Schemas:

-   `LoginRequest`
-   `UserResponse`
-   `CurrentUserResponse`

Rules:

-   Never include `password_hash`
-   Never include session token values
-   Return only safe user fields

## Step 5: Add Auth Endpoints

Implement `app/api/auth.py`.

Routes:

``` text
POST /auth/login
POST /auth/logout
GET /auth/me
```

### `POST /auth/login`

Request:

``` json
{
  "email": "user@example.com",
  "password": "password value"
}
```

Behavior:

-   Normalize email
-   Find active user
-   Verify password
-   Create session
-   Set session cookie
-   Return safe user object
-   Use the same generic error for unknown email and wrong password

Failure response:

``` text
401 Unauthorized
```

Do not reveal whether an email exists.

### `POST /auth/logout`

Behavior:

-   Revoke current session if present
-   Clear session cookie
-   Return success even if the session is already missing

### `GET /auth/me`

Behavior:

-   Return current safe user object
-   Return `401` if unauthenticated

Acceptance criteria:

-   Login sets an HTTP-only session cookie
-   Logout clears the cookie and revokes the session
-   `/auth/me` works only with a valid session

## Step 6: Add Authentication Dependency

Implement `app/auth/dependencies.py`.

Dependencies:

-   `get_current_user`
-   `require_admin`

Behavior:

-   Read session cookie
-   Hash token
-   Load session and user
-   Reject missing, revoked, expired, or inactive-user sessions
-   Update `last_seen_at` opportunistically
-   `require_admin` rejects non-admin users with `403`

Acceptance criteria:

-   Missing cookie returns `401`
-   Expired session returns `401`
-   Non-admin user receives `403` for admin-only actions

## Step 7: Protect Application Routes

For Phase 2, protect all non-health app/API routes.

Current route exceptions:

-   `GET /health`
-   `POST /auth/login`

Protected routes:

-   `POST /auth/logout`
-   `GET /auth/me`
-   Future search and answer endpoints
-   Future ingestion/admin endpoints

Implementation options:

-   Use route-level dependencies for clarity
-   Add router-level dependencies for protected routers later

Avoid global auth middleware for now because `/health` and `/auth/login`
need to remain public.

## Step 8: Add Admin User Creation Command

Implement `app/cli/users.py`.

Command behavior:

``` text
uv run python -m app.cli.users create-admin --email admin@example.com
uv run python -m app.cli.users create-user --email user@example.com
```

Recommended behavior:

-   Prompt for password without echo
-   Confirm password
-   Enforce password minimum length
-   Normalize email
-   Refuse duplicate email
-   Create active user
-   Set `is_admin=true` only for `create-admin`

Do not accept passwords as command-line arguments because shell history can
capture them.

Acceptance criteria:

-   Admin can be created from a clean database
-   Normal user can be created
-   Duplicate emails are rejected
-   Passwords are never printed

## Step 9: Add Tests

Minimum tests:

-   Password hash verifies correctly
-   Wrong password fails verification
-   Login succeeds for active user
-   Login fails with wrong password
-   Login failure does not reveal whether email exists
-   Login sets HTTP-only cookie
-   Logout revokes session
-   `/auth/me` returns current user with valid session
-   `/auth/me` returns `401` without session
-   Expired session returns `401`
-   Inactive user session returns `401`
-   `require_admin` allows admin users
-   `require_admin` rejects normal users

Test database strategy:

-   Prefer a separate test database for integration tests
-   Unit tests may use transaction rollback or temporary records
-   Do not depend on production `.env`

## Step 10: Update Documentation

Update `README.md` with:

-   New environment variables
-   Migration command
-   Admin creation command
-   Login/logout endpoint examples
-   Cookie security note for production

Update the work log after implementation.

## Security Checklist for Phase 2

-   No public signup
-   Passwords hashed with Argon2id or bcrypt
-   Session tokens generated with cryptographic randomness
-   Only session token hashes stored in the database
-   Session cookie is HTTP-only
-   Session cookie is secure in production
-   Cookie `SameSite` is `lax` or stricter
-   Login error does not reveal whether the email exists
-   Logout revokes server-side session
-   Expired sessions are rejected
-   Inactive users are rejected
-   Admin creation does not expose passwords in shell history
-   Tests cover unauthenticated access

## Manual Validation

After implementation:

1.  Run migrations.

``` text
uv run alembic upgrade head
```

2.  Create an admin.

``` text
uv run python -m app.cli.users create-admin --email admin@example.com
```

3.  Start the app.

``` text
uv run uvicorn app.main:app --reload
```

4.  Login.

``` text
curl -i -c cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"your password"}' \
  http://localhost:8000/auth/login
```

5.  Check current user.

``` text
curl -i -b cookies.txt http://localhost:8000/auth/me
```

6.  Logout.

``` text
curl -i -b cookies.txt -c cookies.txt \
  -X POST http://localhost:8000/auth/logout
```

7.  Confirm session is invalid.

``` text
curl -i -b cookies.txt http://localhost:8000/auth/me
```

Expected final response:

``` text
401 Unauthorized
```

## Phase 2 Completion Criteria

Phase 2 is complete when:

-   `users` and `sessions` tables exist
-   An admin can be created from the CLI
-   Login creates a server-side session
-   Session cookie is HTTP-only
-   Logout revokes the session
-   `/auth/me` requires a valid session
-   Admin-only dependency works
-   Public signup does not exist
-   Tests pass
-   README is updated

## Handoff to Phase 3

Phase 3 can start once authenticated access is stable. Source ingestion
commands and any future ingestion endpoints should use the Phase 2 admin
authorization path.
