"""Derive supporting copy from one approved clip, deterministically and without invention.

Everything a Campaign Output asserts comes from two places: the clip's own transcript and
the analysis that chose the moment. The only Clipah-authored words are the localized
scaffolding published in `localization.py` — the labels, the call to action, the thumbnail
direction — which is what makes "nothing was invented here" a property somebody can check
rather than a promise somebody made.

A quote is never translated. Copy for an English audience still quotes an Indonesian
speaker in Indonesian, because a translated quote is no longer evidence of what was said.

Nothing in this module reads a database, a clock, or a provider. Version 1 uses no
language model at all, and says so in every output's model metadata.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from clipah.campaigns.localization import (
    call_to_action,
    terminology,
    thumbnail_direction,
    warning_text,
)
from clipah.campaigns.models import (
    CampaignLanguage,
    CampaignModelMetadata,
    CampaignOutputDraft,
    CampaignWarning,
    CampaignWarningType,
    ThumbnailBrief,
)
from clipah.variants.models import Platform
from clipah.variants.packaging import packaging_for

GENERATOR_NAME = "clipah-deterministic-campaign"
GENERATOR_VERSION = 1
MAX_HASHTAGS = 5
MAX_THUMBNAIL_TEXT = 40


@dataclass(frozen=True, slots=True)
class ClipSummary:
    """One approved moment, as the transcript and the analysis already describe it.

    ``quote`` is the clip's own words, in the language they were spoken in, and
    ``quote_language`` records that language so nothing downstream mistakes it for the
    language the copy is written in.
    """

    hook: str
    payoff: str
    reason: str
    category: str
    tags: tuple[str, ...]
    quote: str
    quote_language: str
    duration_ms: int
    context_warnings: tuple[str, ...] = ()
    # The phrases a Brand Kit forbids, so copy that would repeat one is flagged before a
    # member posts it rather than after.
    claim_phrases_at_risk: tuple[str, ...] = ()
    # What the brand excludes from its pictures, carried into the thumbnail brief so a
    # designer is told before they draw rather than after they submit.
    visual_exclusions: tuple[str, ...] = field(default=())


def generate_campaign_outputs(
    clip: ClipSummary,
    *,
    platforms: Sequence[Platform],
    languages: Sequence[CampaignLanguage],
) -> tuple[CampaignOutputDraft, ...]:
    """Write one piece of copy for each destination and language that was asked for."""
    if not platforms or not languages:
        raise ValueError("campaign copy needs at least one platform and one language")
    return tuple(
        _draft(clip, platform=platform, language=language)
        for platform in platforms
        for language in languages
    )


def _draft(
    clip: ClipSummary, *, platform: Platform, language: CampaignLanguage
) -> CampaignOutputDraft:
    """Write one complete piece of copy for one destination in one language."""
    packaging = packaging_for(platform)
    return CampaignOutputDraft(
        platform=platform,
        language=language,
        title=_trimmed(clip.hook, packaging.max_title_characters),
        post_copy=_post_copy(clip, language=language),
        cta=call_to_action(language, platform),
        hashtags=_hashtags(clip),
        thumbnail_brief=ThumbnailBrief(
            text=_trimmed(clip.hook, MAX_THUMBNAIL_TEXT),
            visual_direction=thumbnail_direction(language),
            avoid=clip.visual_exclusions,
        ),
        warnings=_warnings(clip, language),
        model_metadata=CampaignModelMetadata(
            generator=GENERATOR_NAME,
            version=GENERATOR_VERSION,
            deterministic=True,
            provider=None,
            model=None,
        ),
    )


def _post_copy(clip: ClipSummary, *, language: CampaignLanguage) -> str:
    """Assemble the post around the quote, without rewriting a word of it."""
    words = terminology(language)
    return "\n\n".join(
        (
            clip.hook,
            f"{words['quote_label']}: “{clip.quote}”",
            f"{words['why_label']}: {clip.reason}",
            f"{words['takeaway_label']}: {clip.payoff}",
        )
    )


def _hashtags(clip: ClipSummary) -> tuple[str, ...]:
    """Turn the clip's own tags, and then its category, into usable hashtags."""
    hashtags: list[str] = []
    for term in (*clip.tags, clip.category):
        candidate = _hashtag(term)
        if candidate is not None and candidate not in hashtags:
            hashtags.append(candidate)
        if len(hashtags) == MAX_HASHTAGS:
            break
    return tuple(hashtags)


def _hashtag(term: str) -> str | None:
    """Reduce one term to something a platform will actually accept as a hashtag."""
    letters = re.sub(r"[^0-9A-Za-z]+", "", term)
    return f"#{letters.lower()}" if letters else None


def _warnings(clip: ClipSummary, language: CampaignLanguage) -> tuple[CampaignWarning, ...]:
    """Carry the clip's caveats into the copy, in the language the copy is written in."""
    warnings: list[CampaignWarning] = []
    if clip.context_warnings:
        warnings.append(
            CampaignWarning(
                type=CampaignWarningType.CONTEXT_DEPENDENT,
                detail=warning_text(language, "context_warning"),
            )
        )
    said = " ".join((clip.hook, clip.payoff, clip.reason, clip.quote))
    for phrase in clip.claim_phrases_at_risk:
        if _mentions(said, phrase):
            warnings.append(
                CampaignWarning(
                    type=CampaignWarningType.CLAIM_NEEDS_SOURCE,
                    detail=f"{warning_text(language, 'claim_warning')} {phrase}",
                )
            )
    return tuple(warnings)


def _trimmed(text: str, limit: int) -> str:
    """Shorten text to what a destination shows, cutting between words rather than inside one."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[:limit]
    spaced = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return spaced.rstrip()


def _mentions(text: str, phrase: str) -> bool:
    """Report whether one phrase is said in this text, as a phrase rather than a fragment."""
    pattern = r"\s+".join(re.escape(part) for part in phrase.split())
    return re.search(rf"(?<!\w){pattern}(?!\w)", text, flags=re.IGNORECASE) is not None
