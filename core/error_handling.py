from __future__ import annotations


def sanitize_error_message(raw_error: object | None, fallback: str = "Something went wrong. Please try again.") -> str:
    """Return a safe message for users and hide raw backend/AI provider details."""
    if raw_error is None:
        return fallback

    message = str(raw_error).strip()
    if not message:
        return fallback

    lowered = message.lower()

    if any(token in lowered for token in [
        "failed to validate json",
        "json_validate_failed",
        "failed_generation",
        "invalid_request_error",
        "groq",
        "api key",
        "rate limit",
        "quota",
        "openai",
        "anthropic",
        "llm",
        "model response",
    ]):
        return "We couldn't process this video right now. Please try again."

    if any(token in lowered for token in [
        "traceback",
        "internal server error",
        "exception",
        "sqlalchemy",
        "database",
    ]):
        return "Something went wrong on our side. Please try again in a moment."

    return fallback
