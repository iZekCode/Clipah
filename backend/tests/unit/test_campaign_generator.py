"""Campaign copy derived from one approved clip, in Indonesian and in English.

Two rules decide everything here. Nothing is invented: every fact in the copy comes from
the clip's own transcript and the analysis that chose it. And nothing anybody said is
translated: a quote is evidence, and evidence rewritten in another language is no longer
what the speaker said.

Nothing in this suite reads a database, a clock, or a provider.
"""

from __future__ import annotations

from typing import Any

import pytest

from clipah.campaigns.generator import ClipSummary, generate_campaign_outputs
from clipah.campaigns.localization import terminology
from clipah.campaigns.models import CampaignLanguage, CampaignWarningType
from clipah.variants.models import Platform

INDONESIAN_QUOTE = "Formulir pendaftaran itu yang bikin orang berhenti di tengah jalan"


def clip(**overrides: Any) -> ClipSummary:
    """One reviewed moment, as the analysis and the transcript describe it."""
    values: dict[str, Any] = {
        "hook": "Formulir itu batasnya",
        "payoff": "Menghapusnya melipatgandakan aktivasi",
        "reason": "Satu keputusan lengkap dari awal sampai akhir",
        "category": "insight",
        "tags": ("produk", "aktivasi"),
        "quote": INDONESIAN_QUOTE,
        "quote_language": "id",
        "duration_ms": 42_000,
        "context_warnings": (),
        "claim_phrases_at_risk": (),
    }
    values.update(overrides)
    return ClipSummary(**values)


def only(platform: Platform, language: CampaignLanguage, **overrides: Any) -> Any:
    """Generate exactly one output, for the one destination this assertion is about."""
    drafts = generate_campaign_outputs(
        clip(**overrides), platforms=(platform,), languages=(language,)
    )
    assert len(drafts) == 1
    return drafts[0]


@pytest.mark.unit
def test_one_output_is_produced_for_each_destination_and_language() -> None:
    """A member packages one moment for several places at once, or not at all."""
    drafts = generate_campaign_outputs(
        clip(),
        platforms=(Platform.TIKTOK, Platform.YOUTUBE_SHORTS),
        languages=(CampaignLanguage.INDONESIAN, CampaignLanguage.ENGLISH),
    )

    assert len(drafts) == 4
    assert {(draft.platform, draft.language) for draft in drafts} == {
        (Platform.TIKTOK, CampaignLanguage.INDONESIAN),
        (Platform.TIKTOK, CampaignLanguage.ENGLISH),
        (Platform.YOUTUBE_SHORTS, CampaignLanguage.INDONESIAN),
        (Platform.YOUTUBE_SHORTS, CampaignLanguage.ENGLISH),
    }


@pytest.mark.unit
def test_every_output_carries_the_whole_contract() -> None:
    """A half-written post is one a member has to finish somewhere else."""
    draft = only(Platform.TIKTOK, CampaignLanguage.INDONESIAN)

    assert draft.title
    assert draft.post_copy
    assert draft.cta
    assert draft.hashtags
    assert draft.thumbnail_brief.text
    assert draft.thumbnail_brief.visual_direction
    assert draft.model_metadata.generator
    assert draft.model_metadata.deterministic is True


@pytest.mark.unit
def test_a_title_is_trimmed_to_what_the_destination_actually_shows() -> None:
    """A title cut off mid-word by a platform is a title nobody wrote."""
    long_hook = "Formulir pendaftaran panjang " * 12
    draft = only(Platform.YOUTUBE_SHORTS, CampaignLanguage.INDONESIAN, hook=long_hook)

    assert len(draft.title) <= 100
    assert not draft.title.endswith(" ")


@pytest.mark.unit
def test_the_speaker_s_own_words_are_quoted_exactly_in_every_language() -> None:
    """A quote is evidence; a translated quote is somebody else's sentence."""
    indonesian = only(Platform.TIKTOK, CampaignLanguage.INDONESIAN)
    english = only(Platform.TIKTOK, CampaignLanguage.ENGLISH)

    assert INDONESIAN_QUOTE in indonesian.post_copy
    assert INDONESIAN_QUOTE in english.post_copy


@pytest.mark.unit
def test_the_scaffolding_around_a_quote_is_written_in_the_asked_for_language() -> None:
    """Copy a member posts to an English audience reads as English around the quote."""
    indonesian = only(Platform.TIKTOK, CampaignLanguage.INDONESIAN)
    english = only(Platform.TIKTOK, CampaignLanguage.ENGLISH)

    assert indonesian.cta != english.cta
    assert indonesian.post_copy != english.post_copy


@pytest.mark.unit
def test_copy_says_only_what_the_clip_and_the_analysis_already_said() -> None:
    """An invented benefit is a claim nobody in the clip ever made."""
    draft = only(Platform.TIKTOK, CampaignLanguage.INDONESIAN)
    evidence = " ".join(
        (
            clip().hook,
            clip().payoff,
            clip().reason,
            clip().quote,
            " ".join(clip().tags),
            " ".join(terminology(CampaignLanguage.INDONESIAN).values()),
        )
    ).lower()

    for word in _words(draft.post_copy):
        assert word in evidence


@pytest.mark.unit
def test_hashtags_come_from_the_clip_s_own_tags_and_are_usable_as_written() -> None:
    """A hashtag with a space in it is not a hashtag."""
    draft = only(Platform.INSTAGRAM_REELS, CampaignLanguage.INDONESIAN, tags=("produk digital",))

    assert draft.hashtags
    for hashtag in draft.hashtags:
        assert hashtag.startswith("#")
        assert " " not in hashtag
    assert len(set(draft.hashtags)) == len(draft.hashtags)


@pytest.mark.unit
def test_a_clip_with_no_tags_still_produces_hashtags_from_its_category() -> None:
    """Copy that ships without hashtags is copy a member finishes by hand."""
    draft = only(Platform.TIKTOK, CampaignLanguage.ENGLISH, tags=())

    assert draft.hashtags


@pytest.mark.unit
def test_a_tag_with_no_letters_in_it_produces_no_hashtag() -> None:
    """ "#" on its own is punctuation a member would have to delete by hand."""
    draft = only(Platform.TIKTOK, CampaignLanguage.ENGLISH, tags=("!!!", "produk"))

    assert draft.hashtags == ("#produk", "#insight")


@pytest.mark.unit
def test_no_more_hashtags_are_offered_than_a_caption_can_carry() -> None:
    """A wall of hashtags is a wall a member has to trim before they post."""
    draft = only(
        Platform.TIKTOK,
        CampaignLanguage.ENGLISH,
        tags=("satu", "dua", "tiga", "empat", "lima", "enam", "tujuh"),
    )

    assert len(draft.hashtags) == 5


@pytest.mark.unit
def test_the_thumbnail_brief_directs_a_person_rather_than_writing_the_image() -> None:
    """Clipah does not draw the thumbnail; it says what the thumbnail has to carry."""
    draft = only(Platform.YOUTUBE_SHORTS, CampaignLanguage.INDONESIAN)

    assert len(draft.thumbnail_brief.text) <= 40
    assert draft.thumbnail_brief.visual_direction


@pytest.mark.unit
def test_a_context_warning_on_the_clip_is_carried_into_the_copy() -> None:
    """Copy and caveat are read together, or the caveat may as well not exist."""
    draft = only(
        Platform.TIKTOK,
        CampaignLanguage.INDONESIAN,
        context_warnings=("missing_attribution",),
    )

    assert CampaignWarningType.CONTEXT_DEPENDENT in {warning.type for warning in draft.warnings}


@pytest.mark.unit
def test_a_claim_the_brand_may_not_make_is_reported_beside_the_copy() -> None:
    """A member sees the sentence their lawyers refused before they post it."""
    draft = only(
        Platform.TIKTOK,
        CampaignLanguage.INDONESIAN,
        hook="Dijamin untung setiap bulan",
        claim_phrases_at_risk=("dijamin untung",),
    )

    assert CampaignWarningType.CLAIM_NEEDS_SOURCE in {warning.type for warning in draft.warnings}


@pytest.mark.unit
def test_copy_for_the_same_clip_is_the_same_copy_every_time() -> None:
    """A member comparing two runs is comparing the clip, not the weather."""
    first = generate_campaign_outputs(
        clip(), platforms=(Platform.TIKTOK,), languages=(CampaignLanguage.ENGLISH,)
    )
    second = generate_campaign_outputs(
        clip(), platforms=(Platform.TIKTOK,), languages=(CampaignLanguage.ENGLISH,)
    )

    assert first == second


@pytest.mark.unit
def test_a_request_naming_no_destination_or_no_language_is_refused() -> None:
    """Copy for nowhere in no language is not a smaller request; it is not one at all."""
    with pytest.raises(ValueError):
        generate_campaign_outputs(clip(), platforms=(), languages=(CampaignLanguage.ENGLISH,))
    with pytest.raises(ValueError):
        generate_campaign_outputs(clip(), platforms=(Platform.TIKTOK,), languages=())


@pytest.mark.unit
def test_a_draft_cannot_be_edited_after_it_has_been_generated() -> None:
    """Stored copy is what a member read; a mutable draft could stop being that."""
    draft = only(Platform.TIKTOK, CampaignLanguage.ENGLISH)

    with pytest.raises(ValueError):
        draft.title = "Something else"  # type: ignore[misc]


@pytest.mark.unit
def test_both_languages_publish_the_terms_this_product_uses() -> None:
    """A dictionary nobody can read is a dictionary nobody can correct."""
    for language in CampaignLanguage:
        assert terminology(language)
        assert all(term and translated for term, translated in terminology(language).items())


def _words(text: str) -> tuple[str, ...]:
    """Reduce copy to its bare words, so evidence can be checked word by word."""
    cleaned = "".join(
        character if character.isalnum() or character.isspace() else " " for character in text
    )
    return tuple(word for word in cleaned.lower().split() if word)
