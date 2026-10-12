from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import models
from core.config import FREE_MONTHLY_VIDEO_LIMIT, PRO_MONTHLY_VIDEO_LIMIT
import database


def ensure_default_plans() -> None:
    with database.SessionLocal() as db:
        existing = {plan.code for plan in db.scalars(select(models.Plan)).all()}
        for code, data in {
            models.PlanCode.FREE: {
                "name": "Free",
                "monthly_video_limit": FREE_MONTHLY_VIDEO_LIMIT,
                "monthly_processing_minutes": 180,
                "max_upload_size_mb": 100,
                "export_720p": True,
                "export_1080p": False,
                "watermark": True,
                "priority_processing": False,
                "brand_customization": False,
                "monthly_price": Decimal("0.00"),
                "yearly_price": Decimal("0.00"),
                "is_active": True,
            },
            models.PlanCode.PRO: {
                "name": "Pro",
                "monthly_video_limit": PRO_MONTHLY_VIDEO_LIMIT,
                "monthly_processing_minutes": 600,
                "max_upload_size_mb": 500,
                "export_720p": True,
                "export_1080p": True,
                "watermark": False,
                "priority_processing": True,
                "brand_customization": True,
                "monthly_price": Decimal("2500.00"),
                "yearly_price": Decimal("25000.00"),
                "is_active": True,
            },
        }.items():
            if code in existing:
                continue
            db.add(models.Plan(code=code, **data))
        db.commit()


def monthly_limit_for_plan(plan_value: models.PlanCode | str | None) -> int:
    plan_name = str(getattr(plan_value, "value", plan_value)).lower() if plan_value is not None else "free"
    if plan_name == models.PlanCode.PRO.value:
        return PRO_MONTHLY_VIDEO_LIMIT
    return FREE_MONTHLY_VIDEO_LIMIT


def monthly_video_usage(db: Session, user_id: str, *, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(db.scalar(
        select(func.count(models.Video.id)).where(
            models.Video.user_id == user_id,
            models.Video.created_at >= month_start,
        )
    ) or 0)


def get_plan_by_code(code: models.PlanCode | str) -> models.Plan | None:
    normalized = str(getattr(code, "value", code)).lower()
    with database.SessionLocal() as db:
        return db.query(models.Plan).filter(models.Plan.code == models.PlanCode(normalized)).first()
