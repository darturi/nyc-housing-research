import atexit
import os
import shutil
import tempfile
from pathlib import Path

# Establish isolation before any application module can read settings. These
# values deliberately override, rather than default around, a developer shell
# or checkout .env file.
TEST_WORKSPACE_ROOT = Path(tempfile.mkdtemp(prefix="nyc-housing-tests-"))
atexit.register(shutil.rmtree, TEST_WORKSPACE_ROOT, True)

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["ARTIFACT_STORAGE_BACKEND"] = "local"
os.environ["ARTIFACT_STORAGE_PATH"] = str(TEST_WORKSPACE_ROOT / "artifacts")
os.environ["EMBEDDING_PROVIDER"] = "fake"
os.environ["EMBEDDING_MODEL"] = "fake-small"
os.environ["EMBEDDING_DIMENSION"] = "16"
os.environ["ANSWER_LLM_PROVIDER"] = "fake"
os.environ["ANSWER_LLM_MODEL"] = "fake-answer-small"
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["ANSWER_LLM_API_KEY"] = ""
# Never let ordinary tests unlock, read, or modify a developer's real OS keychain.
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"

import pytest  # noqa: E402

from app import models  # noqa: E402, F401
from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402


def assert_isolated_test_database() -> None:
    database_url = str(engine.url)
    if engine.dialect.name != "sqlite" or database_url != "sqlite+pysqlite:///:memory:":
        raise RuntimeError(
            "Refusing destructive test fixture operations outside the isolated "
            f"in-memory SQLite database (resolved {database_url!r})."
        )


@pytest.fixture(autouse=True)
def reset_database():
    assert_isolated_test_database()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    assert_isolated_test_database()
    Base.metadata.drop_all(bind=engine)
