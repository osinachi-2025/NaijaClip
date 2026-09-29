from __future__ import annotations

from types import SimpleNamespace

from resend.exceptions import ResendError

import services.email_service as email_service


def test_resend_request_uses_configured_credentials_and_branded_content(monkeypatch):
    calls = []
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "test-api-key")
    monkeypatch.setattr(email_service, "MAIL_FROM_EMAIL", "sender@example.com")
    monkeypatch.setattr(email_service, "MAIL_FROM_NAME", "NaijaClip")

    def fake_send(params):
        calls.append(params)
        return SimpleNamespace(id="provider-message-1")

    monkeypatch.setattr(email_service.resend.Emails, "send", fake_send)
    result = email_service.send_verification_email(
        "google-user@gmail.com",
        "https://naijaclip.name.ng/auth/verify-email?token=sample-token",
    )

    assert result.success is True
    assert result.status_code == 200
    assert result.message_id == "provider-message-1"
    assert email_service.resend.api_key == "test-api-key"
    payload = calls[0]
    assert payload["from"] == "NaijaClip <sender@example.com>"
    assert payload["to"] == ["google-user@gmail.com"]
    assert "NaijaClip" in payload["html"]
    assert "Verify email" in payload["html"]
    assert "sample-token" in payload["text"]


def test_resend_failure_returns_status_and_redacts_api_key(monkeypatch, caplog):
    secret = "test-resend-secret"
    monkeypatch.setattr(email_service, "RESEND_API_KEY", secret)
    monkeypatch.setattr(email_service, "MAIL_FROM_EMAIL", "sender@example.com")
    monkeypatch.setattr(
        email_service.resend.Emails,
        "send",
        lambda _params: (_ for _ in ()).throw(ResendError(401, "invalid_api_key", f"Invalid API key {secret}", "Check API key")),
    )

    result = email_service.send_email(
        "person@example.com",
        "Test",
        "<p>Body</p>",
        "Body",
        email_type="test",
    )

    assert result.success is False
    assert result.status_code == 401
    assert result.error == "Invalid API key [redacted]"
    assert secret not in caplog.text


def test_resend_client_error_returns_failure(monkeypatch, caplog):
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "test-api-key")
    monkeypatch.setattr(email_service, "MAIL_FROM_EMAIL", "sender@example.com")
    monkeypatch.setattr(
        email_service.resend.Emails,
        "send",
        lambda _params: (_ for _ in ()).throw(ValueError("invalid request")),
    )

    result = email_service.send_email("person@example.com", "Test", "html", "text", email_type="test")

    assert result.success is False
    assert result.status_code is None
    assert result.error == "Resend request failed: ValueError"
    assert "invalid request" not in caplog.text


def test_google_account_email_uses_the_same_transactional_delivery_path(monkeypatch):
    seen = []
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "test-api-key")
    monkeypatch.setattr(email_service, "MAIL_FROM_EMAIL", "sender@example.com")
    monkeypatch.setattr(
        email_service.resend.Emails,
        "send",
        lambda params: (seen.append(params) or SimpleNamespace(id="welcome-1")),
    )

    result = email_service.send_welcome_email("google-user@gmail.com", "Google User")

    assert result.success is True
    assert seen[0]["to"] == ["google-user@gmail.com"]
    assert seen[0]["subject"] == "Welcome to NaijaClip"


def test_password_reset_email_uses_dynamic_code_and_safety_instructions(monkeypatch):
    sent = []
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "test-api-key")
    monkeypatch.setattr(email_service, "MAIL_FROM_EMAIL", "sender@example.com")
    monkeypatch.setattr(
        email_service.resend.Emails,
        "send",
        lambda params: (sent.append(params) or SimpleNamespace(id="reset-1")),
    )

    result = email_service.send_password_reset_email("person@example.com", "864209")

    assert result.success is True
    payload = sent[0]
    assert payload["subject"] == "Your NaijaClip password reset code"
    assert "864209" in payload["html"]
    assert "864209" in payload["text"]
    assert "expires in 10 minutes" in payload["text"]
    assert "Do not share" in payload["html"]
    assert "123456" not in payload["html"]
    assert "/verify-reset-code" in payload["text"]


def test_missing_resend_configuration_returns_failure_without_request(monkeypatch):
    monkeypatch.setattr(email_service, "RESEND_API_KEY", None)
    monkeypatch.setattr(email_service, "MAIL_FROM_EMAIL", None)
    monkeypatch.setattr(
        email_service.resend.Emails,
        "send",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    result = email_service.send_email("person@example.com", "Test", "html", "text", email_type="test")

    assert result.success is False
    assert result.status_code is None
    assert "not configured" in result.error