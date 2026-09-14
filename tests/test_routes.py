import base64
import json
import os
import re

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.auth import upsert_discord_user
from app.database import SessionLocal, engine
import app.main as main_module
from app.main import app
from app.models import AuditLog, Category, Run, User, UserProfile, utcnow

def test_public_pages_render_with_current_starlette():
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Portal 2" in response.text
        assert '<meta property="og:title"' in response.text
        assert f'styles.css?v={main_module.CSS_VERSION}' in response.text
        css_response = client.get(f"/static/styles.css?v={main_module.CSS_VERSION}")
        assert css_response.status_code == 200

def test_about_page_lists_moderators_and_owner_only():
    with TestClient(app) as client:
        with SessionLocal() as db:
            db.add_all([
                User(discord_id="111111111111111111", username="about_owner"),
                User(discord_id="about-mod", username="about_moderator", is_moderator=True),
                User(discord_id="about-runner", username="about_regular_runner"),
            ])
            db.commit()
        response = client.get("/about")
        assert response.status_code == 200
        assert 'href="/about">About' in response.text
        assert 'href="/users/about-mod"' in response.text
        assert 'href="/users/111111111111111111"' in response.text
        assert "about_regular_runner" not in response.text
        assert '<meta property="og:title"' in response.text
        with SessionLocal() as db:
            for user in db.scalars(select(User).where(User.username.in_(
                ["about_owner", "about_moderator", "about_regular_runner"]
            ))):
                db.delete(user)
            db.commit()

def test_category_rules_are_rendered_from_markdown(monkeypatch, tmp_path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "category.md").write_text("TBD", encoding="utf-8")
    monkeypatch.setattr(main_module, "BASE_DIR", tmp_path / "app")
    monkeypatch.setattr(main_module, "RULES_DIR", rules_dir)

    with TestClient(app) as client:
        with SessionLocal() as db:
            db.add(
                Category(
                    slug="route-test-category",
                    name="Route Test Build - Route Test Category",
                    short_name="Route Test Category",
                    build_slug="route-test-build",
                    build_name="Route Test Build",
                    rules_file="rules/category.md",
                )
            )
            db.commit()

        response = client.get("/route-test-build/route-test-category/rules")
        assert response.status_code == 200
        assert "<p>TBD</p>" in response.text
        assert response.text.count("<h1") == 1

def test_missing_category_uses_html_error_template():
    with TestClient(app) as client:
        response = client.get("/no-such-build/no-such-category")
        assert response.status_code == 404
        assert "Category not found" in response.text
        legacy_response = client.get("/category/not-a-category")
        assert legacy_response.status_code == 404
        assert "Category not found" in legacy_response.text

def test_category_can_show_obsoleted_runs_without_changing_places():
    with TestClient(app) as client:
        with SessionLocal() as db:
            category = Category(
                slug="obsolete-test",
                name="Obsolete Test",
                build_slug="obsolete-build",
                build_name="Obsolete Build",
            )
            other_category = Category(
                slug="obsolete-other",
                name="Obsolete Other",
                build_slug="obsolete-build",
                build_name="Obsolete Build",
            )
            first = User(discord_id="obsolete-first", username="First")
            second = User(discord_id="obsolete-second", username="Second")
            db.add_all([category, other_category, first, second])
            db.flush()
            submitted_at = utcnow()
            runs = [
                Run(runner=first, category=category, time_ms=1000, status="approved"),
                Run(runner=first, category=category, time_ms=1000, status="approved"),
                Run(runner=first, category=category, time_ms=1500, status="approved"),
                Run(runner=second, category=category, time_ms=2000, status="approved"),
                Run(runner=first, category=category, time_ms=500, status="pending"),
                Run(runner=second, category=category, time_ms=600, status="rejected"),
                Run(runner=first, category=other_category, time_ms=700, status="approved"),
            ]
            for run in runs:
                run.video_url = "https://example.com/video"
                run.submitted_at = submitted_at
            db.add_all(runs)
            db.commit()
            ids = [run.id for run in runs]

        for query, expected, checked in [
            ("", [ids[0], ids[3]], False),
            ("?show_obsolete=true", ids[:4], True),
            ("?show_obsolete=false", [ids[0], ids[3]], False),
        ]:
            response = client.get(f"/obsolete-build/obsolete-test{query}")
            assert response.status_code == 200
            assert [int(id_) for id_ in re.findall(r'href="/runs/(\d+)"', response.text)] == expected
            assert ('aria-pressed="true"' in response.text) == checked
            rows = re.findall(r"<tr>(.*?)</tr>", response.text, re.S)
            for run_id, place in [(ids[0], "1st"), (ids[3], "2nd")]:
                row = next(row for row in rows if f'href="/runs/{run_id}"' in row)
                assert f"<td>{place}</td>" in row
            assert response.text.count('aria-label="Obsoleted run"') == (2 if checked else 0)

def test_empty_category_keeps_obsoleted_runs_option():
    with TestClient(app) as client:
        with SessionLocal() as db:
            db.add(
                Category(
                    slug="obsolete-empty",
                    name="Obsolete Empty",
                    build_slug="obsolete-build",
                    build_name="Obsolete Build",
                )
            )
            db.commit()
        response = client.get("/obsolete-build/obsolete-empty?show_obsolete=true")
        assert response.status_code == 200
        assert "No runs yet." in response.text
        assert 'aria-pressed="true"' in response.text

def test_legacy_slug_defaults_to_build_and_category_slug():
    assert (
        Category(slug="nme", build_slug="july09", legacy_slug="").effective_legacy_slug
        == "july09_nme"
    )
    assert (
        Category(
            slug="nme",
            build_slug="july09",
            legacy_slug="2009-no-major-exploits",
        ).effective_legacy_slug
        == "2009-no-major-exploits"
    )
    assert Category(slug="solo", build_slug="").effective_legacy_slug == "solo"

def test_legacy_category_urls_redirect_to_new_urls():
    with TestClient(app) as client:
        with SessionLocal() as db:
            db.add_all(
                [
                    Category(
                        slug="legacy-cat",
                        name="Legacy Test Build - Legacy Cat",
                        short_name="Legacy Cat",
                        build_slug="legacy-build",
                        build_name="Legacy Test Build",
                        legacy_slug="old-legacy-cat",
                        rules_file="rules/unused.md",
                    ),
                    Category(
                        slug="fallback-cat",
                        name="Legacy Test Build - Fallback Cat",
                        short_name="Fallback Cat",
                        build_slug="legacy-build",
                        build_name="Legacy Test Build",
                        legacy_slug="",
                        rules_file="rules/unused.md",
                    ),
                ]
            )
            db.commit()

        response = client.get("/category/old-legacy-cat", follow_redirects=False)
        assert response.status_code == 301
        assert response.headers["location"] == "/legacy-build/legacy-cat"

        query_response = client.get(
            "/category/old-legacy-cat?show_obsolete=true", follow_redirects=False
        )
        assert query_response.status_code == 301
        assert (
            query_response.headers["location"]
            == "/legacy-build/legacy-cat?show_obsolete=true"
        )

        rules_response = client.get(
            "/category/old-legacy-cat/rules", follow_redirects=False
        )
        assert rules_response.status_code == 301
        assert rules_response.headers["location"] == "/legacy-build/legacy-cat/rules"

        fallback_response = client.get(
            "/category/legacy-build_fallback-cat", follow_redirects=False
        )
        assert fallback_response.status_code == 301
        assert fallback_response.headers["location"] == "/legacy-build/fallback-cat"

        followed = client.get("/category/old-legacy-cat")
        assert followed.status_code == 200
        assert "Legacy Cat" in followed.text

        with SessionLocal() as db:
            for category in db.scalars(
                select(Category).where(Category.build_slug == "legacy-build")
            ):
                db.delete(category)
            db.commit()

def test_protected_page_uses_html_error_template_when_signed_out():
    with TestClient(app) as client:
        response = client.get("/submit")
        assert response.status_code == 401
        assert "Sign in with Discord" in response.text

def test_discord_signup_is_not_created_until_turnstile_passes(monkeypatch):
    discord_id = "222222222222222222"
    csrf_token = "first-login-csrf"
    oauth_state = "oauth-state"

    async def fake_exchange(_code):
        return {
            "id": discord_id,
            "username": "new_runner",
            "global_name": "New Runner",
            "avatar": None,
        }

    monkeypatch.setattr(main_module, "exchange_code_for_user", fake_exchange)
    monkeypatch.setattr(main_module, "turnstile_enabled", lambda: True)
    monkeypatch.setattr(
        main_module,
        "verify_turnstile",
        lambda token, action: token == "valid-token" and action == "discord_signup",
    )

    with TestClient(app) as client:
        session_data = base64.b64encode(
            json.dumps({"oauth_state": oauth_state, "csrf_token": csrf_token}).encode()
        )
        session_cookie = TimestampSigner("route-test-secret").sign(session_data).decode()
        client.cookies.set("p2runs_session", session_cookie)

        callback_response = client.get(
            f"/auth/callback?code=test-code&state={oauth_state}",
            follow_redirects=False,
        )
        assert callback_response.status_code == 303
        assert callback_response.headers["location"] == "/auth/verify"
        with SessionLocal() as db:
            assert db.scalar(select(User).where(User.discord_id == discord_id)) is None

        verify_page = client.get("/auth/verify")
        assert verify_page.status_code == 200
        assert 'data-action="discord_signup"' in verify_page.text

        failed_response = client.post(
            "/auth/verify",
            data={"csrf_token": csrf_token, "cf-turnstile-response": "bad-token"},
        )
        assert failed_response.status_code == 422
        with SessionLocal() as db:
            assert db.scalar(select(User).where(User.discord_id == discord_id)) is None

        success_response = client.post(
            "/auth/verify",
            data={"csrf_token": csrf_token, "cf-turnstile-response": "valid-token"},
            follow_redirects=False,
        )
        assert success_response.status_code == 303

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.discord_id == discord_id))
        assert user is not None
        assert user.display_name == "New Runner"
        assert user.last_login_at is not None

def test_regular_submission_requires_turnstile(monkeypatch):
    csrf_token = "submission-csrf"
    discord_id = "333333333333333333"
    monkeypatch.setattr(main_module, "turnstile_enabled", lambda: True)
    monkeypatch.setattr(
        main_module,
        "verify_turnstile",
        lambda token, action: token == "valid-token" and action == "submit_run",
    )

    with TestClient(app) as client:
        with SessionLocal() as db:
            user = User(
                discord_id=discord_id,
                username="submitter",
                last_login_at=utcnow(),
            )
            category = Category(
                slug="submission-captcha-test-category",
                name="CAPTCHA Test Build - Submission Category",
                short_name="Submission Category",
                build_slug="captcha-test-build",
                build_name="CAPTCHA Test Build",
                rules_file="rules/unused.md",
            )
            db.add_all([user, category])
            db.commit()
            db.refresh(user)
            user_id = user.id
            category_id = category.id

        session_data = base64.b64encode(
            json.dumps({"user_id": user_id, "csrf_token": csrf_token}).encode()
        )
        session_cookie = TimestampSigner("route-test-secret").sign(session_data).decode()
        client.cookies.set("p2runs_session", session_cookie)
        form_data = {
            "category_id": category_id,
            "run_time": "2:00.000",
            "video_url": "https://youtu.be/submission",
            "notes": "",
            "csrf_token": csrf_token,
        }

        failed_response = client.post(
            "/submit",
            data={**form_data, "cf-turnstile-response": "bad-token"},
        )
        assert failed_response.status_code == 422
        with SessionLocal() as db:
            assert db.scalar(select(Run).where(Run.user_id == user_id)) is None

        success_response = client.post(
            "/submit",
            data={**form_data, "cf-turnstile-response": "valid-token"},
            follow_redirects=False,
        )
        assert success_response.status_code == 303

    with SessionLocal() as db:
        run = db.scalar(select(Run).where(Run.user_id == user_id))
        assert run is not None
        assert run.status == "pending"

def test_user_can_edit_public_profile_with_preset_color():
    csrf_token = "profile-csrf"
    with TestClient(app) as client:
        with SessionLocal() as db:
            user = User(
                discord_id="444444444444444444",
                username="profile_runner",
                global_name="Profile Runner",
                is_moderator=True,
                last_login_at=utcnow(),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            user_id = user.id

        session_data = base64.b64encode(
            json.dumps({"user_id": user_id, "csrf_token": csrf_token}).encode()
        )
        session_cookie = TimestampSigner("route-test-secret").sign(session_data).decode()
        client.cookies.set("p2runs_session", session_cookie)

        own_page = client.get("/users/444444444444444444")
        assert 'href="/users/444444444444444444" title="My profile"' in own_page.text
        legacy_response = client.get("/me", follow_redirects=False)
        assert legacy_response.status_code == 303
        assert legacy_response.headers["location"] == "/users/444444444444444444"

        invalid_response = client.post(
            "/me/profile",
            data={
                "bio": "Runner bio",
                "background_color": "url(javascript:bad)",
                "csrf_token": csrf_token,
            },
        )
        assert invalid_response.status_code == 422

        response = client.post(
            "/me/profile",
            data={
                "bio": "Runner bio <script>alert(1)</script>",
                "background_color": "purple",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/users/444444444444444444"

        client.cookies.clear()
        public_response = client.get("/users/444444444444444444")
        assert public_response.status_code == 200
        assert '<html lang="en" class="profile-color-purple">' in public_response.text
        assert '<h2>About me</h2>' in public_response.text
        assert 'class="moderator-badge">Moderator' in public_response.text
        assert '<meta name="twitter:card" content="summary">' in public_response.text
        assert '<meta property="og:image"' in public_response.text
        assert client.get(f"/users/{user_id}").status_code == 404
        assert "Runner bio &lt;script&gt;alert(1)&lt;/script&gt;" in public_response.text
        assert '<meta property="og:title"' in public_response.text

    with SessionLocal() as db:
        profile = db.get(UserProfile, user_id)
        assert profile is not None
        assert profile.background_color == "purple"

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
            first_category = Category(
                slug="manual-run-test-category",
                name="Manual Test Build - First Category",
                short_name="First Category",
                build_slug="manual-test-build",
                build_name="Manual Test Build",
                display_order=1,
                rules_file="rules/unused.md",
            )
            second_category = Category(
                slug="edited-run-test-category",
                name="Manual Test Build - Second Category",
                short_name="Second Category",
                build_slug="manual-test-build",
                build_name="Manual Test Build",
                display_order=2,
                rules_file="rules/unused.md",
            )
            db.add_all([moderator, first_category, second_category])
            db.commit()
            db.refresh(moderator)
            moderator_id = moderator.id
            category_id = first_category.id
            edited_category_id = second_category.id

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
        run_id = int(response.headers["location"].rsplit("/", 1)[-1])

        edit_form_response = client.get(f"/moderation/runs/{run_id}/edit")
        assert edit_form_response.status_code == 200
        assert "Temporary Runner" in edit_form_response.text

        edit_response = client.post(
            f"/moderation/runs/{run_id}/edit",
            data={
                "discord_id": discord_id,
                "temporary_display_name": "Corrected Runner",
                "category_id": edited_category_id,
                "run_time": "1:20.000",
                "video_url": "https://youtu.be/corrected",
                "notes": "Corrected notes.",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert edit_response.status_code == 303

        forbidden_response = client.get("/owner/audit-log")
        assert forbidden_response.status_code == 403
        forbidden_download = client.post(
            "/owner/database/download",
            data={"csrf_token": csrf_token},
        )
        assert forbidden_download.status_code == 403

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
        assert "Edited" in audit_response.text
        assert discord_id in audit_response.text

        snapshot_response = client.post(
            "/owner/database/download",
            data={"csrf_token": csrf_token},
        )
        assert snapshot_response.status_code == 200
        assert snapshot_response.content.startswith(b"SQLite format 3\x00")
        assert "attachment;" in snapshot_response.headers["content-disposition"]

    with SessionLocal() as db:
        runner = db.scalar(select(User).where(User.discord_id == discord_id))
        assert runner is not None
        assert runner.display_name == "Corrected Runner"
        run = db.scalar(
            select(Run)
            .options(joinedload(Run.runner))
            .where(Run.user_id == runner.id)
        )
        assert run is not None
        assert run.status == "approved"
        assert run.reviewed_by_user_id == moderator_id
        assert run.category_id == edited_category_id
        assert run.time_ms == 80_000
        assert run.video_url == "https://youtu.be/corrected"
        assert run.notes == "Corrected notes."
        audit_entry = db.scalar(
            select(AuditLog).where(
                AuditLog.run_id == run.id,
                AuditLog.action == "added_manually",
            )
        )
        assert audit_entry is not None
        assert audit_entry.runner_name == "Temporary Runner"
        edited_entry = db.scalar(
            select(AuditLog).where(
                AuditLog.run_id == run.id,
                AuditLog.action == "edited",
            )
        )
        assert edited_entry is not None
        assert "Temporary runner name" in edited_entry.details
        assert "Category:" in edited_entry.details
        assert "Time:" in edited_entry.details

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
