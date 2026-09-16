import os
import subprocess
import sys


def test_test_bootstrap_overrides_inherited_remote_configuration() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+psycopg://example.invalid/never_connect",
            "ARTIFACT_STORAGE_BACKEND": "s3",
            "ARTIFACT_S3_BUCKET": "never-use-this-bucket",
            "EMBEDDING_PROVIDER": "openai",
            "EMBEDDING_API_KEY": "inherited-embedding-secret",
            "ANSWER_LLM_PROVIDER": "openai",
            "ANSWER_LLM_API_KEY": "inherited-answer-secret",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import tests.conftest as bootstrap; "
                "from app.core.config import get_settings; "
                "from app.db.session import engine; "
                "settings = get_settings(); "
                "assert settings.app_env == 'test'; "
                "assert str(engine.url) == 'sqlite+pysqlite:///:memory:'; "
                "assert settings.artifact_storage_backend == 'local'; "
                "assert settings.embedding_provider == 'fake'; "
                "assert settings.answer_llm_provider == 'fake'; "
                "assert 'nyc-housing-tests-' in settings.artifact_storage_path"
            ),
        ],
        check=False,
        capture_output=True,
        cwd=os.getcwd(),
        env=environment,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
