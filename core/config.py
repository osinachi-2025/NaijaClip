from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

def _get_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(float(raw))
        except ValueError:
            value = default
    if minimum is not None and value < minimum:
        value = minimum
    return value


def _get_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            value = default
    if minimum is not None and value < minimum:
        value = minimum
    return value


BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", str(BASE_DIR / "media")))
WORKER_POLL_SECONDS = _get_float("WORKER_POLL_SECONDS", 2.0, minimum=0.5)
MAX_UPLOAD_SIZE_MB = _get_int("MAX_UPLOAD_SIZE_MB", 200, minimum=1)
MAX_UPLOAD_BYTES = _get_int("MAX_UPLOAD_BYTES", MAX_UPLOAD_SIZE_MB * 1024 * 1024, minimum=1)
MAX_VIDEO_DURATION_MINUTES = _get_float("MAX_VIDEO_DURATION_MINUTES", 180.0, minimum=1.0)
MIN_VIDEO_DURATION_SECONDS = _get_float("MIN_VIDEO_DURATION_SECONDS", 3.0, minimum=0.0)
MAX_VIDEO_WIDTH = _get_int("MAX_VIDEO_WIDTH", 3840, minimum=1)
MAX_VIDEO_HEIGHT = _get_int("MAX_VIDEO_HEIGHT", 3840, minimum=1)
AUDIO_SILENCE_THRESHOLD = _get_float("AUDIO_SILENCE_THRESHOLD", 0.02, minimum=0.0)
MIN_AUDIO_LEVEL = _get_float("MIN_AUDIO_LEVEL", 0.03, minimum=0.0)
TARGET_CLIP_DURATION_SECONDS = _get_float("TARGET_CLIP_DURATION_SECONDS", 60.0, minimum=1.0)
MAX_CLIP_DURATION_SECONDS = _get_float("MAX_CLIP_DURATION_SECONDS", 180.0, minimum=1.0)
MAX_CLIPS_PER_VIDEO = _get_int("MAX_CLIPS_PER_VIDEO", 5, minimum=1)
MAX_FEATURED_HOMEPAGE_CLIPS = _get_int("MAX_FEATURED_HOMEPAGE_CLIPS", 6, minimum=1)
WORKER_CONCURRENCY = _get_int("WORKER_CONCURRENCY", 2, minimum=1)
JOB_TIMEOUT_SECONDS = _get_int("JOB_TIMEOUT_SECONDS", 1800, minimum=30)
TRANSCRIPTION_TIMEOUT_SECONDS = _get_int("TRANSCRIPTION_TIMEOUT_SECONDS", 600, minimum=30)
LLM_TIMEOUT_SECONDS = _get_int("LLM_TIMEOUT_SECONDS", 180, minimum=30)
RENDER_TIMEOUT_SECONDS = _get_int("RENDER_TIMEOUT_SECONDS", 600, minimum=30)
MIN_TRACKING_COVERAGE = min(1.0, _get_float("MIN_TRACKING_COVERAGE", 0.95, minimum=0.0))
CAMERA_SMOOTHING_ALPHA = _get_float("CAMERA_SMOOTHING_ALPHA", 0.08, minimum=0.01)
CAMERA_DEAD_ZONE = _get_float("CAMERA_DEAD_ZONE", 64.0, minimum=0.0)
CAMERA_MAX_STEP = _get_float("CAMERA_MAX_STEP", 48.0, minimum=0.1)
CAMERA_MAX_VELOCITY = _get_float("CAMERA_MAX_VELOCITY", 8.0, minimum=0.1)
CAMERA_ACCELERATION = _get_float("CAMERA_ACCELERATION", 0.6, minimum=0.01)
CAMERA_LOOK_AHEAD_SECONDS = _get_float("CAMERA_LOOK_AHEAD_SECONDS", 0.12, minimum=0.0)
CAMERA_LOOK_AHEAD_MAX = _get_float("CAMERA_LOOK_AHEAD_MAX", 24.0, minimum=0.0)
CAMERA_PATH_MAX_ERROR_PX = _get_float("CAMERA_PATH_MAX_ERROR_PX", 0.75, minimum=0.0)
TRACK_LOST_HOLD_FRAMES = _get_int("TRACK_LOST_HOLD_FRAMES", 38, minimum=0)
CAMERA_RETURN_SPEED = _get_float("CAMERA_RETURN_SPEED", 4.0, minimum=0.1)
MAX_RETRIES = _get_int("MAX_RETRIES", 3, minimum=0)
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "nova-3")
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "r2").lower()
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL") or (
    f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else None
)
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL")

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or "dev-secret-key-change-me"
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = _get_int("ACCESS_TOKEN_EXPIRE_MINUTES", 30, minimum=1)
REFRESH_TOKEN_EXPIRE_DAYS = _get_int("REFRESH_TOKEN_EXPIRE_DAYS", 30, minimum=1)
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://naijaclip.name.ng")
AUTH_COOKIE_SECURE = os.getenv(
    "AUTH_COOKIE_SECURE",
    "true" if FRONTEND_URL.startswith("https://") else "false",
).lower() in {"1", "true", "yes", "on"}
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "lax").lower()
if AUTH_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    AUTH_COOKIE_SAMESITE = "lax"
if AUTH_COOKIE_SAMESITE == "none" and not AUTH_COOKIE_SECURE:
    AUTH_COOKIE_SECURE = True
AUTH_COOKIE_DOMAIN = os.getenv("AUTH_COOKIE_DOMAIN") or None
AUTH_ACCESS_COOKIE_NAME = "naijaclip_access"
AUTH_REFRESH_COOKIE_NAME = "naijaclip_refresh"
CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", FRONTEND_URL).split(",") if origin.strip()]
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "https://naijaclip.name.ng/api/auth/google/callback",
)
RESEND_API_KEY = (os.getenv("RESEND_API_KEY") or "").strip() or None
MAIL_FROM_EMAIL = (os.getenv("MAIL_FROM_EMAIL") or "admin@naijaclip.name.ng").strip()
MAIL_FROM_NAME = (os.getenv("MAIL_FROM_NAME") or "NaijaClip").strip()
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY")
PAYSTACK_CALLBACK_URL = os.getenv("PAYSTACK_CALLBACK_URL")
PAYSTACK_MODE = (os.getenv("PAYSTACK_MODE", "test") or "test").lower()
AUTO_ACTIVATE_PRO = (os.getenv("AUTO_ACTIVATE_PRO", "false") or "false").lower() in {"1", "true", "yes", "on"}
FREE_MONTHLY_VIDEO_LIMIT = _get_int("FREE_MONTHLY_VIDEO_LIMIT", 3, minimum=1)
PRO_MONTHLY_VIDEO_LIMIT = _get_int("PRO_MONTHLY_VIDEO_LIMIT", 50, minimum=1)
RESET_CODE_EXPIRY_MINUTES = _get_int("RESET_CODE_EXPIRY_MINUTES", 10, minimum=1)
RESET_CODE_MAX_ATTEMPTS = _get_int("RESET_CODE_MAX_ATTEMPTS", 5, minimum=1)
RESET_CODE_RESEND_COOLDOWN_SECONDS = _get_int("RESET_CODE_RESEND_COOLDOWN_SECONDS", 45, minimum=1)
RESET_AUTHORIZATION_EXPIRY_MINUTES = _get_int("RESET_AUTHORIZATION_EXPIRY_MINUTES", 10, minimum=1)
AUTH_RESET_COOKIE_NAME = "naijaclip_reset_authorization"
EMAIL_VERIFICATION_EXPIRE_HOURS = _get_int("EMAIL_VERIFICATION_EXPIRE_HOURS", 24, minimum=1)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
ADMIN_NAME = os.getenv("ADMIN_NAME", "NaijaClip Admin")
