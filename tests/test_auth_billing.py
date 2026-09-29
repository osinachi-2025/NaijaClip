from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app as web_app
import database
import models
import routers.auth as auth_router_module
import services.paystack_service as paystack_service
from services.auth_service import authenticate_user, hash_password, link_google_account, register_user
from services.email_service import EmailDeliveryResult
from services.paystack_service import approve_subscription, initialize_payment, process_paystack_payment


def test_google_oauth_callback_api_alias_is_registered():
    response = TestClient(web_app.app).get("/api/auth/google/callback")

    assert response.status_code == 400
    assert response.json()["detail"] == "Missing OAuth code"


def _reset_db(tmp_path, monkeypatch):
    db_path = tmp_path / "auth.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setattr(database, "DATABASE_URL", db_url)
    monkeypatch.setenv("ADMIN_EMAIL", "test-admin@example.test")
    monkeypatch.setenv("ADMIN_PASSWORD", "TestAdminPassword123!")
    monkeypatch.setenv("ADMIN_NAME", "NaijaClip Test Admin")
    engine = create_engine(db_url, future=True)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False))
    return engine


def test_admin_created_on_first_database_initialization(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    monkeypatch.setenv("ADMIN_EMAIL", "admin@naijaclip.dev")
    monkeypatch.setenv("ADMIN_PASSWORD", "StrongPassword123!")
    monkeypatch.setenv("ADMIN_NAME", "System Admin")

    database.initialize_database()

    with database.SessionLocal() as db:
        user = db.query(models.User).filter(models.User.role == models.UserRole.ADMIN).first()
        assert user is not None
        assert user.email == "admin@naijaclip.dev"
        assert user.first_name == "System Admin"
        assert user.is_verified is True
        assert user.is_active is True
        assert authenticate_user(user.email, "StrongPassword123!") is not None


def test_admin_bootstrap_is_idempotent_and_never_resets_existing_password(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    with database.SessionLocal() as db:
        admin = db.query(models.User).filter(models.User.role == models.UserRole.ADMIN).one()
        admin_id = admin.id
        original_hash = admin.password_hash

    monkeypatch.setenv("ADMIN_PASSWORD", "ChangedAdminPassword456!")
    database.initialize_database()

    with database.SessionLocal() as db:
        admins = db.query(models.User).filter(models.User.role == models.UserRole.ADMIN).all()
        assert len(admins) == 1
        assert admins[0].id == admin_id
        assert admins[0].password_hash == original_hash
    assert authenticate_user("test-admin@example.test", "TestAdminPassword123!") is not None
    assert authenticate_user("test-admin@example.test", "ChangedAdminPassword456!") is None


def test_admin_bootstrap_leaves_existing_users_and_existing_admin_untouched(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.Base.metadata.create_all(database.engine)
    existing_hash = hash_password("ExistingPassword123!")
    with database.SessionLocal() as db:
        user = models.User(
            email="existing-user@example.test",
            password_hash=existing_hash,
            first_name="Existing User",
            role=models.UserRole.USER,
        )
        admin = models.User(
            email="already-admin@example.test",
            password_hash=hash_password("OriginalAdminPassword123!"),
            first_name="Original Admin",
            role=models.UserRole.ADMIN,
        )
        db.add_all([user, admin])
        db.commit()
        user_id, admin_id = user.id, admin.id
        admin_hash = admin.password_hash

    monkeypatch.setenv("ADMIN_EMAIL", "new-configured-admin@example.test")
    monkeypatch.setenv("ADMIN_PASSWORD", "AnotherAdminPassword456!")
    database.initialize_database()

    with database.SessionLocal() as db:
        user = db.get(models.User, user_id)
        admin = db.get(models.User, admin_id)
        assert user is not None and user.role == models.UserRole.USER and user.password_hash == existing_hash
        assert admin is not None and admin.role == models.UserRole.ADMIN and admin.password_hash == admin_hash
        assert db.query(models.User).filter(models.User.role == models.UserRole.ADMIN).count() == 1


def test_admin_bootstrap_refuses_to_change_existing_non_admin_email(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.Base.metadata.create_all(database.engine)
    with database.SessionLocal() as db:
        user = models.User(
            email="configured-admin@example.test",
            password_hash=hash_password("ExistingPassword123!"),
            role=models.UserRole.USER,
        )
        db.add(user)
        db.commit()

    monkeypatch.setenv("ADMIN_EMAIL", "configured-admin@example.test")
    with pytest.raises(RuntimeError, match="already belongs to a non-admin user"):
        database.initialize_database()

    with database.SessionLocal() as db:
        user = db.query(models.User).filter_by(email="configured-admin@example.test").one()
        assert user.role == models.UserRole.USER
        assert db.query(models.User).filter(models.User.role == models.UserRole.ADMIN).count() == 0


@pytest.mark.parametrize(
    ("email", "password", "missing_setting"),
    [("", "StrongPassword123!", "ADMIN_EMAIL"), ("admin@example.test", "", "ADMIN_PASSWORD")],
)
def test_admin_bootstrap_requires_email_and_password(tmp_path, monkeypatch, email, password, missing_setting):
    _reset_db(tmp_path, monkeypatch)
    monkeypatch.setenv("ADMIN_EMAIL", email)
    monkeypatch.setenv("ADMIN_PASSWORD", password)

    with pytest.raises(RuntimeError, match=missing_setting):
        database.initialize_database()


def test_registration_and_login_works(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()

    user = register_user(
        email="user@example.com",
        password="StrongPassword123!",
        first_name="Jane",
        last_name="Doe",
    )
    assert user.email == "user@example.com"
    assert user.role == models.UserRole.USER
    assert user.id
    assert user.is_active is True
    assert user.plan == models.PlanCode.FREE

    auth_user = authenticate_user("user@example.com", "StrongPassword123!")
    assert auth_user is not None
    assert auth_user.email == "user@example.com"


def test_test_mode_payment_keeps_subscription_pending(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()


def test_cookie_session_survives_home_navigation_refresh_and_logout(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    monkeypatch.setattr(auth_router_module, "AUTH_COOKIE_SECURE", False)
    user = models.User(
        email="cookie-user@example.com",
        password_hash=hash_password("StrongPassword123!"),
        is_active=True,
        is_verified=True,
    )
    with database.SessionLocal() as db:
        db.add(user)
        db.commit()

    with TestClient(web_app.app, base_url="http://testserver") as client:
        public_home = client.get("/", follow_redirects=False)
        assert public_home.status_code == 200

        login = client.post("/auth/login", json={"email": user.email, "password": "StrongPassword123!"})
        assert login.status_code == 200
        cookies = login.headers.get_list("set-cookie")
        assert any("naijaclip_access=" in cookie and "httponly" in cookie.lower() for cookie in cookies)
        assert any("naijaclip_refresh=" in cookie and "max-age=" in cookie.lower() for cookie in cookies)

        assert client.get("/users/me").json()["email"] == user.email
        home_page = client.get("/", follow_redirects=False)
        assert home_page.status_code == 200
        assert 'src="/static/js-home/init.js"' in home_page.text
        assert client.get("/dashboard").status_code == 200
        assert client.get("/dashboard?page=dashboard-overview").status_code == 200

        client.cookies.set("naijaclip_access", "expired-access-cookie")
        refreshed_profile = client.get("/users/me")
        assert refreshed_profile.status_code == 200
        assert any("naijaclip_access=" in cookie for cookie in refreshed_profile.headers.get_list("set-cookie"))

        logout = client.post("/auth/logout")
        assert logout.status_code == 200
        assert client.get("/users/me").status_code == 401
        assert client.get("/", follow_redirects=False).status_code == 200
        assert client.get("/login").status_code == 200
        assert client.get("/dashboard", follow_redirects=False).headers["location"] == "/login"


def test_google_authorization_uses_exact_configured_callback_uri(monkeypatch):
    callback_uri = "https://naijaclip.name.ng/api/auth/google/callback"
    monkeypatch.setattr(auth_router_module, "GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(auth_router_module, "GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(auth_router_module, "GOOGLE_REDIRECT_URI", callback_uri)
    monkeypatch.setattr(auth_router_module, "AUTH_COOKIE_SECURE", False)

    response = TestClient(web_app.app).get("/auth/google", follow_redirects=False)

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["redirect_uri"] == [callback_uri]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email profile"]
    assert query["state"]
    assert any("naijaclip_google_state=" in cookie and "httponly" in cookie.lower() for cookie in response.headers.get_list("set-cookie"))


def test_google_callback_exchanges_code_links_user_and_sets_session_cookies(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    callback_uri = "http://localhost:8000/api/auth/google/callback"
    monkeypatch.setattr(auth_router_module, "GOOGLE_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(auth_router_module, "GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(auth_router_module, "GOOGLE_REDIRECT_URI", callback_uri)
    monkeypatch.setattr(auth_router_module, "AUTH_COOKIE_SECURE", False)
    exchanged = {}

    class FakeResponse:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    def fake_post(_url, *, data, timeout):
        exchanged["redirect_uri"] = data["redirect_uri"]
        exchanged["grant_type"] = data["grant_type"]
        exchanged["code"] = data["code"]
        return FakeResponse({"access_token": "google-access-token"})

    def fake_get(_url, *, headers, timeout):
        exchanged["authorization"] = headers["Authorization"]
        return FakeResponse({
            "sub": "google-subject-1",
            "email": "google-person@gmail.com",
            "email_verified": True,
            "given_name": "Google",
            "family_name": "Person",
        })

    monkeypatch.setattr(auth_router_module.httpx, "post", fake_post)
    monkeypatch.setattr(auth_router_module.httpx, "get", fake_get)

    with TestClient(web_app.app, base_url="http://testserver") as client:
        start = client.get("/auth/google", follow_redirects=False)
        state = client.cookies.get(auth_router_module.GOOGLE_STATE_COOKIE_NAME)
        callback = client.get(
            "/api/auth/google/callback",
            params={"code": "authorization-code", "state": state},
            follow_redirects=False,
        )

        assert callback.status_code == 303
        assert callback.headers["location"] == "/dashboard"
        assert "token=" not in callback.headers["location"]
        assert any("naijaclip_access=" in cookie and "httponly" in cookie.lower() for cookie in callback.headers.get_list("set-cookie"))
        assert client.get("/users/me").json()["email"] == "google-person@gmail.com"
        assert client.get("/", follow_redirects=False).status_code == 200
        assert client.post("/auth/logout").status_code == 200
        assert client.get("/users/me").status_code == 401

    assert exchanged == {
        "redirect_uri": callback_uri,
        "grant_type": "authorization_code",
        "code": "authorization-code",
        "authorization": "Bearer google-access-token",
    }
    with database.SessionLocal() as db:
        user = db.query(models.User).filter_by(email="google-person@gmail.com").one()
        assert user.google_id == "google-subject-1"
        assert user.auth_provider == "google"
        assert user.email_verified is True


def test_google_identity_links_to_existing_local_account_without_duplicate(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    local_user = register_user("same@example.com", "StrongPassword123!", "Local", "User")

    linked_user = link_google_account("google-subject-2", "SAME@example.com", "Google", "User")

    assert linked_user.id == local_user.id
    assert linked_user.auth_provider == "local"
    assert linked_user.google_id == "google-subject-2"
    assert linked_user.email_verified is True
    assert authenticate_user("same@example.com", "StrongPassword123!") is not None
    with database.SessionLocal() as db:
        assert db.query(models.User).filter_by(email="same@example.com").count() == 1


def test_google_only_user_gets_google_signin_guidance_instead_of_reset_email(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    link_google_account("google-subject-3", "google-only@gmail.com")
    monkeypatch.setattr(
        auth_router_module,
        "send_password_reset_email",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    with TestClient(web_app.app) as client:
        response = client.post("/auth/forgot-password", json={"email": "google-only@gmail.com"})

    assert response.status_code == 200
    assert "Google sign-in" in response.json()["detail"]


def test_registration_sends_real_verification_token_and_welcome_email(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    monkeypatch.setattr(auth_router_module, "send_verification_email", lambda email, url: (sent.__setitem__("verification", (email, url)) or EmailDeliveryResult(True, 201, "verify-1")))
    monkeypatch.setattr(auth_router_module, "send_welcome_email", lambda email, name: (sent.__setitem__("welcome", (email, name)) or EmailDeliveryResult(True, 201, "welcome-1")))
    sent = {}

    with TestClient(web_app.app) as client:
        registration = client.post("/auth/register", json={
            "email": "verify-me@example.com",
            "password": "StrongPassword123!",
            "first_name": "Verify",
            "last_name": "Me",
        })
        assert registration.status_code == 200
        assert registration.json()["email_delivery"]["sent"] is True
        verification_url = sent["verification"][1]
        token = parse_qs(urlparse(verification_url).query)["token"][0]

        verified = client.get("/auth/verify-email", params={"token": token}, follow_redirects=False)

    assert verified.status_code == 303
    assert verified.headers["location"] == "/login?notice=verified&welcome=sent"
    assert sent["welcome"] == ("verify-me@example.com", "Verify")
    with database.SessionLocal() as db:
        user = db.query(models.User).filter_by(email="verify-me@example.com").one()
        assert user.email_verified is True


def test_registration_reports_verification_provider_failure(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    monkeypatch.setattr(
        auth_router_module,
        "send_verification_email",
        lambda *_args, **_kwargs: EmailDeliveryResult(False, 401, error="Invalid API key"),
    )

    with TestClient(web_app.app) as client:
        response = client.post("/auth/register", json={
            "email": "delivery-failed@example.com",
            "password": "StrongPassword123!",
            "first_name": "Delivery",
            "last_name": "Failed",
        })

    assert response.status_code == 200
    assert response.json()["email_delivery"] == {"sent": False, "status_code": 401, "message_id": None}
    assert "could not be sent" in response.json()["detail"]


def test_password_reset_reports_provider_failure_without_provider_detail(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    register_user("reset-failed@example.com", "StrongPassword123!")
    monkeypatch.setattr(
        auth_router_module,
        "send_password_reset_email",
        lambda *_args, **_kwargs: EmailDeliveryResult(False, 401, error="Invalid API key"),
    )

    with TestClient(web_app.app) as client:
        response = client.post("/auth/forgot-password", json={"email": "reset-failed@example.com"})

    assert response.status_code == 200
    assert response.json()["detail"] == "If an account exists for this email, we've sent a verification code."


def test_forgot_password_has_generic_response_and_validates_email(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    user = register_user("generic-reset@example.com", "OriginalPassword123!")
    monkeypatch.setattr(auth_router_module, "send_password_reset_email", lambda *_args: EmailDeliveryResult(True))

    with TestClient(web_app.app) as client:
        invalid = client.post("/auth/forgot-password", json={"email": "not-an-email"})
        missing = client.post("/auth/forgot-password", json={"email": "missing@example.com"})
        existing = client.post("/auth/forgot-password", json={"email": user.email})

    assert invalid.status_code == 422
    assert missing.status_code == 200
    assert existing.status_code == 200
    assert missing.json() == existing.json()


def test_password_reset_complete_flow_uses_single_use_authorization_and_logs_out(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    monkeypatch.setattr(auth_router_module, "AUTH_COOKIE_SECURE", False)
    sent_codes = []
    monkeypatch.setattr(
        auth_router_module,
        "send_password_reset_email",
        lambda email, code: (sent_codes.append((email, code)) or EmailDeliveryResult(True, 201, "reset-1")),
    )
    user = register_user("reset-flow@example.com", "OriginalPassword123!")

    with TestClient(web_app.app, base_url="http://testserver") as client:
        assert client.get("/reset-password", follow_redirects=False).status_code == 303
        assert client.post("/auth/login", json={"email": user.email, "password": "OriginalPassword123!"}).status_code == 200
        forgot = client.post("/auth/forgot-password", json={"email": user.email})
        assert forgot.status_code == 200
        assert "verification code" in forgot.json()["detail"]
        assert sent_codes == [(user.email, sent_codes[0][1])]
        code = sent_codes[0][1]
        assert len(code) == 6 and code.isdigit()

        verified = client.post("/auth/verify-reset-code", json={"email": user.email, "code": code})
        assert verified.status_code == 200
        assert auth_router_module.AUTH_RESET_COOKIE_NAME in client.cookies
        assert code not in verified.text
        reset_page = client.get("/reset-password")
        assert reset_page.status_code == 200
        assert 'id="new-password"' in reset_page.text
        assert 'id="confirm-password"' in reset_page.text
        assert "The passwords do not match." in client.get("/static/js/password-reset.js").text
        assert client.post("/auth/verify-reset-code", json={"email": user.email, "code": code}).status_code == 400

        changed = client.post("/auth/reset-password", json={"new_password": "NewPassword456!"})
        assert changed.status_code == 200
        assert client.get("/users/me").status_code == 401
        assert client.post("/auth/reset-password", json={"new_password": "AnotherPassword789!"}).status_code == 400
        assert client.post("/auth/login", json={"email": user.email, "password": "OriginalPassword123!"}).status_code == 401
        assert client.post("/auth/login", json={"email": user.email, "password": "NewPassword456!"}).status_code == 200
        assert client.post("/auth/logout").status_code == 200
        assert client.get("/users/me").status_code == 401


def test_password_reset_code_attempt_limit_and_exact_digit_validation(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    user = register_user("reset-attempts@example.com", "OriginalPassword123!")
    monkeypatch.setattr(auth_router_module, "send_password_reset_email", lambda *_args: EmailDeliveryResult(True))

    with TestClient(web_app.app) as client:
        client.post("/auth/forgot-password", json={"email": user.email})
        malformed = client.post("/auth/verify-reset-code", json={"email": user.email, "code": "12345"})
        assert malformed.status_code == 422
        for attempt in range(5):
            result = client.post("/auth/verify-reset-code", json={"email": user.email, "code": "000000"})
        assert result.status_code == 400
        assert "Too many" in result.json()["detail"]
        assert client.post("/auth/verify-reset-code", json={"email": user.email, "code": "123456"}).status_code == 400


def test_password_reset_expired_code_and_resend_cooldown(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    user = register_user("reset-resend@example.com", "OriginalPassword123!")
    sent_codes = []
    monkeypatch.setattr(
        auth_router_module,
        "send_password_reset_email",
        lambda _email, code: (sent_codes.append(code) or EmailDeliveryResult(True)),
    )

    with TestClient(web_app.app) as client:
        client.post("/auth/forgot-password", json={"email": user.email})
        cooldown = client.post("/auth/resend-reset-code", json={"email": user.email})
        assert cooldown.status_code == 200
        assert len(sent_codes) == 1

        with database.SessionLocal() as db:
            token = db.query(models.PasswordResetToken).filter_by(user_id=user.id).one()
            token.created_at = datetime.now(timezone.utc) - timedelta(seconds=60)
            db.commit()

        resent = client.post("/auth/resend-reset-code", json={"email": user.email})
        assert resent.status_code == 200
        assert len(sent_codes) == 2
        assert sent_codes[0] != sent_codes[1]
        assert client.post("/auth/verify-reset-code", json={"email": user.email, "code": sent_codes[0]}).status_code == 400

        with database.SessionLocal() as db:
            token = db.query(models.PasswordResetToken).filter_by(user_id=user.id).one()
            token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()

        expired = client.post("/auth/verify-reset-code", json={"email": user.email, "code": sent_codes[1]})
        assert expired.status_code == 400
        assert "expired" in expired.json()["detail"].lower()
    monkeypatch.setenv("PAYSTACK_MODE", "test")
    monkeypatch.setenv("AUTO_ACTIVATE_PRO", "true")

    user = register_user(
        email="payer@example.com",
        password="StrongPassword123!",
        first_name="Pay",
        last_name="User",
    )
    payment = process_paystack_payment(
        user_id=user.id,
        amount=Decimal("2500"),
        currency="NGN",
        reference="ref-test-001",
        plan="pro",
    )

    assert payment.status == models.PaymentStatus.SUCCESS
    assert payment.payment_mode == "test"
    assert payment.user_id == user.id
    assert payment.subscription.status == models.SubscriptionStatus.PENDING
    assert payment.subscription.plan == models.PlanCode.PRO
    with database.SessionLocal() as db:
        persisted_user = db.get(models.User, user.id)
        assert persisted_user.plan == models.PlanCode.FREE
        assert persisted_user.subscription_status == models.SubscriptionStatus.INACTIVE.value


def test_test_mode_webhook_cannot_activate_pro_even_when_auto_activation_enabled(tmp_path, monkeypatch):
    _reset_db(tmp_path, monkeypatch)
    database.initialize_database()
    monkeypatch.setenv("PAYSTACK_MODE", "test")
    monkeypatch.setenv("AUTO_ACTIVATE_PRO", "true")
    monkeypatch.setattr(paystack_service, "verify_webhook_signature", lambda *_args: True)

    user = register_user("webhook@example.com", "StrongPassword123!")
    payment = initialize_payment(user.id, Decimal("2500"), "NGN", "pro", reference="ref-webhook-test")
    paystack_service.handle_paystack_webhook({
        "event": "charge.success",
        "data": {
            "reference": payment.reference,
            "amount": 250000,
            "currency": "NGN",
            "live": False,
            "id": 12345,
            "metadata": {"plan": "pro"},
        },
    }, "valid-test-signature")

    with database.SessionLocal() as db:
        persisted_user = db.get(models.User, user.id)
        persisted_payment = db.query(models.Payment).filter_by(reference=payment.reference).one()
        subscription_id = persisted_payment.subscription.id
        assert persisted_user.plan == models.PlanCode.FREE
        assert persisted_user.subscription_status == models.SubscriptionStatus.INACTIVE.value
        assert persisted_payment.subscription.status == models.SubscriptionStatus.PENDING

    with pytest.raises(ValueError, match="Test-mode payments cannot activate"):
        approve_subscription(subscription_id, admin_user_id="admin-1")

    with database.SessionLocal() as db:
        persisted_user = db.get(models.User, user.id)
        assert persisted_user.plan == models.PlanCode.FREE
