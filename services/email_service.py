from __future__ import annotations

import html
import logging
from dataclasses import dataclass

import resend
from resend.exceptions import ResendError

from core.config import FRONTEND_URL, MAIL_FROM_EMAIL, MAIL_FROM_NAME, RESEND_API_KEY


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailDeliveryResult:
    success: bool
    status_code: int | None = None
    message_id: str | None = None
    error: str | None = None

    def __bool__(self) -> bool:
        return self.success


def send_email(
    to_email: str,
    subject: str,
    html_content: str,
    text_content: str,
    *,
    email_type: str,
) -> EmailDeliveryResult:
    if not RESEND_API_KEY or not MAIL_FROM_EMAIL:
        missing = ",".join(
            name for name, value in (
                ("RESEND_API_KEY", RESEND_API_KEY),
                ("MAIL_FROM_EMAIL", MAIL_FROM_EMAIL),
            ) if not value
        )
        logger.error(
            "Resend is not configured type=%s recipient=%s missing=%s",
            email_type,
            to_email,
            missing,
        )
        return EmailDeliveryResult(False, error="Email delivery is not configured")

    resend.api_key = RESEND_API_KEY
    sender = f"{MAIL_FROM_NAME} <{MAIL_FROM_EMAIL}>" if MAIL_FROM_NAME else MAIL_FROM_EMAIL
    try:
        response = resend.Emails.send({
            "from": sender,
            "to": [to_email],
            "subject": subject,
            "html": html_content,
            "text": text_content,
        })
    except ResendError as error:
        provider_error = str(error.message or "Resend rejected the email")[:500]
        if RESEND_API_KEY:
            provider_error = provider_error.replace(RESEND_API_KEY, "[redacted]")
        try:
            status_code = int(error.code)
        except (TypeError, ValueError):
            status_code = None
        logger.error(
            "Resend rejected message type=%s recipient=%s status=%s error_type=%s message=%s",
            email_type,
            to_email,
            status_code,
            error.error_type,
            provider_error,
        )
        return EmailDeliveryResult(False, status_code, error=provider_error)
    except Exception as error:
        logger.error(
            "Resend request failed type=%s recipient=%s error_type=%s",
            email_type,
            to_email,
            type(error).__name__,
        )
        return EmailDeliveryResult(False, error=f"Resend request failed: {type(error).__name__}")

    message_id = getattr(response, "id", None)
    logger.info(
        "Email accepted by Resend type=%s recipient=%s message_id=%s",
        email_type,
        to_email,
        message_id or "unavailable",
    )
    return EmailDeliveryResult(True, 200, str(message_id) if message_id else None)


def _branded_email(
    *,
    title: str,
    explanation: str,
    message_html: str,
    action_label: str,
    action_url: str,
    supporting_text: str,
) -> str:
    safe_title = html.escape(title)
    safe_explanation = html.escape(explanation)
    safe_action = html.escape(action_label)
    safe_url = html.escape(action_url, quote=True)
    safe_support = html.escape(supporting_text)
    return f"""<!doctype html>
<html lang="en">
  <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
  <body style="margin:0;padding:24px 12px;background:#f4f6f5;color:#182321;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;margin:0 auto;background:#ffffff;border:1px solid #dce4e1;border-radius:8px;">
      <tr><td style="padding:24px 28px;border-bottom:1px solid #e5ebe8;">
        <span style="font-size:20px;font-weight:700;color:#123f38;">NaijaClip</span>
      </td></tr>
      <tr><td style="padding:28px;">
        <h1 style="margin:0 0 12px;font-size:24px;line-height:1.25;color:#182321;">{safe_title}</h1>
        <p style="margin:0 0 18px;font-size:15px;line-height:1.6;color:#46534f;">{safe_explanation}</p>
        <div style="font-size:15px;line-height:1.6;color:#182321;">{message_html}</div>
        <p style="margin:24px 0;">
          <a href="{safe_url}" style="display:inline-block;padding:13px 20px;background:#e95f3c;color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;border-radius:5px;">{safe_action}</a>
        </p>
        <p style="margin:0 0 8px;font-size:13px;line-height:1.6;color:#596660;">{safe_support}</p>
        <p style="margin:0;font-size:12px;line-height:1.6;color:#738079;">If the button does not work, open this link:<br><a href="{safe_url}" style="color:#16685b;word-break:break-all;">{safe_url}</a></p>
      </td></tr>
      <tr><td style="padding:18px 28px;border-top:1px solid #e5ebe8;font-size:12px;line-height:1.5;color:#738079;">
        NaijaClip · This is an account-related message. Please do not reply to this automated email.
      </td></tr>
    </table>
  </body>
</html>"""


def send_welcome_email(user_email: str, first_name: str | None = None) -> EmailDeliveryResult:
    name = first_name or "there"
    url = build_frontend_url("/dashboard")
    body = f"<p style=\"margin:0;\">Hello {html.escape(name)}, your NaijaClip account is verified and ready.</p>"
    return send_email(
        user_email,
        "Welcome to NaijaClip",
        _branded_email(
            title="Welcome to NaijaClip",
            explanation="Your email address has been verified and your account is ready.",
            message_html=body,
            action_label="Open your dashboard",
            action_url=url,
            supporting_text="You can sign in any time to manage your videos and clips.",
        ),
        f"Hello {name}, your NaijaClip account is ready. Open your dashboard: {url}",
        email_type="welcome",
    )


def send_verification_email(user_email: str, verification_url: str) -> EmailDeliveryResult:
    absolute_url = verification_url if verification_url.startswith(("http://", "https://")) else build_frontend_url(verification_url)
    return send_email(
        user_email,
        "Verify your NaijaClip email",
        _branded_email(
            title="Verify your email",
            explanation="You received this message because an account was created with this email address.",
            message_html="<p style=\"margin:0;\">Confirm your email address to finish setting up your NaijaClip account.</p>",
            action_label="Verify email",
            action_url=absolute_url,
            supporting_text="If you did not create this account, you can ignore this message.",
        ),
        f"Confirm your NaijaClip email: {absolute_url}\nIf you did not create this account, ignore this message.",
        email_type="verification",
    )


def send_password_reset_email(user_email: str, confirmation_code: str) -> EmailDeliveryResult:
    code = html.escape(confirmation_code)
    reset_url = build_frontend_url("/verify-reset-code")
    return send_email(
        user_email,
        "Your NaijaClip password reset code",
        _branded_email(
            title="Reset your password",
            explanation="You received this message because a password reset was requested for your NaijaClip account.",
            message_html=f"<p style=\"margin:0;\">Enter this code on the reset screen:</p><p style=\"margin:12px 0;font-size:26px;font-weight:700;letter-spacing:3px;color:#123f38;\">{code}</p><p style=\"margin:0;\">This code expires in 10 minutes. Do not share it with anyone.</p>",
            action_label="Enter verification code",
            action_url=reset_url,
            supporting_text="If you did not request a password reset, you can ignore this message.",
        ),
        f"Your NaijaClip password reset code is {confirmation_code}. It expires in 10 minutes. Enter it here: {reset_url}. Do not share this code.",
        email_type="password_reset",
    )


def send_subscription_approval_email(user_email: str, approved: bool) -> EmailDeliveryResult:
    status_text = "approved" if approved else "not approved"
    dashboard_url = build_frontend_url("/dashboard?page=dashboard-billing")
    return send_email(
        user_email,
        f"NaijaClip subscription request {status_text}",
        _branded_email(
            title=f"Subscription request {status_text}",
            explanation="There is an update about your NaijaClip subscription request.",
            message_html=f"<p style=\"margin:0;\">Your subscription request was {html.escape(status_text)}.</p>",
            action_label="View billing",
            action_url=dashboard_url,
            supporting_text="You can review your current plan and billing status in your account.",
        ),
        f"Your NaijaClip subscription request was {status_text}. View billing: {dashboard_url}",
        email_type="subscription",
    )


def build_frontend_url(path: str) -> str:
    return f"{FRONTEND_URL.rstrip('/')}{path}" if path.startswith("/") else f"{FRONTEND_URL.rstrip('/')}/{path}"