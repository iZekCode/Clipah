"""Closed values for context warnings, hook strategies, and platform packaging.

Nothing here reaches a provider or a database on its own. These are the words the rest of
the package is allowed to use, so a warning type nobody can evaluate and a duration target
nobody can render cannot be expressed in the first place.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

#: The only duration targets a Variant may be asked for, in milliseconds. A request for
#: anything else is refused rather than rounded: a member who asks for 25 seconds and
#: silently receives 30 has been told something untrue about their own clip.
SUPPORTED_TARGET_DURATIONS_MS = (20_000, 30_000, 45_000, 60_000, 90_000)


class ContextWarningType(StrEnum):
    """Every way a proposed boundary is known to misrepresent what was said.

    Closed rather than free text, because an open vocabulary cannot be measured: the
    evaluation harness reports recall and precision per type, and a type nobody labeled
    would silently score as neither.
    """

    CUT_OFF_QUESTION = "cut_off_question"
    CUT_OFF_PAYOFF = "cut_off_payoff"
    MISSING_NEGATION = "missing_negation"
    MISSING_ATTRIBUTION = "missing_attribution"
    UNSUPPORTED_REFERENCE = "unsupported_reference"
    OMITTED_CAVEAT = "omitted_caveat"
    INCOMPLETE_LIST = "incomplete_list"
    CLAIM_NEEDS_SOURCE = "claim_needs_source"


class ContextWarningSeverity(StrEnum):
    """How much weight one warning carries when a Variant is being considered."""

    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class ContextWarning(BaseModel):
    """One evidenced observation about a proposed boundary.

    A warning names the words that prove it and, where a fix exists, the boundary that
    would resolve it. It never rewrites the transcript and never moves a boundary itself:
    the member decides, and the generator refuses only what is blocking.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: ContextWarningType
    severity: ContextWarningSeverity
    evidence_word_ids: tuple[str, ...] = Field(min_length=1)
    suggested_start_word_id: str | None = None
    suggested_end_word_id: str | None = None


class HookStrategy(StrEnum):
    """The deterministic opening policies a Variant may be built with."""

    COLD_OPEN = "cold_open"
    QUESTION_FIRST = "question_first"
    STATEMENT_FIRST = "statement_first"


class Platform(StrEnum):
    """The short-form destinations Clipah packages for, without publishing to any of them."""

    TIKTOK = "tiktok"
    INSTAGRAM_REELS = "instagram_reels"
    YOUTUBE_SHORTS = "youtube_shorts"


#: The severity at which a warning refuses a Variant outright.
BLOCKING_SEVERITY = ContextWarningSeverity.BLOCKING
