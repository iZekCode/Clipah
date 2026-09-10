"""Contract tests for the durable Publication lifecycle promised to provider workers."""

from __future__ import annotations

from itertools import product

import pytest

from clipah.publishing.models import PublicationStatus
from clipah.publishing.state_machine import (
    IllegalPublicationTransitionError,
    PublicationCancellationRejectedError,
    may_cancel,
    transition,
)

pytestmark = pytest.mark.unit


ALLOWED_TRANSITIONS = frozenset(
    {
        (PublicationStatus.DRAFT, PublicationStatus.AWAITING_APPROVAL),
        (PublicationStatus.AWAITING_APPROVAL, PublicationStatus.SCHEDULED),
        (PublicationStatus.AWAITING_APPROVAL, PublicationStatus.PREFLIGHTING),
        (PublicationStatus.AWAITING_APPROVAL, PublicationStatus.CANCELLED),
        (PublicationStatus.SCHEDULED, PublicationStatus.PREFLIGHTING),
        (PublicationStatus.SCHEDULED, PublicationStatus.PUBLISHED),
        (PublicationStatus.SCHEDULED, PublicationStatus.CANCELLED),
        (PublicationStatus.SCHEDULED, PublicationStatus.RECONNECT_REQUIRED),
        (PublicationStatus.PREFLIGHTING, PublicationStatus.AWAITING_APPROVAL),
        (PublicationStatus.PREFLIGHTING, PublicationStatus.TRANSFERRING),
        (PublicationStatus.PREFLIGHTING, PublicationStatus.RETRYABLE_FAILED),
        (PublicationStatus.PREFLIGHTING, PublicationStatus.RECONNECT_REQUIRED),
        (PublicationStatus.PREFLIGHTING, PublicationStatus.PERMANENT_FAILED),
        (PublicationStatus.PREFLIGHTING, PublicationStatus.CANCELLED),
        (PublicationStatus.TRANSFERRING, PublicationStatus.PROCESSING),
        (PublicationStatus.TRANSFERRING, PublicationStatus.RETRYABLE_FAILED),
        (PublicationStatus.TRANSFERRING, PublicationStatus.RECONNECT_REQUIRED),
        (PublicationStatus.TRANSFERRING, PublicationStatus.PERMANENT_FAILED),
        (PublicationStatus.PROCESSING, PublicationStatus.SCHEDULED),
        (PublicationStatus.PROCESSING, PublicationStatus.PUBLISHED),
        (PublicationStatus.PROCESSING, PublicationStatus.RETRYABLE_FAILED),
        (PublicationStatus.PROCESSING, PublicationStatus.RECONNECT_REQUIRED),
        (PublicationStatus.PROCESSING, PublicationStatus.PERMANENT_FAILED),
        (PublicationStatus.RETRYABLE_FAILED, PublicationStatus.PREFLIGHTING),
        (PublicationStatus.RETRYABLE_FAILED, PublicationStatus.CANCELLED),
        (PublicationStatus.RECONNECT_REQUIRED, PublicationStatus.SCHEDULED),
        (PublicationStatus.RECONNECT_REQUIRED, PublicationStatus.AWAITING_APPROVAL),
        (PublicationStatus.RECONNECT_REQUIRED, PublicationStatus.CANCELLED),
    }
)


@pytest.mark.parametrize(("current", "target"), sorted(ALLOWED_TRANSITIONS))
def test_documented_publication_transition_is_permitted(
    current: PublicationStatus, target: PublicationStatus
) -> None:
    """Every documented recovery edge must remain available to durable workers."""
    result = transition(current=current, target=target)

    assert result.previous is current
    assert result.current is target


@pytest.mark.parametrize(
    ("current", "target"),
    [pair for pair in product(PublicationStatus, repeat=2) if pair not in ALLOWED_TRANSITIONS],
)
def test_undocumented_publication_transition_is_rejected(
    current: PublicationStatus, target: PublicationStatus
) -> None:
    """No route or worker may invent a lifecycle edge outside the approved matrix."""
    with pytest.raises(IllegalPublicationTransitionError):
        transition(current=current, target=target)


@pytest.mark.parametrize(
    "status",
    [
        PublicationStatus.PUBLISHED,
        PublicationStatus.PERMANENT_FAILED,
        PublicationStatus.CANCELLED,
    ],
)
def test_terminal_publication_cannot_leave_history(status: PublicationStatus) -> None:
    """A terminal destination must never be rolled back because a sibling failed."""
    for target in PublicationStatus:
        with pytest.raises(IllegalPublicationTransitionError):
            transition(current=status, target=target)


@pytest.mark.parametrize(
    "status",
    [
        PublicationStatus.AWAITING_APPROVAL,
        PublicationStatus.SCHEDULED,
        PublicationStatus.PREFLIGHTING,
        PublicationStatus.RETRYABLE_FAILED,
        PublicationStatus.RECONNECT_REQUIRED,
    ],
)
def test_cancellable_publication_reports_truthful_local_cancellation(
    status: PublicationStatus,
) -> None:
    """Work with no irreversible provider effect may truthfully become cancelled."""
    assert may_cancel(current=status, provider_cancellable=True) is True


@pytest.mark.parametrize(
    "status",
    [
        PublicationStatus.TRANSFERRING,
        PublicationStatus.PROCESSING,
        PublicationStatus.PUBLISHED,
    ],
)
def test_late_cancellation_is_rejected_when_provider_cannot_guarantee_it(
    status: PublicationStatus,
) -> None:
    """Clipah must report provider truth instead of displaying false local success."""
    with pytest.raises(PublicationCancellationRejectedError):
        may_cancel(current=status, provider_cancellable=False)
