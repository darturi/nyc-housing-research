from app.cli import users
from app.db.session import SessionLocal
from app.models.user import User

TEST_PASSWORD = "correct horse battery staple"


def test_create_user_cli_creates_normal_user(monkeypatch):
    monkeypatch.setattr(users, "prompt_password", lambda: TEST_PASSWORD)

    users.create_user("USER@example.com", is_admin=False)

    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "user@example.com").one()
        assert user.is_admin is False
        assert user.is_active is True
        assert user.password_hash != TEST_PASSWORD


def test_create_user_cli_creates_admin(monkeypatch):
    monkeypatch.setattr(users, "prompt_password", lambda: TEST_PASSWORD)

    users.create_user("admin@example.com", is_admin=True)

    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "admin@example.com").one()
        assert user.is_admin is True


def test_create_user_cli_rejects_duplicate_email(monkeypatch):
    monkeypatch.setattr(users, "prompt_password", lambda: TEST_PASSWORD)
    users.create_user("user@example.com", is_admin=False)

    try:
        users.create_user("USER@example.com", is_admin=False)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("Expected duplicate email to raise ValueError")
