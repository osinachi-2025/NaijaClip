# app/models.py

from __future__ import annotations
from database import Base

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    JSON,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship




# ============================================================
# HELPERS
# ============================================================

def generate_uuid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
# ENUMS
# ============================================================

class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"


class UserStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class PlanCode(str, Enum):
    FREE = "free"
    PRO = "pro"


class BillingInterval(str, Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


class SubscriptionStatus(str, Enum):
    INACTIVE = "inactive"
    PENDING = "pending"
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class VideoStatus(str, Enum):
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    QUEUED = "queued"
    VALIDATING = "validating"
    PREPARING = "preparing"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCELED = "canceled"


class VideoVisibility(str, Enum):
    PRIVATE = "private"
    PUBLIC = "public"


class JobType(str, Enum):
    TRANSCRIPTION = "transcription"
    AI_ANALYSIS = "ai_analysis"
    CLIP_RENDER = "clip_render"
    CAPTION_RENDER = "caption_render"
    THUMBNAIL = "thumbnail"
    EXPORT = "export"


class JobStatus(str, Enum):
    QUEUED = "queued"
    INGESTING = "ingesting"
    VALIDATING = "validating"
    PREPARING = "preparing"
    ANALYZING = "analyzing"
    TRANSCRIBING = "transcribing"
    SELECTING = "selecting"
    SELECTING_CLIPS = "selecting_clips"
    SCORING = "scoring"
    EXTRACTING = "extracting"
    DETECTING = "detecting"
    TRACKING = "tracking"
    REFRAMING = "reframing"
    CAPTIONS = "captions"
    AUDIO_PROCESSING = "audio_processing"
    RENDERING = "rendering"
    VALIDATING_OUTPUT = "validating_output"
    EXPORTING = "exporting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCELED = "canceled"


class TranscriptStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ClipStatus(str, Enum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    RENDERING = "rendering"
    READY = "ready"
    FAILED = "failed"
    DISCARDED = "discarded"


class ClipAspectRatio(str, Enum):
    PORTRAIT = "9:16"
    SQUARE = "1:1"
    LANDSCAPE = "16:9"


class CaptionStyle(str, Enum):
    NONE = "none"
    BASIC = "basic"
    BOLD = "bold"
    HIGHLIGHT = "highlight"


class ExportStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    REFUNDED = "refunded"


class PaymentProvider(str, Enum):
    PAYSTACK = "paystack"


def enum_values(enum_cls):
    return [member.value for member in enum_cls]


# ============================================================
# USERS
# ============================================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        nullable=False,
        index=True,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    first_name: Mapped[Optional[str]] = mapped_column(
        String(100)
    )

    last_name: Mapped[Optional[str]] = mapped_column(
        String(100)
    )

    role: Mapped[UserRole] = mapped_column(
        SAEnum(
            UserRole,
            name="user_role",
            native_enum=False,
            values_callable=enum_values,
        ),
        default=UserRole.USER,
        nullable=False,
    )

    status: Mapped[UserStatus] = mapped_column(
        SAEnum(
            UserStatus,
            name="user_status",
            native_enum=False,
            values_callable=enum_values,
        ),
        default=UserStatus.ACTIVE,
        nullable=False,
    )

    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    email_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    auth_provider: Mapped[str] = mapped_column(
        String(50),
        default="local",
        nullable=False,
    )

    google_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
        index=True,
    )

    @property
    def is_verified(self) -> bool:
        return self.email_verified

    @is_verified.setter
    def is_verified(self, value: bool) -> None:
        self.email_verified = value

    plan: Mapped[PlanCode] = mapped_column(
        SAEnum(
            PlanCode,
            name="user_plan",
            native_enum=False,
            values_callable=enum_values,
        ),
        default=PlanCode.FREE,
        nullable=False,
    )

    subscription_status: Mapped[str] = mapped_column(
        String(50),
        default=SubscriptionStatus.INACTIVE.value,
        nullable=False,
    )

    subscription_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    videos_used: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    avatar_url: Mapped[Optional[str]] = mapped_column(
        String(2048)
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    # Relationships

    videos: Mapped[list["Video"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    usage_records: Mapped[list["UsageRecord"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    payments: Mapped[list["Payment"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


# ============================================================
# PLANS
# ============================================================

class Plan(Base):
    __tablename__ = "plans"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PlanCode):
            return self.code == other
        if isinstance(other, str):
            return self.code.value == other.lower()
        return super().__eq__(other)

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    code: Mapped[PlanCode] = mapped_column(
        SAEnum(
            PlanCode,
            name="plan_code",
            native_enum=False,
            values_callable=enum_values,
        ),
        unique=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    monthly_video_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    monthly_processing_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    max_upload_size_mb: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    export_720p: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    export_1080p: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    watermark: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    priority_processing: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    brand_customization: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    monthly_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    yearly_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0.00"),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="plan"
    )


# ============================================================
# SUBSCRIPTIONS
# ============================================================

class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    plan_id: Mapped[str] = mapped_column(
        ForeignKey(
            "plans.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    status: Mapped[SubscriptionStatus] = mapped_column(
        SAEnum(
            SubscriptionStatus,
            name="subscription_status",
            native_enum=False,
            values_callable=enum_values,
        ),
        default=SubscriptionStatus.ACTIVE,
        nullable=False,
    )

    billing_interval: Mapped[BillingInterval] = mapped_column(
        SAEnum(
            BillingInterval,
            name="billing_interval",
            native_enum=False,
            values_callable=enum_values,
        ),
        default=BillingInterval.MONTHLY,
        nullable=False,
    )

    provider: Mapped[Optional[str]] = mapped_column(
        String(50)
    )

    payment_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("payments.id", ondelete="SET NULL"),
        index=True,
    )

    reference: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
        index=True,
    )

    provider_subscription_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
    )

    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    trial_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    canceled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    user: Mapped["User"] = relationship(
        back_populates="subscriptions"
    )

    payment: Mapped[Optional["Payment"]] = relationship(
        back_populates="subscription",
        foreign_keys="[Subscription.payment_id]",
        uselist=False,
    )

    plan: Mapped["Plan"] = relationship(
        back_populates="subscriptions"
    )

    __table_args__ = (
        Index(
            "ix_subscriptions_user_status",
            "user_id",
            "status",
        ),
    )


# ============================================================
# VIDEOS
# ============================================================

class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    original_filename: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    status: Mapped[VideoStatus] = mapped_column(
        SAEnum(
            VideoStatus,
            name="video_status",
            native_enum=False,
        ),
        default=VideoStatus.UPLOADING,
        nullable=False,
        index=True,
    )

    visibility: Mapped[VideoVisibility] = mapped_column(
        SAEnum(
            VideoVisibility,
            name="video_visibility",
            native_enum=False,
        ),
        default=VideoVisibility.PRIVATE,
        nullable=False,
    )

    source_url: Mapped[Optional[str]] = mapped_column(
        String(2048)
    )

    source_storage_key: Mapped[Optional[str]] = mapped_column(
        String(1024)
    )

    source_mime_type: Mapped[Optional[str]] = mapped_column(
        String(100)
    )

    file_size_bytes: Mapped[Optional[int]] = mapped_column(
        Integer
    )

    duration_seconds: Mapped[Optional[float]] = mapped_column(
        Numeric(12, 3)
    )

    width: Mapped[Optional[int]] = mapped_column(
        Integer
    )

    height: Mapped[Optional[int]] = mapped_column(
        Integer
    )

    frame_rate: Mapped[Optional[float]] = mapped_column(
        Numeric(8, 3)
    )

    video_codec: Mapped[Optional[str]] = mapped_column(
        String(50)
    )

    audio_codec: Mapped[Optional[str]] = mapped_column(
        String(50)
    )

    audio_quality_status: Mapped[Optional[str]] = mapped_column(
        String(50)
    )

    media_metadata: Mapped[Optional[str]] = mapped_column(
        Text
    )

    clip_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    processing_error: Mapped[Optional[str]] = mapped_column(
        Text
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    user: Mapped["User"] = relationship(
        back_populates="videos"
    )

    jobs: Mapped[list["ProcessingJob"]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
    )

    transcript: Mapped[Optional["Transcript"]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
        uselist=False,
    )

    clips: Mapped[list["Clip"]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "ix_videos_user_created",
            "user_id",
            "created_at",
        ),
        Index(
            "ix_videos_user_status",
            "user_id",
            "status",
        ),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0"
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0"
        ),
    )


# ============================================================
# PROCESSING JOBS
# ============================================================

class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    video_id: Mapped[str] = mapped_column(
        ForeignKey(
            "videos.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    job_type: Mapped[JobType] = mapped_column(
        SAEnum(
            JobType,
            name="job_type",
            native_enum=False,
        ),
        nullable=False,
    )

    status: Mapped[JobStatus] = mapped_column(
        SAEnum(
            JobStatus,
            name="job_status",
            native_enum=False,
        ),
        default=JobStatus.QUEUED,
        nullable=False,
        index=True,
    )

    queue_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        index=True,
    )

    worker_id: Mapped[Optional[str]] = mapped_column(
        String(255)
    )

    progress: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    current_stage: Mapped[Optional[str]] = mapped_column(String(100))

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    error_message: Mapped[Optional[str]] = mapped_column(
        Text
    )

    error_code: Mapped[Optional[str]] = mapped_column(
        String(50)
    )

    safe_error_message: Mapped[Optional[str]] = mapped_column(
        Text
    )

    internal_error_details: Mapped[Optional[str]] = mapped_column(
        Text
    )

    retryable: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    max_attempts: Mapped[int] = mapped_column(
        Integer,
        default=3,
        nullable=False,
    )

    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    video: Mapped["Video"] = relationship(
        back_populates="jobs"
    )

    __table_args__ = (
        Index(
            "ix_jobs_video_type",
            "video_id",
            "job_type",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100"
        ),
        CheckConstraint(
            "attempt_count >= 0"
        ),
    )


# ============================================================
# TRANSCRIPTS
# ============================================================

class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    video_id: Mapped[str] = mapped_column(
        ForeignKey(
            "videos.id",
            ondelete="CASCADE",
        ),
        unique=True,
        nullable=False,
    )

    provider: Mapped[str] = mapped_column(
        String(50),
        default="deepgram",
        nullable=False,
    )

    provider_job_id: Mapped[Optional[str]] = mapped_column(
        String(255)
    )

    status: Mapped[TranscriptStatus] = mapped_column(
        SAEnum(
            TranscriptStatus,
            name="transcript_status",
            native_enum=False,
        ),
        default=TranscriptStatus.PENDING,
        nullable=False,
    )

    language: Mapped[Optional[str]] = mapped_column(
        String(20)
    )

    full_text: Mapped[Optional[str]] = mapped_column(
        Text
    )

    raw_response_url: Mapped[Optional[str]] = mapped_column(
        String(2048)
    )

    error_message: Mapped[Optional[str]] = mapped_column(
        Text
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    video: Mapped["Video"] = relationship(
        back_populates="transcript"
    )

    segments: Mapped[list["TranscriptSegment"]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
        order_by="TranscriptSegment.start_seconds",
    )


# ============================================================
# TRANSCRIPT SEGMENTS
# ============================================================

class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    transcript_id: Mapped[str] = mapped_column(
        ForeignKey(
            "transcripts.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    start_seconds: Mapped[float] = mapped_column(
        Numeric(12, 3),
        nullable=False,
    )

    end_seconds: Mapped[float] = mapped_column(
        Numeric(12, 3),
        nullable=False,
    )

    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    speaker: Mapped[Optional[str]] = mapped_column(
        String(100)
    )

    confidence: Mapped[Optional[float]] = mapped_column(
        Numeric(6, 5)
    )

    transcript: Mapped["Transcript"] = relationship(
        back_populates="segments"
    )

    __table_args__ = (
        Index(
            "ix_transcript_segments_transcript_start",
            "transcript_id",
            "start_seconds",
        ),
        CheckConstraint(
            "start_seconds >= 0"
        ),
        CheckConstraint(
            "end_seconds >= start_seconds"
        ),
        CheckConstraint(
            "confidence IS NULL OR "
            "(confidence >= 0 AND confidence <= 1)"
        ),
    )


# ============================================================
# CLIPS
# ============================================================

class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    video_id: Mapped[str] = mapped_column(
        ForeignKey(
            "videos.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    source_segment_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey(
            "transcript_segments.id",
            ondelete="SET NULL",
        ),
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text
    )

    start_seconds: Mapped[float] = mapped_column(
        Numeric(12, 3),
        nullable=False,
    )

    end_seconds: Mapped[float] = mapped_column(
        Numeric(12, 3),
        nullable=False,
    )

    status: Mapped[ClipStatus] = mapped_column(
        SAEnum(
            ClipStatus,
            name="clip_status",
            native_enum=False,
        ),
        default=ClipStatus.CANDIDATE,
        nullable=False,
        index=True,
    )

    ai_score: Mapped[Optional[float]] = mapped_column(
        Numeric(7, 4)
    )

    hook_score: Mapped[Optional[float]] = mapped_column(
        Numeric(7, 4)
    )

    standalone_score: Mapped[Optional[float]] = mapped_column(
        Numeric(7, 4)
    )

    engagement_score: Mapped[Optional[float]] = mapped_column(
        Numeric(7, 4)
    )

    rank: Mapped[Optional[int]] = mapped_column(
        Integer
    )

    aspect_ratio: Mapped[ClipAspectRatio] = mapped_column(
        SAEnum(
            ClipAspectRatio,
            name="clip_aspect_ratio",
            native_enum=False,
        ),
        default=ClipAspectRatio.PORTRAIT,
        nullable=False,
    )

    caption_style: Mapped[CaptionStyle] = mapped_column(
        SAEnum(
            CaptionStyle,
            name="caption_style",
            native_enum=False,
        ),
        default=CaptionStyle.BASIC,
        nullable=False,
    )

    caption_text: Mapped[Optional[str]] = mapped_column(
        Text
    )

    output_url: Mapped[Optional[str]] = mapped_column(
        String(2048)
    )

    output_storage_key: Mapped[Optional[str]] = mapped_column(
        String(1024)
    )

    editor_source_storage_key: Mapped[Optional[str]] = mapped_column(
        String(1024)
    )

    thumbnail_url: Mapped[Optional[str]] = mapped_column(
        String(2048)
    )

    thumbnail_storage_key: Mapped[Optional[str]] = mapped_column(
        String(1024)
    )

    is_featured: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    featured_order: Mapped[Optional[int]] = mapped_column(
        Integer
    )

    featured_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    output_mime_type: Mapped[Optional[str]] = mapped_column(
        String(100)
    )

    output_size_bytes: Mapped[Optional[int]] = mapped_column(
        Integer
    )

    processing_error: Mapped[Optional[str]] = mapped_column(
        Text
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    video: Mapped["Video"] = relationship(
        back_populates="clips"
    )

    source_segment: Mapped[Optional["TranscriptSegment"]] = relationship()

    exports: Mapped[list["ClipExport"]] = relationship(
        back_populates="clip",
        cascade="all, delete-orphan",
    )

    edits: Mapped[list["ClipEdit"]] = relationship(
        back_populates="clip",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "ix_clips_video_status",
            "video_id",
            "status",
        ),
        Index(
            "ix_clips_video_rank",
            "video_id",
            "rank",
        ),
        Index(
            "ix_clips_featured_order",
            "is_featured",
            "featured_order",
        ),
        CheckConstraint(
            "start_seconds >= 0"
        ),
        CheckConstraint(
            "end_seconds > start_seconds"
        ),
        CheckConstraint(
            "output_size_bytes IS NULL OR output_size_bytes >= 0"
        ),
    )


# ============================================================
# CLIP EDITS
# ============================================================

class ClipEdit(Base):
    __tablename__ = "clip_edits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    clip: Mapped["Clip"] = relationship(back_populates="edits")

    __table_args__ = (
        UniqueConstraint("clip_id", "user_id", name="uq_clip_edits_clip_user"),
    )


# ============================================================
# CLIP EXPORTS
# ============================================================

class ClipExport(Base):
    __tablename__ = "clip_exports"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    clip_id: Mapped[str] = mapped_column(
        ForeignKey(
            "clips.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    status: Mapped[ExportStatus] = mapped_column(
        SAEnum(
            ExportStatus,
            name="export_status",
            native_enum=False,
        ),
        default=ExportStatus.QUEUED,
        nullable=False,
    )

    format: Mapped[str] = mapped_column(
        String(20),
        default="mp4",
        nullable=False,
    )

    resolution: Mapped[str] = mapped_column(
        String(20),
        default="1080p",
        nullable=False,
    )

    output_url: Mapped[Optional[str]] = mapped_column(
        String(2048)
    )

    output_storage_key: Mapped[Optional[str]] = mapped_column(
        String(1024)
    )

    edit_configuration: Mapped[Optional[dict]] = mapped_column(JSON)

    processing_job_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        unique=True,
        index=True,
    )

    file_size_bytes: Mapped[Optional[int]] = mapped_column(
        Integer
    )

    error_message: Mapped[Optional[str]] = mapped_column(
        Text
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    clip: Mapped["Clip"] = relationship(
        back_populates="exports"
    )


# ============================================================
# USAGE RECORDS
# ============================================================

class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    videos_processed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    processing_seconds: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    clips_generated: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    user: Mapped["User"] = relationship(
        back_populates="usage_records"
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "period_start",
            name="uq_usage_user_period",
        ),
        CheckConstraint(
            "videos_processed >= 0"
        ),
        CheckConstraint(
            "processing_seconds >= 0"
        ),
        CheckConstraint(
            "clips_generated >= 0"
        ),
    )


# ============================================================
# PAYMENTS
# ============================================================

class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    provider: Mapped[PaymentProvider] = mapped_column(
        SAEnum(
            PaymentProvider,
            name="payment_provider",
            native_enum=False,
        ),
        default=PaymentProvider.PAYSTACK,
        nullable=False,
    )

    payment_mode: Mapped[str] = mapped_column(
        String(20),
        default="test",
        nullable=False,
    )

    plan: Mapped[Optional[str]] = mapped_column(
        String(20)
    )

    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(
            PaymentStatus,
            name="payment_status",
            native_enum=False,
        ),
        default=PaymentStatus.PENDING,
        nullable=False,
        index=True,
    )

    reference: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    provider_transaction_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        index=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
        nullable=False,
    )

    description: Mapped[Optional[str]] = mapped_column(
        String(500)
    )

    raw_response: Mapped[Optional[str]] = mapped_column(
        Text
    )

    payment_metadata: Mapped[Optional[str]] = mapped_column(
        Text
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    user: Mapped["User"] = relationship(
        back_populates="payments"
    )

    subscription: Mapped[Optional["Subscription"]] = relationship(
        back_populates="payment",
        foreign_keys="[Subscription.payment_id]",
        uselist=False,
    )

    __table_args__ = (
        Index(
            "ix_payments_user_created",
            "user_id",
            "created_at",
        ),
        CheckConstraint(
            "amount >= 0"
        ),
    )


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    admin_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    target_payment_id: Mapped[Optional[str]] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"), index=True)
    target_subscription_id: Mapped[Optional[str]] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True)
    audit_metadata: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ============================================================
# EMAIL VERIFICATION TOKENS
# ============================================================

class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


# ============================================================
# PASSWORD RESET TOKENS
# ============================================================

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class PasswordResetAuthorization(Base):
    __tablename__ = "password_reset_authorizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)