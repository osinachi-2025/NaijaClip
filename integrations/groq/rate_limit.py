from __future__ import annotations

from typing import Any

from redis import Redis

from core.config import (
    GROQ_RATE_LIMIT_KEY_PREFIX,
    GROQ_RATE_LIMIT_REDIS_URL,
    GROQ_RATE_LIMIT_REQUESTS_PER_MINUTE,
    GROQ_RATE_LIMIT_TOKENS_PER_MINUTE,
)


_ACQUIRE_SCRIPT = """
local time = redis.call('TIME')
local now = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)
local cooldown = redis.call('PTTL', KEYS[3])
if cooldown > 0 then
    return {0, cooldown}
end

local function refill(key, capacity, rate_per_ms)
    local values = redis.call('HMGET', key, 'tokens', 'updated_at')
    local tokens = tonumber(values[1]) or capacity
    local updated_at = tonumber(values[2]) or now
    tokens = math.min(capacity, tokens + math.max(0, now - updated_at) * rate_per_ms)
    return tokens
end

local requests = refill(KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[1]) / 60000)
local tokens = refill(KEYS[2], tonumber(ARGV[2]), tonumber(ARGV[2]) / 60000)
local request_cost = 1
local token_cost = tonumber(ARGV[3])
if requests < request_cost or tokens < token_cost then
    local request_wait = math.max(0, request_cost - requests) / (tonumber(ARGV[1]) / 60000)
    local token_wait = math.max(0, token_cost - tokens) / (tonumber(ARGV[2]) / 60000)
    return {0, math.ceil(math.max(request_wait, token_wait))}
end

redis.call('HMSET', KEYS[1], 'tokens', requests - request_cost, 'updated_at', now)
redis.call('HMSET', KEYS[2], 'tokens', tokens - token_cost, 'updated_at', now)
redis.call('PEXPIRE', KEYS[1], 120000)
redis.call('PEXPIRE', KEYS[2], 120000)
return {1, 0}
"""

_COOLDOWN_SCRIPT = """
local current = redis.call('PTTL', KEYS[1])
local requested = tonumber(ARGV[1])
if current < requested then
    redis.call('SET', KEYS[1], '1', 'PX', requested)
end
return math.max(current, requested)
"""


class GroqRateLimiter:
    """A shared Redis token bucket for Groq requests and estimated tokens."""

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        model: str,
        requests_per_minute: int = GROQ_RATE_LIMIT_REQUESTS_PER_MINUTE,
        tokens_per_minute: int = GROQ_RATE_LIMIT_TOKENS_PER_MINUTE,
    ) -> None:
        self.redis = redis_client or Redis.from_url(
            GROQ_RATE_LIMIT_REDIS_URL,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self.prefix = f"{GROQ_RATE_LIMIT_KEY_PREFIX}:{model}"
        self.request_key = f"{self.prefix}:requests"
        self.token_key = f"{self.prefix}:tokens"
        self.cooldown_key = f"{self.prefix}:cooldown"

    def acquire(self, estimated_tokens: int) -> float:
        if estimated_tokens > self.tokens_per_minute:
            raise GroqRateLimitConfigurationError(
                "Groq rate limiter configuration allows fewer tokens than one estimated request"
            )
        allowed, wait_ms = self.redis.eval(
            _ACQUIRE_SCRIPT,
            3,
            self.request_key,
            self.token_key,
            self.cooldown_key,
            self.requests_per_minute,
            self.tokens_per_minute,
            max(1, estimated_tokens),
        )
        return 0.0 if allowed else max(0.001, float(wait_ms) / 1000)

    def defer_all_workers(self, delay_seconds: float) -> None:
        self.redis.eval(
            _COOLDOWN_SCRIPT,
            1,
            self.cooldown_key,
            max(1, round(delay_seconds * 1000)),
        )


class GroqRateLimitConfigurationError(RuntimeError):
    pass
