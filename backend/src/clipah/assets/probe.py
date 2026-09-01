"""Convert untrusted media inspection output into validated Asset metadata."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any

MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024
MAX_DURATION_MS = 4 * 60 * 60 * 1000
MAX_SHORT_EDGE = 2160
MAX_LONG_EDGE = 3840
SUPPORTED_VIDEO_CODECS = frozenset({"h264", "hevc", "vp8", "vp9", "av1"})
SUPPORTED_AUDIO_CODECS = frozenset({"aac", "opus", "mp3"})
MP4_MIME_TYPES = frozenset({"application/mp4", "video/mp4"})


class MediaValidationError(Exception):
    """A stable sanitized refusal raised for invalid or unsupported source media."""

    def __init__(self, code: str) -> None:
        """Expose only the fixed code and discard all provider-specific diagnostics."""
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Trusted source-media facts required by ingest and later pipeline stages."""

    duration_ms: int
    width: int
    height: int
    video_codecs: tuple[str, ...]
    audio_codecs: tuple[str, ...]
    variable_frame_rate: bool

    @property
    def video_codec(self) -> str | None:
        """Return the primary video codec persisted on the Asset row."""
        return self.video_codecs[0] if self.video_codecs else None

    @property
    def audio_codec(self) -> str | None:
        """Return the primary audio codec persisted on the Asset row."""
        return self.audio_codecs[0] if self.audio_codecs else None

    @property
    def video_stream_count(self) -> int:
        """Return the number of video streams observed by ffprobe."""
        return len(self.video_codecs)

    @property
    def audio_stream_count(self) -> int:
        """Return the number of audio streams observed by ffprobe."""
        return len(self.audio_codecs)


MimeDetector = Callable[[Path], str]


def sniff_mime(path: Path, *, detector: MimeDetector | None = None) -> str:
    """Identify media from file bytes rather than trusting client metadata."""
    try:
        observed = (detector or _libmagic_mime)(path)
    except (ImportError, OSError, TypeError, ValueError) as error:
        raise MediaValidationError("ASSET_INVALID_MEDIA") from error
    if not isinstance(observed, str) or not observed:
        raise MediaValidationError("ASSET_INVALID_MEDIA")
    return observed.lower()


def _libmagic_mime(path: Path) -> str:
    """Call python-magic while containing every native adapter failure."""
    try:
        import magic

        observed = magic.from_file(str(path), mime=True)
    except Exception as error:
        raise MediaValidationError("ASSET_INVALID_MEDIA") from error
    if not isinstance(observed, str):
        raise TypeError
    return observed


def parse_probe(payload: Mapping[str, object]) -> SourceMetadata:
    """Parse one ffprobe JSON object while rejecting malformed or non-finite fields."""
    try:
        format_payload = _mapping(payload["format"])
        streams = _sequence(payload["streams"])
        duration_ms = _duration_ms(format_payload["duration"])
        video_streams = tuple(
            _mapping(stream) for stream in streams if _mapping(stream).get("codec_type") == "video"
        )
        audio_streams = tuple(
            _mapping(stream) for stream in streams if _mapping(stream).get("codec_type") == "audio"
        )
        video_codecs = tuple(_codec_name(stream) for stream in video_streams)
        audio_codecs = tuple(_codec_name(stream) for stream in audio_streams)
        width = _positive_int(video_streams[0]["width"]) if video_streams else 0
        height = _positive_int(video_streams[0]["height"]) if video_streams else 0
        variable_frame_rate = any(_is_variable_frame_rate(stream) for stream in video_streams)
    except (
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        InvalidOperation,
        ZeroDivisionError,
    ) as error:
        raise MediaValidationError("ASSET_INVALID_MEDIA") from error
    return SourceMetadata(
        duration_ms=duration_ms,
        width=width,
        height=height,
        video_codecs=video_codecs,
        audio_codecs=audio_codecs,
        variable_frame_rate=variable_frame_rate,
    )


def validate_source(
    *,
    declared_mime: str,
    sniffed_mime: str,
    metadata: SourceMetadata,
    size_bytes: int,
) -> None:
    """Enforce the fixed first-release media boundary using exact failure codes."""
    if size_bytes > MAX_MEDIA_BYTES:
        raise MediaValidationError("ASSET_TOO_LARGE")
    if size_bytes < 0:
        raise MediaValidationError("ASSET_INVALID_MEDIA")
    validate_mime_types(declared_mime=declared_mime, sniffed_mime=sniffed_mime)
    if metadata.duration_ms > MAX_DURATION_MS:
        raise MediaValidationError("ASSET_TOO_LONG")
    if metadata.video_stream_count == 0:
        raise MediaValidationError("ASSET_VIDEO_STREAM_REQUIRED")
    if metadata.video_stream_count != 1:
        raise MediaValidationError("ASSET_VIDEO_STREAM_COUNT_INVALID")
    if metadata.audio_stream_count == 0:
        raise MediaValidationError("ASSET_AUDIO_STREAM_REQUIRED")
    short_edge, long_edge = sorted((metadata.width, metadata.height))
    if short_edge > MAX_SHORT_EDGE or long_edge > MAX_LONG_EDGE:
        raise MediaValidationError("ASSET_RESOLUTION_UNSUPPORTED")
    if any(codec not in SUPPORTED_VIDEO_CODECS for codec in metadata.video_codecs):
        raise MediaValidationError("ASSET_INVALID_VIDEO_CODEC")
    if any(codec not in SUPPORTED_AUDIO_CODECS for codec in metadata.audio_codecs):
        raise MediaValidationError("ASSET_INVALID_AUDIO_CODEC")


def validate_mime_types(*, declared_mime: str, sniffed_mime: str) -> None:
    """Reject a declared/observed MIME mismatch before invoking media parsers."""
    if not _mime_types_match(declared_mime, sniffed_mime):
        raise MediaValidationError("ASSET_MIME_MISMATCH")


def _mapping(value: object) -> Mapping[str, Any]:
    """Narrow one untrusted JSON value to an object mapping."""
    if not isinstance(value, Mapping):
        raise TypeError
    return value


def _sequence(value: object) -> Sequence[object]:
    """Narrow one untrusted JSON value to a non-text array."""
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError
    return value


def _duration_ms(value: object) -> int:
    """Convert positive decimal seconds into representable integer milliseconds."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise TypeError
    seconds = Decimal(str(value))
    if not seconds.is_finite() or seconds <= 0:
        raise ValueError
    milliseconds = int(seconds * 1000)
    if milliseconds <= 0:
        raise ValueError
    return milliseconds


def _positive_int(value: object) -> int:
    """Accept a positive integer without treating booleans as dimensions."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError
    return value


def _codec_name(stream: Mapping[str, Any]) -> str:
    """Read one normalized non-empty codec name from a stream object."""
    value = stream["codec_name"]
    if not isinstance(value, str) or not value:
        raise TypeError
    return value.lower()


def _is_variable_frame_rate(stream: Mapping[str, Any]) -> bool:
    """Compare ffprobe rational rates exactly so close rates never collapse."""
    average = stream["avg_frame_rate"]
    real = stream["r_frame_rate"]
    if not isinstance(average, str) or not isinstance(real, str):
        raise TypeError
    return Fraction(average) != Fraction(real)


def _mime_types_match(declared: str, sniffed: str) -> bool:
    """Allow only exact MIME equality or the two standard MP4 aliases."""
    normalized = (declared.lower(), sniffed.lower())
    return normalized[0] == normalized[1] or all(value in MP4_MIME_TYPES for value in normalized)
