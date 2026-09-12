import base64
import json
import os
import tempfile
from pathlib import Path

TEST_DB = Path(tempfile.gettempdir()) / "portal2_runs_route_tests.db"
TEST_DB.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["SESSION_SECRET"] = "route-test-secret"
os.environ["OWNER_DISCORD_ID"] = "111111111111111111"

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.auth import upsert_discord_user
from app.database import SessionLocal, engine
from app.main import app
from app.models import AuditLog, Category, Run, User


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


def test_moderator_can_add_run_for_placeholder_discord_user():
    csrf_token = "test-csrf-token"
    discord_id = "123456789012345678"

    with TestClient(app) as client:
        with SessionLocal() as db:
            moderator = User(
                discord_id="987654321098765432",
                username="Moderator",
                is_moderator=True,
            )
            db.add(moderator)
            db.commit()
            db.refresh(moderator)
            moderator_id = moderator.id
            category_id = db.scalar(
                select(Category.id).where(Category.slug == "2009-no-major-exploits")
            )

        session_data = base64.b64encode(
            json.dumps({"user_id": moderator_id, "csrf_token": csrf_token}).encode()
        )
        session_cookie = TimestampSigner("route-test-secret").sign(session_data).decode()
        client.cookies.set("p2runs_session", session_cookie)

        moderation_response = client.get("/moderation")
        assert 'href="/moderation/runs/add"' in moderation_response.text
        assert 'name="temporary_display_name"' not in moderation_response.text

        form_response = client.get("/moderation/runs/add")
        assert form_response.status_code == 200
        assert 'name="temporary_display_name"' in form_response.text

        response = client.post(
            "/moderation/runs/add",
            data={
                "discord_id": discord_id,
                "temporary_display_name": "Temporary Runner",
                "category_id": category_id,
                "run_time": "1:23.456",
                "video_url": "https://youtu.be/example",
                "notes": "Imported by a moderator.",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        forbidden_response = client.get("/owner/audit-log")
        assert forbidden_response.status_code == 403

        with SessionLocal() as db:
            owner = User(discord_id=os.environ["OWNER_DISCORD_ID"], username="Owner")
            db.add(owner)
            db.commit()
            db.refresh(owner)
            owner_id = owner.id

        owner_session = base64.b64encode(
            json.dumps({"user_id": owner_id, "csrf_token": csrf_token}).encode()
        )
        owner_cookie = TimestampSigner("route-test-secret").sign(owner_session).decode()
        client.cookies.clear()
        client.cookies.set("p2runs_session", owner_cookie)
        audit_response = client.get("/owner/audit-log")
        assert audit_response.status_code == 200
        assert "Added Manually" in audit_response.text
        assert discord_id in audit_response.text

    with SessionLocal() as db:
        runner = db.scalar(select(User).where(User.discord_id == discord_id))
        assert runner is not None
        assert runner.display_name == "Temporary Runner"
        run = db.scalar(
            select(Run)
            .options(joinedload(Run.runner))
            .where(Run.user_id == runner.id)
        )
        assert run is not None
        assert run.status == "approved"
        assert run.reviewed_by_user_id == moderator_id
        audit_entry = db.scalar(
            select(AuditLog).where(
                AuditLog.run_id == run.id,
                AuditLog.action == "added_manually",
            )
        )
        assert audit_entry is not None
        assert audit_entry.runner_name == "Temporary Runner"

        upsert_discord_user(
            db,
            {
                "id": discord_id,
                "username": "current_username",
                "global_name": "Current Discord Name",
                "avatar": None,
            },
        )
        assert db.scalar(select(func.count(User.id)).where(User.discord_id == discord_id)) == 1
        db.refresh(run)
        assert run.runner.display_name == "Current Discord Name"


def teardown_module():
    engine.dispose()
    TEST_DB.unlink(missing_ok=True)
