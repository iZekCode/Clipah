"""Wakeups for job event subscribers, with polling as the fallback that always works."""

from __future__ import annotations

import time
from typing import Protocol, cast
from uuid import UUID

from redis import Redis
from redis.client import PubSub

CHANNEL_PREFIX = "clipah:jobs"


def event_channel(*, workspace_id: UUID, job_id: UUID) -> str:
    """Name the channel one job's subscribers listen on."""
    return f"{CHANNEL_PREFIX}:{workspace_id}:{job_id}"


class JobEventSubscription(Protocol):
    """One subscriber's open interest in a single job's history."""

    def wait(self, timeout: float) -> None:
        """Block until this job records something new or ``timeout`` seconds pass."""

    def close(self) -> None:
        """Release whatever the subscription holds."""


class JobEventNotifier(Protocol):
    """Wake subscribers when a job appends history."""

    def notify(self, *, workspace_id: UUID, job_id: UUID) -> None:
        """Announce that this job has recorded something new."""

    def subscribe(self, *, workspace_id: UUID, job_id: UUID) -> JobEventSubscription:
        """Open one subscriber's interest in a single job."""


class PollingSubscription:
    """Wait out the polling interval instead of listening for a push."""

    def wait(self, timeout: float) -> None:
        """Sleep for one polling interval."""
        time.sleep(timeout)

    def close(self) -> None:
        """Release nothing, because nothing was held."""


class PollingJobEventNotifier:
    """Serve deployments configured without Redis.

    Correctness never depends on a wakeup arriving: every subscriber re-reads the
    durable history after waiting, so this fallback is slower but never wrong.
    """

    def notify(self, *, workspace_id: UUID, job_id: UUID) -> None:
        """Do nothing, because nobody is listening for a push."""
        del workspace_id, job_id

    def subscribe(self, *, workspace_id: UUID, job_id: UUID) -> JobEventSubscription:
        """Hand back a subscription that only ever waits out the polling interval."""
        del workspace_id, job_id
        return PollingSubscription()


class RedisSubscription:
    """One Redis pub/sub channel held open for the life of a stream."""

    def __init__(self, redis: Redis, channel: str) -> None:
        """Subscribe to one job's channel immediately, before any history is read."""
        # ``pubsub`` carries no annotation of its own, so name the type it returns here.
        self._pubsub = cast(PubSub, redis.pubsub(ignore_subscribe_messages=True))
        self._pubsub.subscribe(channel)

    def wait(self, timeout: float) -> None:
        """Await one published wakeup, giving up after ``timeout`` seconds."""
        self._pubsub.get_message(timeout=timeout)

    def close(self) -> None:
        """Unsubscribe and return the connection to the pool."""
        self._pubsub.close()


class RedisJobEventNotifier:
    """Publish and await job wakeups over Redis pub/sub."""

    def __init__(self, redis: Redis) -> None:
        """Bind the notifier to one Redis connection pool shared by this process."""
        self._redis = redis

    def notify(self, *, workspace_id: UUID, job_id: UUID) -> None:
        """Publish one wakeup for every subscriber currently following this job."""
        self._redis.publish(event_channel(workspace_id=workspace_id, job_id=job_id), "1")

    def subscribe(self, *, workspace_id: UUID, job_id: UUID) -> JobEventSubscription:
        """Open one pub/sub channel for this job before its history is replayed."""
        return RedisSubscription(
            self._redis, event_channel(workspace_id=workspace_id, job_id=job_id)
        )
