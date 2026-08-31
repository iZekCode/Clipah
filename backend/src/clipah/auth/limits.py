"""Sliding-window request limits enforced across every Clipah API process."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from math import ceil
from typing import Protocol

from redis import Redis

KEY_PREFIX = "clipah:ratelimit"

# One atomic decision: drop everything older than the window, count what is left, and
# only then spend a slot. Splitting these steps across round trips would let two
# processes admit the same final request.
_SLIDING_WINDOW_SCRIPT = """
local entries = KEYS[1]
local sequence = KEYS[2]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', entries, '-inf', now_ms - window_ms)
local used = redis.call('ZCARD', entries)
if used >= limit then
    local oldest = redis.call('ZRANGE', entries, 0, 0, 'WITHSCORES')
    local retry_after_ms = (tonumber(oldest[2]) + window_ms) - now_ms
    if retry_after_ms < 0 then
        retry_after_ms = 0
    end
    return {0, 0, retry_after_ms}
end

local member = redis.call('INCR', sequence)
redis.call('ZADD', entries, now_ms, member)
redis.call('PEXPIRE', entries, window_ms)
redis.call('PEXPIRE', sequence, window_ms)
return {1, limit - used - 1, 0}
"""


class RateLimitExceededError(Exception):
    """Raised when a subject has spent its allowance for one bucket."""

    def __init__(self, bucket: str, *, retry_after: timedelta) -> None:
        """Carry the bucket and the wait a caller must honour, and nothing else."""
        super().__init__(str(bucket))
        self.bucket = bucket
        self.retry_after = retry_after


class RateLimitBucket(StrEnum):
    """Allowances that are counted separately even for the same subject."""

    READ = "read"
    WRITE = "write"
    ANALYSIS = "analysis"
    SOCIAL_PUBLISH = "social_publish"


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """The outcome of one admission question, including how long a refusal lasts."""

    allowed: bool
    remaining: int
    retry_after: timedelta

    def retry_after_seconds(self) -> int:
        """Round a refusal up to the whole second an HTTP ``Retry-After`` header carries."""
        return max(ceil(self.retry_after.total_seconds()), 1)


class RateLimiter(Protocol):
    """The limit boundary callers depend on instead of any Redis detail."""

    def check(
        self, *, subject: str, bucket: str, limit: int, window: timedelta
    ) -> RateLimitDecision:
        """Spend one slot for a subject, or report how long the caller must wait."""
        ...


class RedisRateLimiter:
    """A shared sliding-window limiter evaluated inside one atomic Redis script."""

    def __init__(
        self, client: Redis, *, now: Callable[[], datetime], key_prefix: str = KEY_PREFIX
    ) -> None:
        """Bind one limiter to a Redis connection and the clock its windows are measured on."""
        self._client = client
        self._now = now
        self._key_prefix = key_prefix
        self._script = client.register_script(_SLIDING_WINDOW_SCRIPT)

    def check(
        self, *, subject: str, bucket: str, limit: int, window: timedelta
    ) -> RateLimitDecision:
        """Spend one slot for a subject, or report how long the caller must wait."""
        entries = f"{self._key_prefix}:{bucket}:{subject}"
        allowed, remaining, retry_after_ms = self._script(
            keys=[entries, f"{entries}:sequence"],
            args=[
                int(self._now().timestamp() * 1000),
                int(window.total_seconds() * 1000),
                limit,
            ],
        )
        return RateLimitDecision(
            allowed=bool(allowed),
            remaining=int(remaining),
            retry_after=timedelta(milliseconds=int(retry_after_ms)),
        )
