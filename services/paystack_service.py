from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

import models
from core.config import AUTO_ACTIVATE_PRO, PAYSTACK_CALLBACK_URL, PAYSTACK_MODE, PAYSTACK_SECRET_KEY
import database
from services.email_service import send_subscription_approval_email
	


def _normalize_payment_mode(mode: str | None) -> str:
    raw_mode = os.getenv("PAYSTACK_MODE") or PAYSTACK_MODE or "test"
    return (mode or raw_mode or "test").lower()


def _auto_activate_pro() -> bool:
    raw = os.getenv("AUTO_ACTIVATE_PRO")
    if raw is None:
        raw = str(AUTO_ACTIVATE_PRO)
    return raw.lower() in {"1", "true", "yes", "on"}


def _generate_reference() -> str:
    return f"paystack_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(6).hex()}"


def initialize_payment(user_id: str, amount: Decimal, currency: str, plan: str, reference: str | None = None) -> models.Payment:
    with database.SessionLocal() as db:
        record = models.Payment(
            user_id=user_id,
            amount=amount,
            currency=currency,
            reference=reference or _generate_reference(),
            payment_mode=_normalize_payment_mode(None),
            status=models.PaymentStatus.PENDING,
            provider=models.PaymentProvider.PAYSTACK,
            plan=plan,
            description=f"{plan.upper()} subscription",
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record


def process_paystack_payment(*, user_id: str, amount: Decimal, currency: str, reference: str, plan: str) -> models.Payment:
    with database.SessionLocal() as db:
        payment = db.query(models.Payment).filter(models.Payment.reference == reference).first()
        if payment is None:
            payment = models.Payment(
                user_id=user_id,
                amount=amount,
                currency=currency,
                reference=reference,
                payment_mode=_normalize_payment_mode(None),
                status=models.PaymentStatus.SUCCESS,
                provider=models.PaymentProvider.PAYSTACK,
                plan=plan,
                description=f"{plan.upper()} subscription",
            )
            db.add(payment)
            db.commit()
            db.refresh(payment)

        if payment.status == models.PaymentStatus.SUCCESS and payment.user_id == user_id:
            payment.subscription = ensure_subscription(db, payment, plan)
            db.commit()
            return payment

        payment.status = models.PaymentStatus.SUCCESS
        payment.amount = Decimal(str(amount))
        payment.currency = currency
        payment.payment_mode = _normalize_payment_mode(None)
        if payment.payment_mode == "test":
            payment.subscription = ensure_subscription(db, payment, plan)
            payment.subscription.status = models.SubscriptionStatus.PENDING
            payment.subscription.plan = db.query(models.Plan).filter(models.Plan.code == models.PlanCode.PRO).first()
            db.commit()
            return payment

        if _auto_activate_pro():
            payment.subscription = ensure_subscription(db, payment, plan)
            payment.subscription.status = models.SubscriptionStatus.ACTIVE
            payment.subscription.current_period_start = datetime.now(timezone.utc)
            payment.subscription.current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
            payment.subscription.provider = "paystack"
            payment.subscription.reference = payment.reference
            payment.subscription.payment_id = payment.id
            user = db.get(models.User, user_id)
            if user is not None:
                user.plan = models.PlanCode.PRO
                user.subscription_status = models.SubscriptionStatus.ACTIVE.value
                user.subscription_expires_at = payment.subscription.current_period_end
        db.commit()
        return payment


def ensure_subscription(db, payment: models.Payment, plan: str) -> models.Subscription:
    plan_row = db.query(models.Plan).filter(models.Plan.code == models.PlanCode.PRO if plan.lower() == "pro" else models.PlanCode.FREE).first()
    if plan_row is None:
        plan_row = models.Plan(code=(models.PlanCode.PRO if plan.lower() == "pro" else models.PlanCode.FREE), name="Pro" if plan.lower() == "pro" else "Free", monthly_video_limit=50 if plan.lower() == "pro" else 3, monthly_processing_minutes=600 if plan.lower() == "pro" else 180, max_upload_size_mb=500 if plan.lower() == "pro" else 100, export_720p=True, export_1080p=plan.lower() == "pro", watermark=plan.lower() != "pro", priority_processing=plan.lower() == "pro", brand_customization=plan.lower() == "pro", monthly_price=Decimal("2500.00") if plan.lower() == "pro" else Decimal("0.00"), yearly_price=Decimal("25000.00") if plan.lower() == "pro" else Decimal("0.00"), is_active=True)
        db.add(plan_row)
        db.flush()

    subscription = db.query(models.Subscription).filter(models.Subscription.user_id == payment.user_id, models.Subscription.status.in_([models.SubscriptionStatus.PENDING, models.SubscriptionStatus.ACTIVE])).order_by(models.Subscription.created_at.desc()).first()
    if subscription is not None:
        subscription.plan = plan_row
        return subscription

    now = datetime.now(timezone.utc)
    subscription = models.Subscription(
        user_id=payment.user_id,
        plan_id=plan_row.id,
        status=models.SubscriptionStatus.PENDING if _normalize_payment_mode(None) == "test" else models.SubscriptionStatus.ACTIVE if _auto_activate_pro() else models.SubscriptionStatus.INACTIVE,
        billing_interval=models.BillingInterval.MONTHLY,
        provider="paystack",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        reference=payment.reference,
        payment_id=payment.id,
    )
    db.add(subscription)
    db.flush()
    subscription.plan = plan_row
    return subscription


def approve_subscription(subscription_id: str, admin_user_id: str) -> models.Subscription:
    with database.SessionLocal() as db:
        subscription = db.get(models.Subscription, subscription_id)
        if subscription is None:
            raise ValueError("Subscription not found.")
        payment = db.get(models.Payment, subscription.payment_id) if subscription.payment_id else None
        if payment is None or payment.status != models.PaymentStatus.SUCCESS:
            raise ValueError("Payment not found or not successful.")
        if payment.payment_mode == "test":
            raise ValueError("Test-mode payments cannot activate a Pro subscription.")
        if subscription.status == models.SubscriptionStatus.ACTIVE:
            return subscription

        subscription.status = models.SubscriptionStatus.ACTIVE
        subscription.current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
        user = db.get(models.User, subscription.user_id)
        if user is not None:
            user.plan = models.PlanCode.PRO
            user.subscription_status = models.SubscriptionStatus.ACTIVE.value
            user.subscription_expires_at = subscription.current_period_end
            user.is_active = True
        db.add(models.AdminAuditLog(admin_user_id=admin_user_id, action="APPROVED_TEST_SUBSCRIPTION", target_user_id=subscription.user_id, target_subscription_id=subscription.id, audit_metadata=json.dumps({"payment_id": payment.id, "reference": payment.reference})))
        db.commit()
        db.refresh(subscription)
        if user is not None and user.email:
            send_subscription_approval_email(user.email, True)
        return subscription


def reject_subscription(subscription_id: str, admin_user_id: str) -> models.Subscription:
    with database.SessionLocal() as db:
        subscription = db.get(models.Subscription, subscription_id)
        if subscription is None:
            raise ValueError("Subscription not found.")
        subscription.status = models.SubscriptionStatus.INACTIVE
        user = db.get(models.User, subscription.user_id)
        if user is not None:
            user.plan = models.PlanCode.FREE
            user.subscription_status = models.SubscriptionStatus.INACTIVE.value
            user.subscription_expires_at = None
        db.add(models.AdminAuditLog(admin_user_id=admin_user_id, action="REJECTED_TEST_SUBSCRIPTION", target_user_id=subscription.user_id, target_subscription_id=subscription.id, audit_metadata=json.dumps({"subscription_id": subscription.id})))
        db.commit()
        db.refresh(subscription)
        if user is not None and user.email:
            send_subscription_approval_email(user.email, False)
        return subscription


def verify_webhook_signature(payload: bytes, signature: str | None) -> bool:
    if not PAYSTACK_SECRET_KEY or not signature:
        return False
    expected = hmac.new(PAYSTACK_SECRET_KEY.encode(), payload, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle_paystack_webhook(payload: dict[str, Any], signature: str | None) -> dict[str, Any]:
    if not verify_webhook_signature(json.dumps(payload).encode(), signature):
        raise ValueError("Invalid webhook signature.")

    event = payload.get("event")
    if event != "charge.success":
        return {"processed": False, "status": "ignored"}

    data = payload.get("data", {})
    reference = data.get("reference")
    if not reference:
        raise ValueError("Missing reference.")

    with database.SessionLocal() as db:
        payment = db.query(models.Payment).filter(models.Payment.reference == reference).first()
        if payment is None:
            payment = models.Payment(
                user_id=data.get("customer", {}).get("email", "unknown"),
                amount=Decimal(str(data.get("amount", 0))) / Decimal("100"),
                currency=data.get("currency", "NGN"),
                reference=reference,
                payment_mode=_normalize_payment_mode("live" if data.get("live") else "test"),
                status=models.PaymentStatus.SUCCESS,
                provider=models.PaymentProvider.PAYSTACK,
                plan=data.get("metadata", {}).get("plan", "pro"),
                provider_transaction_id=str(data.get("id")),
                payment_metadata=json.dumps(data),
            )
            db.add(payment)
            db.flush()
        elif payment.status == models.PaymentStatus.SUCCESS:
            return {"processed": False, "status": "duplicate"}

        payment.status = models.PaymentStatus.SUCCESS
        payment.provider_transaction_id = str(data.get("id"))
        payment.raw_response = json.dumps(data)
        payment.payment_metadata = json.dumps(data)
        payment.subscription = ensure_subscription(db, payment, payment.plan or "pro")
        if payment.payment_mode == "test":
            payment.subscription.status = models.SubscriptionStatus.PENDING
        elif _auto_activate_pro():
            payment.subscription.status = models.SubscriptionStatus.ACTIVE
            payment.subscription.current_period_start = datetime.now(timezone.utc)
            payment.subscription.current_period_end = datetime.now(timezone.utc) + timedelta(days=30)
            payment.subscription.provider = "paystack"
            payment.subscription.reference = payment.reference
            payment.subscription.payment_id = payment.id
            user = db.get(models.User, payment.user_id)
            if user is not None:
                user.plan = models.PlanCode.PRO
                user.subscription_status = models.SubscriptionStatus.ACTIVE.value
                user.subscription_expires_at = payment.subscription.current_period_end
        else:
            payment.subscription.status = models.SubscriptionStatus.PENDING
        db.commit()
        return {"processed": True, "status": payment.status.value, "reference": reference}
