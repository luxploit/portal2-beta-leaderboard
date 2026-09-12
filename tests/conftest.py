"""Install test isolation before pytest imports any test modules."""
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

_test_directory = tempfile.TemporaryDirectory(prefix="p2bsr-tests-")
TEST_DB = Path(_test_directory.name) / "test.db"
TEST_DATABASE_URL = f"sqlite:///{TEST_DB.as_posix()}"

# Override real environment values before app/config.py can load .env.
os.environ.update({
    "DATABASE_URL": TEST_DATABASE_URL,
    "SESSION_SECRET": "route-test-secret",
    "OWNER_DISCORD_ID": "111111111111111111",
    "BASE_URL": "http://testserver",
    "DISCORD_CLIENT_ID": "",
    "DISCORD_CLIENT_SECRET": "",
    "DISCORD_MODERATION_WEBHOOK_URL": "",
    "TURNSTILE_SITE_KEY": "",
    "TURNSTILE_SECRET_KEY": "",
    "COOKIE_SECURE": "false",
})


def assert_test_database(url):
    if str(url) != TEST_DATABASE_URL:
        raise RuntimeError("Tests refused to connect to a non-test database.")


@event.listens_for(Engine, "do_connect")
def guard_database_connection(dialect, connection_record, connection_args, connection_params):
    # This runs before the DBAPI opens the file, including during collection.
    if dialect.name != "sqlite" or Path(connection_args[0]).resolve() != TEST_DB.resolve():
        raise RuntimeError("Tests refused to connect to a non-test database.")


@pytest.fixture(autouse=True)
def verify_app_database_is_isolated():
    from app.database import engine
    from app.config import settings
    assert_test_database(engine.url)
    assert_test_database(settings.database_url)


@event.listens_for(Engine, "before_cursor_execute")
def guard_database_query(connection, cursor, statement, parameters, context, executemany):
    # Also covers a connection opened before this conftest was loaded.
    assert_test_database(connection.engine.url)


def pytest_sessionfinish(session, exitstatus):
    from app.database import engine
    engine.dispose()
    _test_directory.cleanup()
