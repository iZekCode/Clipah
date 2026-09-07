"""The words Clipah writes around a quote, in each language it writes copy in.

This is a dictionary, not a translator. Clipah localizes its own scaffolding — the labels,
the calls to action, the thumbnail direction — and never the words somebody said. A quote
translated into another language is no longer evidence of what the speaker said, and copy
built on a translated quote asserts something nobody in the clip asserted.

Indonesian is first because that is who this product is for.
"""

from __future__ import annotations

from collections.abc import Mapping

from clipah.campaigns.models import CampaignLanguage
from clipah.variants.models import Platform

#: The scaffolding each language writes around a quote, keyed by what it is for. The
#: values are the only Clipah-authored words that ever reach a member's post copy, which
#: is what makes "nothing was invented" a property somebody can check.
_TERMINOLOGY: Mapping[CampaignLanguage, Mapping[str, str]] = {
    CampaignLanguage.INDONESIAN: {
        "quote_label": "Kutipan",
        "why_label": "Kenapa penting",
        "takeaway_label": "Intinya",
    },
    CampaignLanguage.ENGLISH: {
        "quote_label": "Quote",
        "why_label": "Why it matters",
        "takeaway_label": "The takeaway",
    },
}

#: What each destination asks a viewer to do, in each language. A call to action is the
#: one sentence a platform's own conventions decide, so it is published per platform
#: rather than derived from the clip.
_CALLS_TO_ACTION: Mapping[CampaignLanguage, Mapping[Platform, str]] = {
    CampaignLanguage.INDONESIAN: {
        Platform.TIKTOK: "Ikuti untuk klip lainnya.",
        Platform.INSTAGRAM_REELS: "Simpan Reel ini dan bagikan ke tim kamu.",
        Platform.YOUTUBE_SHORTS: "Tonton episode lengkapnya di kanal ini.",
    },
    CampaignLanguage.ENGLISH: {
        Platform.TIKTOK: "Follow for more clips like this.",
        Platform.INSTAGRAM_REELS: "Save this Reel and send it to your team.",
        Platform.YOUTUBE_SHORTS: "Watch the full episode on this channel.",
    },
}

#: The caveats shown beside copy, in the language the copy is written in. These are kept
#: apart from the scaffolding above because they are never part of a post: a caveat is
#: something a member reads, not something they publish.
_WARNINGS: Mapping[CampaignLanguage, Mapping[str, str]] = {
    CampaignLanguage.INDONESIAN: {
        "context_warning": (
            "Momen ini punya catatan konteks dari peninjauan. Baca dulu sebelum copy ini dipakai."
        ),
        "claim_warning": "Copy ini mengulang klaim yang butuh sumber:",
    },
    CampaignLanguage.ENGLISH: {
        "context_warning": (
            "This moment carries context warnings from review. Read them before this copy is used."
        ),
        "claim_warning": "This copy repeats a claim that needs a source:",
    },
}

#: How a thumbnail should be directed, in each language. This is guidance for a person,
#: never an image Clipah draws.
_THUMBNAIL_DIRECTION: Mapping[CampaignLanguage, str] = {
    CampaignLanguage.INDONESIAN: (
        "Wajah pembicara di sisi kanan, teks pendek di kiri atas, kontras tinggi."
    ),
    CampaignLanguage.ENGLISH: (
        "Speaker's face on the right, short text at the upper left, high contrast."
    ),
}


def terminology(language: CampaignLanguage) -> Mapping[str, str]:
    """Publish the scaffolding this language writes around a quote."""
    return _TERMINOLOGY[language]


def warning_text(language: CampaignLanguage, key: str) -> str:
    """Report one caveat in the language the copy beside it is written in."""
    return _WARNINGS[language][key]


def call_to_action(language: CampaignLanguage, platform: Platform) -> str:
    """Report what this destination asks a viewer to do, in this language."""
    return _CALLS_TO_ACTION[language][platform]


def thumbnail_direction(language: CampaignLanguage) -> str:
    """Report how a person should be directed to build this thumbnail."""
    return _THUMBNAIL_DIRECTION[language]
