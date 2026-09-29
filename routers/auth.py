from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import database
import models
from core.config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    AUTH_ACCESS_COOKIE_NAME,
    AUTH_COOKIE_DOMAIN,
    AUTH_COOKIE_SAMESITE,
    AUTH_COOKIE_SECURE,
    AUTH_RESET_COOKIE_NAME,
    AUTH_REFRESH_COOKIE_NAME,
    FRONTEND_URL,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    REFRESH_TOKEN_EXPIRE_DAYS,
    RESET_AUTHORIZATION_EXPIRY_MINUTES,
    RESET_CODE_RESEND_COOLDOWN_SECONDS,
)
from database import get_db
from services.auth_service import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    create_email_verification_token,
    consume_reset_authorization,
    decode_token,
    ensure_initial_admin,
    get_user_by_email,
    is_reset_authorization_valid,
    issue_reset_code,
    link_google_account,
    register_user,
    verify_email_token,
    verify_password,
    verify_reset_code,
)
from services.email_service import (
    build_frontend_url,
    send_password_reset_email,
    send_verification_email,
    send_welcome_email,
)
from services.paystack_service import approve_subscription, initialize_payment, process_paystack_payment, reject_subscription
from services.plan_service import monthly_limit_for_plan

router = APIRouter()
security = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)
GOOGLE_STATE_COOKIE_NAME = "naijaclip_google_state"


def _set_access_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        AUTH_ACCESS_COOKIE_NAME,
        token,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        domain=AUTH_COOKIE_DOMAIN,
        path="/",
    )


def _set_auth_cookies(response: Response, user: models.User) -> tuple[str, str]:
    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)
    _set_access_cookie(response, access_token)
    response.set_cookie(
        AUTH_REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        domain=AUTH_COOKIE_DOMAIN,
        path="/",
    )
    return access_token, refresh_token


def _clear_auth_cookies(response: Response) -> None:
    for cookie_name in (AUTH_ACCESS_COOKIE_NAME, AUTH_REFRESH_COOKIE_NAME, GOOGLE_STATE_COOKIE_NAME):
        response.set_cookie(
            cookie_name,
            "",
            max_age=0,
            httponly=True,
            secure=AUTH_COOKIE_SECURE,
            samesite=AUTH_COOKIE_SAMESITE,
            domain=AUTH_COOKIE_DOMAIN,
            path="/",
        )


def _user_from_token(token: str, token_type: str, db: Session) -> models.User | None:
    payload = decode_token(token)
    if payload.get("type") != token_type:
        return None
    user = db.get(models.User, payload.get("sub"))
    if user is None or not user.is_active:
        return None
    return user


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    first_name: str | None = None
    last_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8)


class VerifyResetCodeRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class PaystackInitializeRequest(BaseModel):
    amount: Decimal
    currency: str = "NGN"
    plan: str = "pro"


class PaystackWebhookRequest(BaseModel):
    event: str
    data: dict[str, Any] = {}


class AdminActionRequest(BaseModel):
    reason: str | None = None


async def get_current_user(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> models.User:
    access_tokens = []
    if credentials and credentials.credentials:
        access_tokens.append(credentials.credentials)
    cookie_access = request.cookies.get(AUTH_ACCESS_COOKIE_NAME)
    if cookie_access and cookie_access not in access_tokens:
        access_tokens.append(cookie_access)

    for token in access_tokens:
        try:
            user = _user_from_token(token, "access", db)
        except Exception:
            continue
        if user is not None:
            if not request.cookies.get(AUTH_ACCESS_COOKIE_NAME) or not request.cookies.get(AUTH_REFRESH_COOKIE_NAME):
                _set_auth_cookies(response, user)
            return user

    refresh_token = request.cookies.get(AUTH_REFRESH_COOKIE_NAME)
    if refresh_token:
        try:
            user = _user_from_token(refresh_token, "refresh", db)
        except Exception:
            user = None
        if user is not None:
            _set_access_cookie(response, create_access_token(user))
            return user

    detail = "Invalid or expired token" if access_tokens or refresh_token else "Authentication required"
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def get_optional_current_user(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> models.User | None:
    try:
        return await get_current_user(request, response, credentials, db)
    except HTTPException as error:
        if error.status_code == status.HTTP_401_UNAUTHORIZED:
            return None
        raise


async def get_current_active_user(current_user: models.User = Depends(get_current_user)) -> models.User:
    if not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")
    return current_user


async def require_admin(current_user: models.User = Depends(get_current_active_user)) -> models.User:
    if current_user.role != models.UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/auth/register")
def register_user_route(payload: RegisterRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        user = register_user(payload.email, payload.password, payload.first_name, payload.last_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    raw_token = create_email_verification_token(user.id)
    verification_url = build_frontend_url(f"/auth/verify-email?token={raw_token}")
    delivery = send_verification_email(user.email, verification_url)
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "role": user.role.value,
            "plan": user.plan.value,
            "is_verified": user.email_verified,
        },
        "email_delivery": {
            "sent": delivery.success,
            "status_code": delivery.status_code,
            "message_id": delivery.message_id,
        },
        "detail": "Account created. Check your email to verify your address." if delivery.success else "Account created, but the verification email could not be sent. You can request another verification email later.",
    }


@router.post("/auth/resend-verification")
def resend_verification(payload: ForgotPasswordRequest) -> dict[str, Any]:
    user = get_user_by_email(payload.email)
    if user is None or user.email_verified:
        return {"detail": "If this account needs verification, a new verification email will be sent."}
    raw_token = create_email_verification_token(user.id)
    verification_url = build_frontend_url(f"/auth/verify-email?token={raw_token}")
    delivery = send_verification_email(user.email, verification_url)
    if not delivery.success:
        raise HTTPException(status_code=502, detail="We couldn't send the verification email. Please try again later.")
    return {
        "detail": "A new verification email has been sent.",
        "email_delivery": {"sent": True, "status_code": delivery.status_code, "message_id": delivery.message_id},
    }


@router.post("/auth/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    user = authenticate_user(payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    access_token, refresh_token = _set_auth_cookies(response, user)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/auth/refresh")
def refresh(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    token = payload.refresh_token if payload and payload.refresh_token else request.cookies.get(AUTH_REFRESH_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Refresh token required")
    try:
        user = _user_from_token(token, "refresh", db)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid refresh token") from exc
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    access_token, refresh_token = _set_auth_cookies(response, user)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/auth/logout")
def logout(response: Response) -> dict[str, str]:
    _clear_auth_cookies(response)
    return {"detail": "Logged out successfully"}


@router.get("/auth/google")
def google_login(response: Response) -> RedirectResponse:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REDIRECT_URI:
        raise HTTPException(status_code=501, detail="Google OAuth is not configured")
    state = secrets.token_urlsafe(32)
    logger.info("Google OAuth redirect URI: %s", GOOGLE_REDIRECT_URI)
    query = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    redirect = RedirectResponse(url=f"https://accounts.google.com/o/oauth2/v2/auth?{query}", status_code=302)
    redirect.set_cookie(
        GOOGLE_STATE_COOKIE_NAME,
        state,
        max_age=600,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
        domain=AUTH_COOKIE_DOMAIN,
        path="/",
    )
    return redirect


@router.get("/auth/google/callback")
@router.get("/api/auth/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    logger.info("Google OAuth callback route=%s redirect_uri=%s", request.url.path, GOOGLE_REDIRECT_URI)
    if error:
        logger.warning("Google OAuth returned an error callback error=%s", error[:100])
        raise HTTPException(status_code=401, detail="Google sign-in was cancelled or could not be completed.")
    if code is None:
        raise HTTPException(status_code=400, detail="Missing OAuth code")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REDIRECT_URI:
        raise HTTPException(status_code=501, detail="Google OAuth is not configured")
    expected_state = request.cookies.get(GOOGLE_STATE_COOKIE_NAME)
    if not expected_state or not state or not secrets.compare_digest(expected_state, state):
        logger.warning("Google OAuth state validation failed route=%s", request.url.path)
        raise HTTPException(status_code=400, detail="Google sign-in could not be verified. Please try again.")

    try:
        token_response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=15.0,
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        google_access_token = token_data.get("access_token")
        if not google_access_token:
            raise ValueError("Google token response did not include an access token")
        profile_response = httpx.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {google_access_token}"},
            timeout=15.0,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Google OAuth provider request failed route=%s status=%d", request.url.path, exc.response.status_code)
        raise HTTPException(status_code=502, detail="Google sign-in could not be completed. Please try again.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Google OAuth provider response was invalid route=%s error=%s", request.url.path, type(exc).__name__)
        raise HTTPException(status_code=502, detail="Google sign-in could not be completed. Please try again.") from exc

    google_subject = profile.get("sub")
    email = profile.get("email")
    email_verified = profile.get("email_verified") in (True, "true", "True", 1)
    if not google_subject or not email or not email_verified:
        logger.warning("Google OAuth profile missing verified identity fields route=%s", request.url.path)
        raise HTTPException(status_code=401, detail="Google did not provide a verified email address.")

    try:
        user = link_google_account(
            str(google_subject),
            str(email),
            profile.get("given_name"),
            profile.get("family_name"),
            profile.get("picture"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    redirect = RedirectResponse(url="/dashboard", status_code=303)
    _set_auth_cookies(redirect, user)
    redirect.delete_cookie(GOOGLE_STATE_COOKIE_NAME, domain=AUTH_COOKIE_DOMAIN, path="/")
    return redirect


@router.get("/auth/verify-email")
def verify_email(token: str = Query(...), db: Session = Depends(get_db)) -> RedirectResponse:
    user = verify_email_token(token)
    if user is None:
        raise HTTPException(status_code=400, detail="Verification link is invalid or expired")
    welcome_delivery = send_welcome_email(user.email, user.first_name)
    welcome_status = "sent" if welcome_delivery.success else "not-sent"
    return RedirectResponse(url=f"/login?notice=verified&welcome={welcome_status}", status_code=303)


@router.post("/auth/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = get_user_by_email(payload.email)
    if user is None:
        return {
            "detail": "If an account exists for this email, we've sent a verification code.",
            "resend_after_seconds": RESET_CODE_RESEND_COOLDOWN_SECONDS,
        }
    if user.auth_provider == "google":
        return {
            "detail": "This account uses Google sign-in. Continue with Google to access your account.",
            "google_signin": True,
        }
    code = issue_reset_code(user)
    if not code:
        return {
            "detail": "If an account exists for this email, we've sent a verification code.",
            "resend_after_seconds": RESET_CODE_RESEND_COOLDOWN_SECONDS,
        }
    delivery = send_password_reset_email(user.email, code)
    if not delivery.success:
        logger.error("Password reset email delivery failed recipient=%s status=%s", user.email, delivery.status_code)
    return {
        "detail": "If an account exists for this email, we've sent a verification code.",
        "resend_after_seconds": RESET_CODE_RESEND_COOLDOWN_SECONDS,
    }


@router.post("/auth/resend-reset-code")
def resend_reset_code(payload: ForgotPasswordRequest) -> dict[str, Any]:
    user = get_user_by_email(payload.email)
    generic_response = {
        "detail": "If an account exists for this email, we've sent a verification code.",
        "resend_after_seconds": RESET_CODE_RESEND_COOLDOWN_SECONDS,
    }
    if user is None:
        return generic_response
    if user.auth_provider == "google":
        return {
            "detail": "This account uses Google sign-in. Continue with Google to access your account.",
            "google_signin": True,
        }
    code = issue_reset_code(user)
    if not code:
        return generic_response
    delivery = send_password_reset_email(user.email, code)
    if not delivery.success:
        logger.error("Password reset resend failed recipient=%s status=%s", user.email, delivery.status_code)
    return generic_response


@router.post("/auth/verify-reset-code")
def verify_password_reset_code(payload: VerifyResetCodeRequest, response: Response) -> dict[str, str]:
    user = get_user_by_email(payload.email)
    if user is None or user.auth_provider == "google" or not user.is_active:
        raise HTTPException(status_code=400, detail="The verification code is invalid or expired.")
    raw_authorization, error = verify_reset_code(user, payload.code)
    if raw_authorization is None:
        detail = {
            "expired": "This verification code has expired. Request a new code.",
            "too_many_attempts": "Too many incorrect attempts. Request a new code.",
        }.get(error, "The verification code is incorrect.")
        raise HTTPException(status_code=400, detail=detail)
    response.set_cookie(
        AUTH_RESET_COOKIE_NAME,
        raw_authorization,
        max_age=RESET_AUTHORIZATION_EXPIRY_MINUTES * 60,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        domain=AUTH_COOKIE_DOMAIN,
        path="/",
    )
    return {"detail": "Code verified. You can now choose a new password."}


@router.post("/auth/reset-password")
def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    response: Response,
) -> dict[str, str]:
    authorization = request.cookies.get(AUTH_RESET_COOKIE_NAME)
    if not consume_reset_authorization(authorization, payload.new_password):
        raise HTTPException(status_code=400, detail="Reset authorization is invalid or expired. Verify your code again.")
    _clear_auth_cookies(response)
    response.delete_cookie(AUTH_RESET_COOKIE_NAME, domain=AUTH_COOKIE_DOMAIN, path="/")
    return {"detail": "Password reset successfully"}


@router.post("/auth/change-password")
def change_password(payload: ChangePasswordRequest, current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)) -> dict[str, str]:
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    current_user.password_hash = __import__("services.auth_service", fromlist=["hash_password"]).hash_password(payload.new_password)
    db.commit()
    return {"detail": "Password updated successfully"}


@router.get("/users/me")
def user_profile(current_user: models.User = Depends(get_current_active_user)) -> dict[str, Any]:
    return {
        "id": current_user.id,
        "email": current_user.email,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "role": current_user.role.value,
        "plan": current_user.plan.value,
        "subscription_status": current_user.subscription_status,
        "is_verified": current_user.email_verified,
        "videos_used": current_user.videos_used,
    }


@router.get("/billing")
def billing(current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {
        "current_plan": current_user.plan.value,
        "usage_limit": monthly_limit_for_plan(current_user.plan),
        "used_this_month": current_user.videos_used,
        "subscription_status": current_user.subscription_status,
        "subscription_expires_at": current_user.subscription_expires_at.isoformat() if current_user.subscription_expires_at else None,
        "payments": [
            {
                "id": payment.id,
                "amount": str(payment.amount),
                "currency": payment.currency,
                "status": payment.status.value,
                "payment_mode": payment.payment_mode,
                "reference": payment.reference,
            }
            for payment in db.query(models.Payment).filter(models.Payment.user_id == current_user.id).order_by(models.Payment.created_at.desc()).all()
        ],
    }


@router.get("/billing/payments")
def list_billing_payments(current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    payments = db.query(models.Payment).filter(models.Payment.user_id == current_user.id).order_by(models.Payment.created_at.desc()).all()
    return {"payments": [{"id": p.id, "reference": p.reference, "amount": str(p.amount), "status": p.status.value, "mode": p.payment_mode, "plan": p.plan} for p in payments]}


@router.get("/billing/subscription")
def billing_subscription(current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    subscription = db.query(models.Subscription).filter(models.Subscription.user_id == current_user.id).order_by(models.Subscription.created_at.desc()).first()
    return {
        "plan": current_user.plan.value,
        "status": current_user.subscription_status,
        "subscription_expires_at": current_user.subscription_expires_at.isoformat() if current_user.subscription_expires_at else None,
        "subscription": {
            "id": subscription.id,
            "status": subscription.status.value,
            "plan_id": subscription.plan_id,
            "reference": subscription.reference,
        } if subscription else None,
    }


@router.post("/payments/paystack/initialize")
def initialize_paystack_payment(payload: PaystackInitializeRequest, current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    payment = initialize_payment(current_user.id, payload.amount, payload.currency, payload.plan)
    return {
        "payment_id": payment.id,
        "reference": payment.reference,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "status": payment.status.value,
        "mode": payment.payment_mode,
        "authorization_url": f"{FRONTEND_URL}/billing?reference={payment.reference}",
    }


@router.get("/payments/paystack/callback")
def paystack_callback(reference: str | None = None, current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    if not reference:
        raise HTTPException(status_code=400, detail="Missing reference")
    payment = db.query(models.Payment).filter(models.Payment.reference == reference, models.Payment.user_id == current_user.id).first()
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.payment_mode == "test":
        return {"status": "successful", "subscription_status": "pending", "mode": "test", "reference": payment.reference}
    return {"status": payment.status.value, "reference": payment.reference}


@router.post("/payments/paystack/webhook")
def paystack_webhook(payload: PaystackWebhookRequest, signature: str | None = None) -> dict[str, Any]:
    if signature is None:
        raise HTTPException(status_code=401, detail="Missing signature")
    try:
        result = __import__("services.paystack_service", fromlist=["handle_paystack_webhook"]).handle_paystack_webhook(payload.model_dump(), signature)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.get("/admin")
def admin_dashboard(current_user: models.User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {
        "total_users": db.query(models.User).count(),
        "free_users": db.query(models.User).filter(models.User.plan == models.PlanCode.FREE).count(),
        "pro_users": db.query(models.User).filter(models.User.plan == models.PlanCode.PRO).count(),
        "pending_subscriptions": db.query(models.Subscription).filter(models.Subscription.status == models.SubscriptionStatus.PENDING).count(),
        "successful_payments": db.query(models.Payment).filter(models.Payment.status == models.PaymentStatus.SUCCESS).count(),
        "failed_payments": db.query(models.Payment).filter(models.Payment.status == models.PaymentStatus.FAILED).count(),
        "processing_jobs": db.query(models.ProcessingJob).count(),
        "failed_jobs": db.query(models.ProcessingJob).filter(models.ProcessingJob.status == models.JobStatus.FAILED).count(),
        "recent_registrations": db.query(models.User).order_by(models.User.created_at.desc()).limit(5).all(),
    }


@router.get("/admin/users")
def admin_users(current_user: models.User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    return {"users": [{"id": u.id, "email": u.email, "plan": u.plan.value, "role": u.role.value, "subscription_status": u.subscription_status, "is_active": u.is_active} for u in users]}


@router.get("/admin/payments")
def admin_payments(current_user: models.User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    payments = db.query(models.Payment).order_by(models.Payment.created_at.desc()).all()
    return {"payments": [{"id": p.id, "user_id": p.user_id, "reference": p.reference, "amount": str(p.amount), "status": p.status.value, "mode": p.payment_mode, "plan": p.plan} for p in payments]}


@router.get("/admin/subscriptions")
def admin_subscriptions(current_user: models.User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    subscriptions = db.query(models.Subscription).order_by(models.Subscription.created_at.desc()).all()
    return {"subscriptions": [{"id": s.id, "user_id": s.user_id, "plan_id": s.plan_id, "status": s.status.value, "reference": s.reference, "payment_id": s.payment_id} for s in subscriptions]}


@router.post("/admin/subscriptions/{subscription_id}/approve")
def approve_test_subscription(subscription_id: str, _: AdminActionRequest | None = None, current_user: models.User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        subscription = approve_subscription(subscription_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"detail": "Subscription approved", "subscription_id": subscription.id, "status": subscription.status.value}


@router.post("/admin/subscriptions/{subscription_id}/reject")
def reject_test_subscription(subscription_id: str, _: AdminActionRequest | None = None, current_user: models.User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    subscription = reject_subscription(subscription_id, current_user.id)
    return {"detail": "Subscription rejected", "subscription_id": subscription.id, "status": subscription.status.value}
