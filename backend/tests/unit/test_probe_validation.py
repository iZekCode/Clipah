"""Unit contracts for turning untrusted probe output into trusted media metadata."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from clipah.assets.probe import (
    MAX_MEDIA_BYTES,
    MediaValidationError,
    parse_probe,
    sniff_mime,
    validate_source,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "media"


def _probe_payload(
    *,
    duration: object = "42.125",
    width: object = 1920,
    height: object = 1080,
    video_codecs: tuple[str, ...] = ("h264",),
    audio_codecs: tuple[str, ...] = ("aac",),
    average_rate: str = "30000/1001",
    real_rate: str = "30000/1001",
) -> Mapping[str, Any]:
    """Build complete ffprobe-shaped input while allowing one boundary to vary."""
    streams: list[dict[str, object]] = [
        {
            "codec_type": "video",
            "codec_name": codec,
            "width": width,
            "height": height,
            "avg_frame_rate": average_rate,
            "r_frame_rate": real_rate,
        }
        for codec in video_codecs
    ]
    streams.extend({"codec_type": "audio", "codec_name": codec} for codec in audio_codecs)
    return {"format": {"duration": duration}, "streams": streams}


@pytest.mark.unit
def test_parse_probe_preserves_exact_metadata_for_supported_media() -> None:
    """Trusted metadata must retain values later persisted on source and derived Assets."""
    metadata = parse_probe(_probe_payload())

    assert metadata.duration_ms == 42_125
    assert metadata.width == 1920
    assert metadata.height == 1080
    assert metadata.video_codec == "h264"
    assert metadata.audio_codec == "aac"
    assert metadata.video_codecs == ("h264",)
    assert metadata.audio_codecs == ("aac",)
    assert metadata.video_stream_count == 1
    assert metadata.audio_stream_count == 1
    assert metadata.variable_frame_rate is False


@pytest.mark.unit
def test_parse_probe_detects_variable_frame_rate_without_float_rounding() -> None:
    """VFR sources must remain observable even when their rational rates are close."""
    metadata = parse_probe(_probe_payload(average_rate="24000/1001", real_rate="30000/1001"))

    assert metadata.variable_frame_rate is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({}, "ASSET_INVALID_MEDIA"),
        ({"format": [], "streams": []}, "ASSET_INVALID_MEDIA"),
        ({"format": {"duration": "1"}, "streams": "invalid"}, "ASSET_INVALID_MEDIA"),
        ({"format": {"duration": "nan"}, "streams": []}, "ASSET_INVALID_MEDIA"),
        (_probe_payload(duration=True), "ASSET_INVALID_MEDIA"),
        (_probe_payload(duration="-1"), "ASSET_INVALID_MEDIA"),
        (_probe_payload(duration="0.0001"), "ASSET_INVALID_MEDIA"),
        (_probe_payload(width=0), "ASSET_INVALID_MEDIA"),
        (_probe_payload(video_codecs=("",)), "ASSET_INVALID_MEDIA"),
        (_probe_payload(average_rate=1), "ASSET_INVALID_MEDIA"),
        (_probe_payload(average_rate="broken"), "ASSET_INVALID_MEDIA"),
    ],
)
def test_parse_probe_rejects_malformed_or_nonfinite_provider_values(
    payload: Mapping[str, Any], code: str
) -> None:
    """Untrusted ffprobe JSON must fail closed instead of leaking parser exceptions."""
    with pytest.raises(MediaValidationError) as captured:
        parse_probe(payload)

    assert captured.value.code == code
    assert str(captured.value) == code


@pytest.mark.unit
@pytest.mark.parametrize(
    ("case", "payload", "declared_mime", "sniffed_mime", "size_bytes", "code"),
    [
        (
            "too large",
            _probe_payload(),
            "video/mp4",
            "video/mp4",
            MAX_MEDIA_BYTES + 1,
            "ASSET_TOO_LARGE",
        ),
        (
            "negative size",
            _probe_payload(),
            "video/mp4",
            "video/mp4",
            -1,
            "ASSET_INVALID_MEDIA",
        ),
        (
            "MIME mismatch",
            _probe_payload(),
            "audio/mpeg",
            "video/mp4",
            100,
            "ASSET_MIME_MISMATCH",
        ),
        (
            "too long",
            _probe_payload(duration="14400.001"),
            "video/mp4",
            "video/mp4",
            100,
            "ASSET_TOO_LONG",
        ),
        (
            "missing video",
            _probe_payload(video_codecs=()),
            "video/mp4",
            "video/mp4",
            100,
            "ASSET_VIDEO_STREAM_REQUIRED",
        ),
        (
            "multiple video streams",
            _probe_payload(video_codecs=("h264", "h264")),
            "video/mp4",
            "video/mp4",
            100,
            "ASSET_VIDEO_STREAM_COUNT_INVALID",
        ),
        (
            "missing audio",
            _probe_payload(audio_codecs=()),
            "video/mp4",
            "video/mp4",
            100,
            "ASSET_AUDIO_STREAM_REQUIRED",
        ),
        (
            "oversized landscape",
            _probe_payload(width=3841, height=2160),
            "video/mp4",
            "video/mp4",
            100,
            "ASSET_RESOLUTION_UNSUPPORTED",
        ),
        (
            "oversized portrait",
            _probe_payload(width=2160, height=3841),
            "video/mp4",
            "video/mp4",
            100,
            "ASSET_RESOLUTION_UNSUPPORTED",
        ),
        (
            "unsupported video codec",
            _probe_payload(video_codecs=("mpeg4",)),
            "video/mp4",
            "video/mp4",
            100,
            "ASSET_INVALID_VIDEO_CODEC",
        ),
        (
            "unsupported secondary audio codec",
            _probe_payload(audio_codecs=("aac", "flac")),
            "video/mp4",
            "video/mp4",
            100,
            "ASSET_INVALID_AUDIO_CODEC",
        ),
    ],
)
def test_validate_source_returns_the_exact_code_for_each_media_limit(
    case: str,
    payload: Mapping[str, Any],
    declared_mime: str,
    sniffed_mime: str,
    size_bytes: int,
    code: str,
) -> None:
    """Every media refusal must remain stable without exposing provider diagnostics."""
    del case
    metadata = parse_probe(payload)

    with pytest.raises(MediaValidationError) as captured:
        validate_source(
            declared_mime=declared_mime,
            sniffed_mime=sniffed_mime,
            metadata=metadata,
            size_bytes=size_bytes,
        )

    assert captured.value.code == code
    assert str(captured.value) == code


@pytest.mark.unit
@pytest.mark.parametrize(
    ("duration", "width", "height", "video_codec", "audio_codec"),
    [
        ("14400", 3840, 2160, "h264", "aac"),
        ("14400", 2160, 3840, "hevc", "opus"),
        ("1", 1280, 720, "vp8", "mp3"),
        ("1", 1280, 720, "vp9", "aac"),
        ("1", 1280, 720, "av1", "aac"),
    ],
)
def test_validate_source_accepts_exact_limits_and_every_supported_codec(
    duration: str, width: int, height: int, video_codec: str, audio_codec: str
) -> None:
    """Boundary values and the complete approved codec set must remain ingestible."""
    metadata = parse_probe(
        _probe_payload(
            duration=duration,
            width=width,
            height=height,
            video_codecs=(video_codec,),
            audio_codecs=(audio_codec,),
        )
    )

    validate_source(
        declared_mime="video/mp4",
        sniffed_mime="application/mp4",
        metadata=metadata,
        size_bytes=MAX_MEDIA_BYTES,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fixture", "width", "height", "variable_frame_rate"),
    [
        ("landscape.mp4", 640, 360, False),
        ("portrait.mp4", 360, 640, False),
        ("variable-frame-rate.mp4", 320, 180, True),
    ],
)
def test_generated_video_fixtures_retain_their_declared_semantics(
    fixture: str, width: int, height: int, variable_frame_rate: bool
) -> None:
    """Fixture drift must not silently stop exercising orientation and VFR branches."""
    payload = json.loads((FIXTURE_ROOT / f"{fixture}.ffprobe.json").read_text())

    metadata = parse_probe(payload)

    assert (metadata.width, metadata.height) == (width, height)
    assert metadata.variable_frame_rate is variable_frame_rate


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fixture", "code"),
    [
        ("no-audio.mp4", "ASSET_AUDIO_STREAM_REQUIRED"),
        ("audio-only.m4a", "ASSET_VIDEO_STREAM_REQUIRED"),
        ("unsupported-codec.mkv", "ASSET_INVALID_VIDEO_CODEC"),
    ],
)
def test_generated_invalid_fixtures_reach_the_intended_validation_branch(
    fixture: str, code: str
) -> None:
    """Each invalid fixture must keep catching the specific production regression it names."""
    payload = json.loads((FIXTURE_ROOT / f"{fixture}.ffprobe.json").read_text())
    metadata = parse_probe(payload)

    with pytest.raises(MediaValidationError) as captured:
        validate_source(
            declared_mime="video/mp4",
            sniffed_mime="video/mp4",
            metadata=metadata,
            size_bytes=(FIXTURE_ROOT / fixture).stat().st_size,
        )

    assert captured.value.code == code


@pytest.mark.unit
def test_oversized_metadata_fixture_reaches_the_duration_boundary_first() -> None:
    """The cheap synthetic boundary fixture must reject before allocating huge real media."""
    payload = json.loads((FIXTURE_ROOT / "oversized-metadata.json").read_text())
    metadata = parse_probe(payload)

    with pytest.raises(MediaValidationError) as captured:
        validate_source(
            declared_mime="video/mp4",
            sniffed_mime="video/mp4",
            metadata=metadata,
            size_bytes=100,
        )

    assert captured.value.code == "ASSET_TOO_LONG"


@pytest.mark.unit
def test_libmagic_identifies_bytes_instead_of_the_filename_extension() -> None:
    """Renaming corrupt bytes to MP4 must not let them enter ffprobe as trusted video."""
    observed: list[Path] = []

    def detect(path: Path) -> str:
        """Model libmagic's byte-based result without requiring a host-native library."""
        observed.append(path)
        return "video/mp4" if path.read_bytes().startswith(b"\x00\x00\x00") else "text/plain"

    assert sniff_mime(FIXTURE_ROOT / "landscape.mp4", detector=detect) == "video/mp4"
    assert sniff_mime(FIXTURE_ROOT / "corrupt.bin", detector=detect) == "text/plain"
    assert observed == [FIXTURE_ROOT / "landscape.mp4", FIXTURE_ROOT / "corrupt.bin"]


@pytest.mark.unit
def test_default_mime_detector_calls_libmagic_and_normalizes_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production boundary must ask libmagic for MIME output based on the exact local file."""
    module = ModuleType("magic")
    calls: list[tuple[str, bool]] = []

    def from_file(path: str, *, mime: bool) -> str:
        """Return the mixed-case value emitted by a native detector."""
        calls.append((path, mime))
        return "Video/MP4"

    module.from_file = from_file  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "magic", module)
    path = FIXTURE_ROOT / "landscape.mp4"

    assert sniff_mime(path) == "video/mp4"
    assert calls == [(str(path), True)]


@pytest.mark.unit
def test_default_mime_detector_sanitizes_python_magic_native_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """python-magic exceptions must never escape as generic internal Job failures."""
    module = ModuleType("magic")

    class MagicException(Exception):  # noqa: N818 - mirrors python-magic's public exception
        """Model python-magic's direct Exception subclass."""

    def from_file(_path: str, *, mime: bool) -> str:
        """Raise the native adapter's exception after verifying MIME mode."""
        assert mime is True
        raise MagicException("native database detail")

    module.MagicException = MagicException  # type: ignore[attr-defined]
    module.from_file = from_file  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "magic", module)

    with pytest.raises(MediaValidationError, match=r"^ASSET_INVALID_MEDIA$"):
        sniff_mime(FIXTURE_ROOT / "landscape.mp4")


@pytest.mark.unit
@pytest.mark.parametrize(
    "detector",
    [lambda _path: "", lambda _path: (_ for _ in ()).throw(OSError("native failure"))],
)
def test_mime_detection_failures_collapse_to_one_sanitized_code(detector: object) -> None:
    """Native errors and empty results must never escape into Job metadata."""
    with pytest.raises(MediaValidationError, match=r"^ASSET_INVALID_MEDIA$"):
        sniff_mime(FIXTURE_ROOT / "landscape.mp4", detector=detector)  # type: ignore[arg-type]
