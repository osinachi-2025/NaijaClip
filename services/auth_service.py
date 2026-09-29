from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext
from sqlalchemy import select

import models
from core.config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ADMIN_EMAIL,
    ADMIN_NAME,
    ADMIN_PASSWORD,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    RESET_AUTHORIZATION_EXPIRY_MINUTES,
    RESET_CODE_EXPIRY_MINUTES,
    RESET_CODE_MAX_ATTEMPTS,
    RESET_CODE_RESEND_COOLDOWN_SECONDS,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
import database
from services.plan_service import ensure_default_plans

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user: models.User) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user.id,
        "email": user.email,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user: models.User) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user.id,
        "email": user.email,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])


def _token_is_expired(expires_at: datetime) -> bool:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def register_user(email: str, password: str, first_name: str | None = None, last_name: str | None = None, *, auth_provider: str = "local") -> models.User:
    normalized = _normalize_email(email)
    with database.SessionLocal() as db:
        if db.query(models.User).filter(models.User.email == normalized).first() is not None:
            raise ValueError("A user with this email already exists.")

        ensure_default_plans()
        user = models.User(
            email=normalized,
            password_hash=hash_password(password),
            first_name=first_name,
            last_name=last_name,
            role=models.UserRole.USER,
            status=models.UserStatus.ACTIVE,
            is_active=True,
            is_verified=auth_provider == "google",
            email_verified_at=datetime.now(timezone.utc) if auth_provider == "google" else None,
            auth_provider=auth_provider,
            plan=models.PlanCode.FREE,
            subscription_status=models.SubscriptionStatus.INACTIVE.value,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def create_email_verification_token(user_id: str) -> str:
    raw_token = secrets.token_urlsafe(32)
    record = models.EmailVerificationToken(
        user_id=user_id,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    with database.SessionLocal() as db:
        db.add(record)
        db.commit()
    return raw_token


def link_google_account(
    google_subject: str,
    email: str,
    first_name: str | None = None,
    last_name: str | None = None,
    avatar_url: str | None = None,
) -> models.User:
    normalized_email = _normalize_email(email)
    now = datetime.now(timezone.utc)
    with database.SessionLocal() as db:
        user = db.scalar(select(models.User).where(models.User.google_id == google_subject))
        email_user = db.scalar(select(models.User).where(models.User.email == normalized_email))
        if user is not None and email_user is not None and user.id != email_user.id:
            raise ValueError("Google account email belongs to another user.")
        user = user or email_user

        if user is not None and user.google_id not in (None, google_subject):
            raise ValueError("This email is already linked to a different Google account.")

        if user is None:
            ensure_default_plans()
            user = models.User(
                email=normalized_email,
                password_hash=hash_password(secrets.token_urlsafe(48)),
                first_name=first_name,
                last_name=last_name,
                role=models.UserRole.USER,
                status=models.UserStatus.ACTIVE,
                is_active=True,
                is_verified=True,
                email_verified_at=now,
                auth_provider="google",
                google_id=google_subject,
                avatar_url=avatar_url,
                plan=models.PlanCode.FREE,
                subscription_status=models.SubscriptionStatus.INACTIVE.value,
            )
            db.add(user)
        else:
            if not user.is_active:
                raise ValueError("This account is not active.")
            user.email = normalized_email
            user.google_id = google_subject
            user.email_verified = True
            user.email_verified_at = user.email_verified_at or now
            user.first_name = user.first_name or first_name
            user.last_name = user.last_name or last_name
            user.avatar_url = user.avatar_url or avatar_url
            if user.auth_provider != "local":
                user.auth_provider = "google"

        db.commit()
        db.refresh(user)
        return user


def authenticate_user(email: str, password: str) -> models.User | None:
    with database.SessionLocal() as db:
        user = db.query(models.User).filter(models.User.email == _normalize_email(email)).first()
        if user is None:
            return None
        if not verify_password(password, user.password_hash):
            return None
        if not user.is_active:
            return None
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        return user


def ensure_initial_admin() -> models.User | None:
    admin_email = os.getenv("ADMIN_EMAIL", ADMIN_EMAIL)
    admin_password = os.getenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    admin_name = os.getenv("ADMIN_NAME", ADMIN_NAME) or "NaijaClip Admin"
    with database.SessionLocal() as db:
        admin = db.scalar(select(models.User).where(models.User.role == models.UserRole.ADMIN))
        if admin is not None:
            return admin

        missing = [
            name for name, value in (("ADMIN_EMAIL", admin_email), ("ADMIN_PASSWORD", admin_password))
            if not value or not value.strip()
        ]
        if missing:
            raise RuntimeError(
                "Cannot initialize the database without an admin account. Set required configuration: "
                + ", ".join(missing)
            )

        normalized_email = _normalize_email(admin_email)
        existing_user = db.scalar(select(models.User).where(models.User.email == normalized_email))
        if existing_user is not None:
            raise RuntimeError(
                "ADMIN_EMAIL already belongs to a non-admin user; refusing to change the existing account. "
                "Configure ADMIN_EMAIL to an unused address or assign the account admin role explicitly."
            )

        ensure_default_plans()
        now = datetime.now(timezone.utc)
        admin = models.User(
            email=normalized_email,
            password_hash=hash_password(admin_password),
            first_name=admin_name,
            role=models.UserRole.ADMIN,
            status=models.UserStatus.ACTIVE,
            is_active=True,
            is_verified=True,
            email_verified_at=now,
            auth_provider="local",
            plan=models.PlanCode.PRO,
            subscription_status=models.SubscriptionStatus.ACTIVE.value,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        return admin


def issue_reset_code(user: models.User) -> str:
    now = datetime.now(timezone.utc)
    code = f"{secrets.randbelow(900_000) + 100_000}"
    hash_value = hmac.new(
        JWT_SECRET_KEY.encode(),
        f"naijaclip-reset-code:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()

    with database.SessionLocal() as db:
        latest = db.scalar(
            select(models.PasswordResetToken)
            .where(models.PasswordResetToken.user_id == user.id)
            .order_by(models.PasswordResetToken.created_at.desc())
            .with_for_update()
        )
        if latest is not None:
            created_at = latest.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if now < created_at + timedelta(seconds=RESET_CODE_RESEND_COOLDOWN_SECONDS):
                return ""

        db.query(models.PasswordResetToken).filter(models.PasswordResetToken.user_id == user.id).delete()
        db.query(models.PasswordResetAuthorization).filter(
            models.PasswordResetAuthorization.user_id == user.id
        ).delete()
        token = models.PasswordResetToken(
            user_id=user.id,
            token_hash=hash_value,
            expires_at=now + timedelta(minutes=RESET_CODE_EXPIRY_MINUTES),
            created_at=now,
        )
        db.add(token)
        db.commit()
    return code


def verify_reset_code(user: models.User, code: str) -> tuple[str | None, str | None]:
    if len(code) != 6 or not code.isdigit():
        return None, "invalid"

    hash_value = hmac.new(
        JWT_SECRET_KEY.encode(),
        f"naijaclip-reset-code:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()
    now = datetime.now(timezone.utc)
    with database.SessionLocal() as db:
        reset_token = (
            db.query(models.PasswordResetToken)
            .filter(models.PasswordResetToken.user_id == user.id, models.PasswordResetToken.used_at.is_(None))
            .with_for_update()
            .order_by(models.PasswordResetToken.created_at.desc())
            .first()
        )
        if reset_token is None:
            return None, "invalid"
        if _token_is_expired(reset_token.expires_at):
            return None, "expired"
        if reset_token.attempt_count >= RESET_CODE_MAX_ATTEMPTS:
            reset_token.used_at = now
            db.commit()
            return None, "too_many_attempts"

        reset_token.attempt_count += 1
        if not hmac.compare_digest(reset_token.token_hash, hash_value):
            if reset_token.attempt_count >= RESET_CODE_MAX_ATTEMPTS:
                reset_token.used_at = now
            db.commit()
            return None, "too_many_attempts" if reset_token.used_at is not None else "invalid"

        raw_authorization = secrets.token_urlsafe(32)
        reset_token.used_at = now
        db.add(models.PasswordResetAuthorization(
            user_id=user.id,
            token_hash=hashlib.sha256(raw_authorization.encode()).hexdigest(),
            expires_at=now + timedelta(minutes=RESET_AUTHORIZATION_EXPIRY_MINUTES),
        ))
        db.commit()
        return raw_authorization, None


def is_reset_authorization_valid(raw_token: str | None) -> bool:
    if not raw_token:
        return False
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    with database.SessionLocal() as db:
        authorization = db.scalar(
            select(models.PasswordResetAuthorization)
            .where(
                models.PasswordResetAuthorization.token_hash == token_hash,
                models.PasswordResetAuthorization.used_at.is_(None),
            )
        )
        if authorization is None or _token_is_expired(authorization.expires_at):
            return False
        user = db.get(models.User, authorization.user_id)
        return user is not None and user.is_active and user.auth_provider != "google"


def consume_reset_authorization(raw_token: str | None, new_password: str) -> bool:
    if not raw_token:
        return False
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    with database.SessionLocal() as db:
        authorization = db.scalar(
            select(models.PasswordResetAuthorization)
            .where(
                models.PasswordResetAuthorization.token_hash == token_hash,
                models.PasswordResetAuthorization.used_at.is_(None),
            )
            .with_for_update()
        )
        if authorization is None or _token_is_expired(authorization.expires_at):
            return False
        user = db.get(models.User, authorization.user_id)
        if user is None or not user.is_active or user.auth_provider == "google":
            return False
        user.password_hash = hash_password(new_password)
        authorization.used_at = now
        db.query(models.PasswordResetAuthorization).filter(
            models.PasswordResetAuthorization.user_id == user.id,
            models.PasswordResetAuthorization.id != authorization.id,
        ).delete(synchronize_session=False)
        db.query(models.PasswordResetToken).filter(
            models.PasswordResetToken.user_id == user.id
        ).delete(synchronize_session=False)
        db.commit()
        return True


def verify_email_token(token: str) -> models.User | None:
    with database.SessionLocal() as db:
        hash_value = hashlib.sha256(token.encode()).hexdigest()
        record = db.query(models.EmailVerificationToken).filter(models.EmailVerificationToken.token_hash == hash_value).first()
        if record is None or record.used_at is not None or _token_is_expired(record.expires_at):
            return None
        user = db.get(models.User, record.user_id)
        if user is None:
            return None
        user.email_verified = True
        user.email_verified_at = datetime.now(timezone.utc)
        record.used_at = datetime.now(timezone.utc)
        db.commit()
        return user


def get_user_by_email(email: str) -> models.User | None:
    with database.SessionLocal() as db:
        return db.query(models.User).filter(models.User.email == _normalize_email(email)).first()
