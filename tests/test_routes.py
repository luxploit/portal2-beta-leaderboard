import os
import tempfile
from pathlib import Path

TEST_DB = Path(tempfile.gettempdir()) / "portal2_runs_route_tests.db"
TEST_DB.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["SESSION_SECRET"] = "route-test-secret"

from fastapi.testclient import TestClient

from app.database import engine
from app.main import app


def test_public_pages_render_with_current_starlette():
    with TestClient(app) as client:
        for path in (
            "/",
            "/category/2009-no-major-exploits",
            "/category/2009-oob-sla",
            "/category/2009-in-bounds-no-sla",
            "/category/2010-no-major-exploits",
            "/category/2010-oob-sla",
            "/category/2010-in-bounds-no-sla",
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            assert "text/html" in response.headers["content-type"]
            assert "Portal 2" in response.text


def test_home_groups_categories_by_build():
    with TestClient(app) as client:
        response = client.get("/")
        assert "July 2009 852_0" in response.text
        assert "February 2010 841_0" in response.text
        assert response.text.count("No Major Exploits") == 2


def test_category_rules_are_rendered_from_markdown():
    with TestClient(app) as client:
        response = client.get("/category/2009-no-major-exploits/rules")
        assert response.status_code == 200
        assert "<p>TBD</p>" in response.text
        assert response.text.count("<h1") == 1


def test_missing_category_uses_html_error_template():
    with TestClient(app) as client:
        response = client.get("/category/not-a-category")
        assert response.status_code == 404
        assert "Category not found" in response.text


def test_protected_page_uses_html_error_template_when_signed_out():
    with TestClient(app) as client:
        response = client.get("/submit")
        assert response.status_code == 401
        assert "Sign in with Discord" in response.text


def teardown_module():
    engine.dispose()
    TEST_DB.unlink(missing_ok=True)
