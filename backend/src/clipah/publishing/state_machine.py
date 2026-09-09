"""The closed Publication transition matrix and truthful cancellation boundary."""

from __future__ import annotations

from clipah.publishing.models import PublicationStatus, PublicationTransition


class IllegalPublicationTransitionError(Exception):
    """The requested move is absent from the approved lifecycle."""


class PublicationCancellationRejectedError(Exception):
    """The provider can no longer guarantee that external work will stop."""


_ALLOWED_TARGETS = {
    PublicationStatus.DRAFT: frozenset({PublicationStatus.AWAITING_APPROVAL}),
    PublicationStatus.AWAITING_APPROVAL: frozenset(
        {
            PublicationStatus.SCHEDULED,
            PublicationStatus.PREFLIGHTING,
            PublicationStatus.CANCELLED,
        }
    ),
    PublicationStatus.SCHEDULED: frozenset(
        {
            PublicationStatus.PREFLIGHTING,
            PublicationStatus.CANCELLED,
            PublicationStatus.RECONNECT_REQUIRED,
        }
    ),
    PublicationStatus.PREFLIGHTING: frozenset(
        {
            PublicationStatus.AWAITING_APPROVAL,
            PublicationStatus.TRANSFERRING,
            PublicationStatus.RETRYABLE_FAILED,
            PublicationStatus.RECONNECT_REQUIRED,
            PublicationStatus.PERMANENT_FAILED,
            PublicationStatus.CANCELLED,
        }
    ),
    PublicationStatus.TRANSFERRING: frozenset(
        {
            PublicationStatus.PROCESSING,
            PublicationStatus.RETRYABLE_FAILED,
            PublicationStatus.RECONNECT_REQUIRED,
            PublicationStatus.PERMANENT_FAILED,
        }
    ),
    PublicationStatus.PROCESSING: frozenset(
        {
            PublicationStatus.PUBLISHED,
            PublicationStatus.RETRYABLE_FAILED,
            PublicationStatus.RECONNECT_REQUIRED,
            PublicationStatus.PERMANENT_FAILED,
        }
    ),
    PublicationStatus.RETRYABLE_FAILED: frozenset(
        {PublicationStatus.PREFLIGHTING, PublicationStatus.CANCELLED}
    ),
    PublicationStatus.RECONNECT_REQUIRED: frozenset(
        {
            PublicationStatus.SCHEDULED,
            PublicationStatus.AWAITING_APPROVAL,
            PublicationStatus.CANCELLED,
        }
    ),
    PublicationStatus.PUBLISHED: frozenset(),
    PublicationStatus.PERMANENT_FAILED: frozenset(),
    PublicationStatus.CANCELLED: frozenset(),
}

_LOCALLY_CANCELLABLE = frozenset(
    {
        PublicationStatus.AWAITING_APPROVAL,
        PublicationStatus.SCHEDULED,
        PublicationStatus.PREFLIGHTING,
        PublicationStatus.RETRYABLE_FAILED,
        PublicationStatus.RECONNECT_REQUIRED,
    }
)


def transition(*, current: PublicationStatus, target: PublicationStatus) -> PublicationTransition:
    """Accept one documented edge or reject it without changing durable state."""
    if target not in _ALLOWED_TARGETS[current]:
        raise IllegalPublicationTransitionError(f"cannot move {current.value} to {target.value}")
    return PublicationTransition(previous=current, current=target)


def may_cancel(*, current: PublicationStatus, provider_cancellable: bool) -> bool:
    """Confirm cancellation is both locally valid and provider-truthful."""
    if current not in _LOCALLY_CANCELLABLE or not provider_cancellable:
        raise PublicationCancellationRejectedError(
            f"cancellation is not guaranteed from {current.value}"
        )
    return True
