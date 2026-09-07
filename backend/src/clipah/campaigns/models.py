"""Closed values and strict shapes for campaign copy derived from an Edit Revision.

Nothing here reaches a provider or a database. These are the words the rest of the
package is allowed to use, so copy in a language nobody localized and a destination
nobody packages for cannot be expressed in the first place.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from clipah.variants.models import Platform


class CampaignLanguage(StrEnum):
    """The languages Clipah writes supporting copy in.

    Indonesian first, because that is who this product is for. The values are the ISO 639-1
    codes the transcript already records, so a Transcript's language selects copy without a
    translation table in between.
    """

    INDONESIAN = "id"
    ENGLISH = "en"


class CampaignWarningType(StrEnum):
    """Every caveat copy can carry from the clip it was derived from.

    Closed rather than free text, because a member acts on the kind of caveat rather than
    on its wording, and a type nobody can act on is a sentence nobody reads.
    """

    CONTEXT_DEPENDENT = "context_dependent"
    CLAIM_NEEDS_SOURCE = "claim_needs_source"


class CampaignModel(BaseModel):
    """The shared strictness every part of a Campaign Output inherits."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class CampaignWarning(CampaignModel):
    """One caveat a member reads beside the copy, never instead of it."""

    type: CampaignWarningType
    detail: Annotated[str, Field(min_length=1, max_length=400)]


class ThumbnailBrief(CampaignModel):
    """What a thumbnail has to carry, written for the person who will make it.

    Clipah does not draw thumbnails. It says what text belongs on one and how the frame
    should be directed, so a designer starts from the moment rather than from nothing.
    """

    text: Annotated[str, Field(min_length=1, max_length=40)]
    visual_direction: Annotated[str, Field(min_length=1, max_length=400)]
    avoid: tuple[Annotated[str, Field(min_length=1, max_length=120)], ...] = ()


class CampaignModelMetadata(CampaignModel):
    """What produced this copy, so a member can tell how much to trust it."""

    generator: Annotated[str, Field(min_length=1, max_length=120)]
    version: Annotated[int, Field(gt=0)]
    # Version 1 derives copy from the transcript and the analysis alone. A language model
    # would be a provider behind this same field, and a member has to be able to tell the
    # two apart without asking anybody.
    deterministic: bool
    provider: str | None = None
    model: str | None = None


class CampaignOutputDraft(CampaignModel):
    """One complete piece of supporting copy, for one destination in one language."""

    platform: Platform
    language: CampaignLanguage
    title: Annotated[str, Field(min_length=1, max_length=200)]
    post_copy: Annotated[str, Field(min_length=1, max_length=4_000)]
    cta: Annotated[str, Field(min_length=1, max_length=200)]
    hashtags: tuple[Annotated[str, Field(min_length=2, max_length=60)], ...]
    thumbnail_brief: ThumbnailBrief
    warnings: tuple[CampaignWarning, ...]
    model_metadata: CampaignModelMetadata
