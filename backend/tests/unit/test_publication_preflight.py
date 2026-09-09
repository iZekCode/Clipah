"""Prove social Publication preflight rejects each unsafe provider mismatch."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest

from clipah.publishing.preflight import (
    MediaFacts,
    OverlayBounds,
    PublicationEvidence,
    preflight,
)
from clipah.publishing.profiles import (
    MetadataLimit,
    ProviderProfile,
    SafeZone,
    UnknownProviderProfileError,
    profile_for,
)
from clipah.social_accounts.models import SocialProvider


def _profile(**changes: object) -> ProviderProfile:
    """Return one deliberately strict profile whose every boundary is testable."""
    base = ProviderProfile(
        provider=SocialProvider.TIKTOK,
        version="test-v1",
        containers=frozenset({"mp4"}),
        video_codecs=frozenset({"h264"}),
        audio_codecs=frozenset({"aac"}),
        max_file_size_bytes=1_000,
        min_duration_ms=3_000,
        max_duration_ms=60_000,
        min_width=720,
        max_width=1_080,
        min_height=1_280,
        max_height=1_920,
        min_aspect_ratio=Fraction(9, 16),
        max_aspect_ratio=Fraction(9, 16),
        min_frame_rate=Fraction(24, 1),
        max_frame_rate=Fraction(60, 1),
        audio_required=True,
        captions_required=True,
        thumbnail_required=True,
        required_disclosures=frozenset({"ai_generated"}),
        safe_zone=SafeZone(top=100, right=80, bottom=120, left=80),
        promotional_watermarks_forbidden=True,
        metadata_limits=(MetadataLimit(field="title", minimum=1, maximum=12),),
        output_width=1_080,
        output_height=1_920,
        output_frame_rate=30,
        output_video_bitrate="8M",
        output_audio_bitrate="128k",
    )
    return replace(base, **changes)


def _media(**changes: object) -> MediaFacts:
    """Return media exactly inside every strict profile boundary."""
    base = MediaFacts(
        size_bytes=999,
        duration_ms=30_000,
        container="mp4",
        video_codec="h264",
        audio_codec="aac",
        width=1_080,
        height=1_920,
        frame_rate=Fraction(30, 1),
    )
    return replace(base, **changes)


def _evidence(**changes: object) -> PublicationEvidence:
    """Return complete evidence whose overlays sit inside the safe zone."""
    base = PublicationEvidence(
        captions_attached=True,
        thumbnail_attached=True,
        promotional_watermarks=(),
        overlays=(OverlayBounds(left=80, top=100, right=1_000, bottom=1_800),),
        metadata={"title": "Launch"},
        disclosures={"ai_generated": True},
    )
    return replace(base, **changes)


@pytest.mark.unit
def test_checked_in_profiles_are_versioned_for_every_social_provider() -> None:
    """A provider rule change must create a new snapshot rather than rewrite old evidence."""
    profiles = tuple(profile_for(provider) for provider in SocialProvider)

    assert [profile.provider for profile in profiles] == list(SocialProvider)
    assert all(profile.version == "2026-09-09" for profile in profiles)
    assert profile_for(SocialProvider.TIKTOK, version="2026-09-09") == profiles[2]


@pytest.mark.unit
def test_an_unknown_profile_version_is_rejected() -> None:
    """A persisted version that code cannot reproduce must fail closed."""
    with pytest.raises(UnknownProviderProfileError):
        profile_for(SocialProvider.YOUTUBE, version="missing")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("media", "evidence", "expected_code"),
    [
        (_media(size_bytes=1_001), _evidence(), "file_size"),
        (_media(duration_ms=2_999), _evidence(), "duration"),
        (_media(duration_ms=60_001), _evidence(), "duration"),
        (_media(container="mkv"), _evidence(), "container"),
        (_media(video_codec="av1"), _evidence(), "video_codec"),
        (_media(audio_codec="opus"), _evidence(), "audio_codec"),
        (_media(audio_codec=None), _evidence(), "audio_required"),
        (_media(width=719), _evidence(), "resolution"),
        (_media(height=1_921), _evidence(), "resolution"),
        (_media(width=1_080, height=1_080), _evidence(), "aspect_ratio"),
        (_media(frame_rate=Fraction(23, 1)), _evidence(), "frame_rate"),
        (_media(frame_rate=Fraction(61, 1)), _evidence(), "frame_rate"),
        (_media(), _evidence(captions_attached=False), "captions_required"),
        (_media(), _evidence(thumbnail_attached=False), "thumbnail_required"),
        (_media(), _evidence(disclosures={}), "disclosure_required"),
        (
            _media(),
            _evidence(overlays=(OverlayBounds(left=79, top=100, right=1_000, bottom=1_800),)),
            "safe_zone",
        ),
        (_media(), _evidence(promotional_watermarks=("Clipah",)), "promotional_watermark"),
        (_media(), _evidence(metadata={"title": ""}), "metadata_minimum"),
        (_media(), _evidence(metadata={"title": "thirteen chars"}), "metadata_maximum"),
    ],
)
def test_each_profile_constraint_has_one_stable_public_violation(
    media: MediaFacts,
    evidence: PublicationEvidence,
    expected_code: str,
) -> None:
    """Removing any provider check must make its dedicated case fail."""
    report = preflight(profile=_profile(), media=media, evidence=evidence)

    assert expected_code in [violation.code for violation in report.violations]
    assert report.passed is False


@pytest.mark.unit
def test_a_compliant_master_returns_a_reproducible_public_report() -> None:
    """The same frozen inputs must serialize identically across retries."""
    first = preflight(profile=_profile(), media=_media(), evidence=_evidence())
    second = preflight(profile=_profile(), media=_media(), evidence=_evidence())

    assert first.passed is True
    assert first.violations == ()
    assert first.as_dict() == {
        "passed": True,
        "profileVersion": "test-v1",
        "provider": "tiktok",
        "violations": [],
    }
    assert first.as_dict() == second.as_dict()


@pytest.mark.unit
def test_violations_have_fixed_order_and_non_destructive_remediation() -> None:
    """Users need a stable diff and TikTok remediation must never promise mark removal."""
    report = preflight(
        profile=_profile(),
        media=_media(size_bytes=1_001, container="mkv"),
        evidence=_evidence(promotional_watermarks=("Clipah",)),
    )

    assert [item.code for item in report.violations] == [
        "file_size",
        "container",
        "promotional_watermark",
    ]
    watermark = report.violations[-1]
    assert watermark.field == "watermarks"
    assert watermark.remediation == "Render a clean master without promotional branding."
    assert "remove" not in watermark.message.lower()


@pytest.mark.unit
def test_provider_without_watermark_prohibition_preserves_creator_marks() -> None:
    """A mark is evidence, not a universal reason to mutate or reject creator media."""
    report = preflight(
        profile=_profile(promotional_watermarks_forbidden=False),
        media=_media(),
        evidence=_evidence(promotional_watermarks=("creator mark",)),
    )

    assert report.passed is True


@pytest.mark.unit
def test_report_never_serializes_internal_media_details() -> None:
    """Public preflight output must not become a side channel for private storage data."""
    report = preflight(
        profile=_profile(),
        media=_media(container="private/key/master.mp4?token=secret"),
        evidence=_evidence(),
    )

    serialized = str(report.as_dict())
    assert "private/key" not in serialized
    assert "token=secret" not in serialized
    assert report.violations[0].message == "The video container is not supported."
