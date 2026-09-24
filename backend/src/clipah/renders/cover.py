"""A clip's cover picture: which frame it shows, and how each design draws its title.

Two rules keep a cover honest to the editor that designed it.

* **The frame is the export's frame.** ``at_ms`` is clip time, so it is resolved through
  the base track the same way the renderer plays it: the item under that instant, the
  source moment it maps to, and the crop window that item frames — moving with its
  keyframes exactly as the compiler's linear interpolation moves it.
* **Every size is a share of the canvas width,** as the watermark's is, so a cover drawn at
  any canvas matches the preview the browser draws with the same numbers.

Nothing here reads a database, a clock, or storage. Fonts are the ones the renderer ships.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid5

from PIL import Image, ImageDraw, ImageFont

from clipah.editor.models import (
    CompositionV1,
    Cover,
    CoverPreset,
    Keyframe,
    TrackType,
    Watermark,
    WatermarkKind,
)

COVER_NAMESPACE = UUID("5b0c7e3a-2f41-4c88-9a1e-3c0ae5d7c0e1")
#: The gap between a watermark and the edges it sits against, as the export keeps it.
WATERMARK_MARGIN = 0.04
#: Where the renderer's fonts live in a worker, then beside the editor that shares them.
FONT_DIRECTORIES = (
    Path("/usr/local/share/fonts/clipah"),
    Path(__file__).resolve().parents[4] / "frontend" / "features" / "editor" / "fonts",
)


class CoverUnavailableError(Exception):
    """A composition whose cover cannot be drawn: no cover, or no frame under it."""


@dataclass(frozen=True, slots=True)
class CoverFrame:
    """The source moment a cover shows and the window of the source frame it frames.

    The window is a share of the source frame, so it applies to the proxy unchanged; a
    window of ``None`` is the whole frame.
    """

    source_ms: int
    window: tuple[float, float, float, float] | None


@dataclass(frozen=True, slots=True)
class TitleDesign:
    """How one preset writes its title, every length a share of the canvas width."""

    font: str
    size: float
    min_size: float
    color: str
    max_lines: int
    uppercase: bool
    stroke: float


#: The title each preset draws; a minimal cover draws none.
TITLE_DESIGNS: dict[CoverPreset, TitleDesign] = {
    CoverPreset.BOLD: TitleDesign("Poppins-ExtraBold.ttf", 0.085, 0.05, "#FFD166", 4, True, 0.006),
    CoverPreset.CLEAN: TitleDesign("Poppins-SemiBold.ttf", 0.065, 0.04, "#FFFFFF", 3, False, 0.0),
    CoverPreset.TOP_TITLE: TitleDesign("Anton-Regular.ttf", 0.1, 0.06, "#FFFFFF", 3, True, 0.0),
}
TITLE_WIDTH = 0.84
LINE_HEIGHT = 1.12


def cover_asset_id(revision_id: UUID) -> UUID:
    """The one identity a Revision's cover has, so a redrawn cover is recognized."""
    return uuid5(COVER_NAMESPACE, f"cover-v1:{revision_id}")


def cover_name(revision_id: UUID) -> str:
    """The version-one storage name of one Revision's cover."""
    return f"covers-v1/{cover_asset_id(revision_id)}.jpg"


def cover_frame(composition: CompositionV1) -> CoverFrame:
    """Resolve the cover instant to the source moment and window the export shows there."""
    cover = composition.cover
    if cover is None:
        raise CoverUnavailableError("this composition has no cover")
    items = [
        item
        for track in composition.tracks
        if track.type is TrackType.VIDEO
        for item in track.items
        if item.timeline_start_ms <= cover.at_ms < item.timeline_end_ms
    ]
    if not items:
        raise CoverUnavailableError("no picture plays at the cover instant")
    item = items[0]
    local_ms = cover.at_ms - item.timeline_start_ms
    crop = item.crop
    if crop is None:
        return CoverFrame(source_ms=item.source_in_ms + local_ms, window=None)
    x, y = crop.x, crop.y
    moving = [frame for frame in item.keyframes if frame.transform is not None]
    if moving:
        x = _window_edge(moving, local_ms, "x", crop.width)
        y = _window_edge(moving, local_ms, "y", crop.height)
    return CoverFrame(
        source_ms=item.source_in_ms + local_ms, window=(x, y, crop.width, crop.height)
    )


def _window_edge(frames: list[Keyframe], at_ms: int, axis: str, extent: float) -> float:
    """The window's leading edge at one instant, held before and after its keyframes."""
    points = sorted((frame.at_ms, float(getattr(frame.transform, axis))) for frame in frames)
    centre = points[-1][1]
    if at_ms <= points[0][0]:
        centre = points[0][1]
    else:
        for (start, start_value), (end, end_value) in pairwise(points):
            if start <= at_ms < end:
                centre = start_value + (end_value - start_value) * (at_ms - start) / (end - start)
                break
    return min(max(centre - extent / 2, 0.0), max(1.0 - extent, 0.0))


def draw_cover(
    frame: Image.Image,
    cover: Cover,
    *,
    watermark: Watermark | None,
    watermark_image: Image.Image | None,
) -> Image.Image:
    """Draw one preset over the framed still, then the clip's watermark over both."""
    canvas = frame.convert("RGBA")
    shade = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    title = cover.title if cover.preset in TITLE_DESIGNS else None
    if cover.preset is CoverPreset.BOLD:
        _gradient(shade, start=0.55, end=1.0, darkest=0.85, downward=True)
    elif cover.preset is CoverPreset.TOP_TITLE:
        _gradient(shade, start=0.0, end=0.45, darkest=0.8, downward=False)
    canvas = Image.alpha_composite(canvas, shade)
    if title is not None:
        canvas = _draw_title(canvas, cover.preset, title)
    if watermark is not None:
        canvas = _draw_watermark(canvas, watermark, watermark_image)
    return canvas.convert("RGB")


def _gradient(
    layer: Image.Image, *, start: float, end: float, darkest: float, downward: bool
) -> None:
    """Shade a band of the canvas from clear to dark, towards the edge the title sits on."""
    width, height = layer.size
    draw = ImageDraw.Draw(layer)
    top = round(start * height)
    bottom = round(end * height)
    span = max(bottom - top, 1)
    for row in range(top, bottom):
        share = (row - top) / span if downward else 1 - (row - top) / span
        draw.line([(0, row), (width, row)], fill=(0, 0, 0, round(255 * darkest * share)))


def _draw_title(canvas: Image.Image, preset: CoverPreset, title: str) -> Image.Image:
    """Write the title in its preset's type, shrinking it until it fits its lines."""
    design = TITLE_DESIGNS[preset]
    width, height = canvas.size
    text = title.upper() if design.uppercase else title
    size = design.size
    while True:
        font = _font(design.font, max(round(width * size), 8))
        lines = _wrap(text, font, width * TITLE_WIDTH)
        if len(lines) <= design.max_lines or size <= design.min_size:
            break
        size = max(size * 0.9, design.min_size)
    lines = lines[: design.max_lines]
    pixel_size = max(round(width * size), 8)
    line_height = round(pixel_size * LINE_HEIGHT)
    block = line_height * len(lines)
    if preset is CoverPreset.BOLD:
        top = round(height * 0.86) - block
    elif preset is CoverPreset.TOP_TITLE:
        top = round(height * 0.1)
    else:
        top = round(height * 0.72) - block // 2
    draw = ImageDraw.Draw(canvas)
    if preset is CoverPreset.CLEAN:
        pad = round(width * 0.04)
        band = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(band).rectangle(
            [(0, top - pad), (width, top + block + pad)], fill=(0, 0, 0, round(255 * 0.7))
        )
        canvas = Image.alpha_composite(canvas, band)
        draw = ImageDraw.Draw(canvas)
    stroke = round(width * design.stroke)
    for index, line in enumerate(lines):
        line_width = draw.textlength(line, font=font)
        draw.text(
            ((width - line_width) / 2, top + index * line_height),
            line,
            font=font,
            fill=design.color,
            stroke_width=stroke,
            stroke_fill="#000000",
        )
    return canvas


def _wrap(text: str, font: ImageFont.FreeTypeFont, limit: float) -> list[str]:
    """Break text into lines no wider than the limit, a word too long standing alone."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and font.getlength(trial) > limit:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def _draw_watermark(
    canvas: Image.Image, watermark: Watermark, picture: Image.Image | None
) -> Image.Image:
    """Place the clip's mark in its grid cell at its share of the width, as the export does."""
    width, height = canvas.size
    margin = round(width * WATERMARK_MARGIN)
    if watermark.kind is WatermarkKind.TEXT:
        font = _font("Poppins-SemiBold.ttf", max(round(width * watermark.size), 12))
        text = watermark.text or ""
        box = ImageDraw.Draw(canvas).textbbox((0, 0), text, font=font)
        mark_width, mark_height = round(box[2] - box[0]), round(box[3] - box[1])
        x, y = _cell(watermark, width, height, mark_width, mark_height, margin)
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        alpha = round(255 * watermark.opacity)
        ImageDraw.Draw(layer).text(
            (x - box[0], y - box[1]), text, font=font, fill=(255, 255, 255, alpha)
        )
        return Image.alpha_composite(canvas, layer)
    if picture is None:
        return canvas
    mark_width = max(round(width * watermark.size), 2)
    mark_height = max(round(picture.height * mark_width / picture.width), 2)
    mark = picture.convert("RGBA").resize((mark_width, mark_height), Image.Resampling.LANCZOS)
    if watermark.opacity < 1:
        faded = mark.getchannel("A").point(lambda value: round(value * watermark.opacity))
        mark.putalpha(faded)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste(mark, _cell(watermark, width, height, mark_width, mark_height, margin), mark)
    return Image.alpha_composite(canvas, layer)


def _cell(
    watermark: Watermark, width: int, height: int, mark_width: int, mark_height: int, margin: int
) -> tuple[int, int]:
    """The top-left corner of a mark in its grid cell."""
    position = watermark.position.value
    if position.endswith("Left"):
        x = margin
    elif position.endswith("Right"):
        x = width - mark_width - margin
    else:
        x = (width - mark_width) // 2
    if position.startswith("top"):
        y = margin
    elif position.startswith("bottom"):
        y = height - mark_height - margin
    else:
        y = (height - mark_height) // 2
    return x, y


@lru_cache(maxsize=32)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Open one of the renderer's shipped fonts at one pixel size."""
    for directory in FONT_DIRECTORIES:
        path = directory / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise CoverUnavailableError(f"font {name} is not installed")
