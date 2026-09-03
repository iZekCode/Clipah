"""Compile one composition into a complete, safe FFmpeg plan.

The compiler is where a member's editing decisions become a filter graph, and it is the
only place that decides what this renderer can and cannot reproduce. Two rules run through
all of it:

* **Text is content, never syntax.** Caption text, overlay text, and watermark text are
  written to UTF-8 files inside the Job workspace and read back by `subtitles` and
  `drawtext`. No member-supplied character ever reaches an argument or a filter option.
* **An effect this renderer cannot reproduce is refused.** An export that silently drops a
  keyframe, a blend mode, or a motion preset is worse than one that never starts, because
  the member approved a preview that the file no longer matches.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import UUID

from clipah.editor.models import (
    BlendMode,
    CaptionMode,
    Captions,
    CaptionWord,
    CompositionV1,
    Crop,
    ImageOverlay,
    Keyframe,
    MotionPreset,
    TextAlign,
    TextOverlay,
    TrackType,
    VideoOverlay,
)
from clipah.editor.models import CitationOverlay as CitationOverlayModel
from clipah.renders.models import (
    ASSET_MISSING,
    FEATURE_UNSUPPORTED,
    PRESET_CANVAS,
    RENDER_AUDIO_SAMPLE_RATE,
    RENDER_FRAME_RATE,
    RenderAsset,
    RenderCompilationError,
    RenderFile,
    RenderInput,
    RenderPlan,
    RenderPreset,
    Watermark,
)

VIDEO_OUTPUT_LABEL = "vout"
AUDIO_OUTPUT_LABEL = "aout"
# Overlays may fade and stills may drift; anything else is an effect the browser preview
# can show and this renderer cannot yet reproduce faithfully.
IMAGE_MOTIONS = frozenset(
    {
        MotionPreset.NONE,
        MotionPreset.FADE,
        MotionPreset.KEN_BURNS_IN,
        MotionPreset.KEN_BURNS_OUT,
        MotionPreset.PAN_LEFT,
        MotionPreset.PAN_RIGHT,
    }
)
VIDEO_MOTIONS = frozenset({MotionPreset.NONE, MotionPreset.FADE})
FADE_SECONDS = 0.3
KEN_BURNS_STEP = 0.0015
KEN_BURNS_LIMIT = 1.35


def input_path(workspace: Path, asset_id: UUID) -> Path:
    """Where one asset is downloaded to, agreed between the compiler and the worker."""
    return workspace / "inputs" / f"{asset_id}.bin"


def compile_render_plan(
    composition: CompositionV1,
    *,
    assets: Mapping[UUID, RenderAsset],
    preset: RenderPreset,
    workspace: Path,
    watermark: Watermark | None = None,
) -> RenderPlan:
    """Turn one composition into the exact plan that renders it at one preset."""
    return _Compiler(composition, assets, preset, workspace, watermark).compile()


class _Compiler:
    """One compilation, holding the labels and inputs it hands out along the way."""

    def __init__(
        self,
        composition: CompositionV1,
        assets: Mapping[UUID, RenderAsset],
        preset: RenderPreset,
        workspace: Path,
        watermark: Watermark | None,
    ) -> None:
        """Bind one composition to the preset, workspace, and brand mark it renders with."""
        self._composition = composition
        self._assets = assets
        self._preset = preset
        self._workspace = workspace
        self._watermark = watermark
        self._width, self._height = PRESET_CANVAS[preset]
        self._inputs: list[RenderInput] = []
        self._files: list[RenderFile] = []
        self._stages: list[str] = []
        self._ducks: list[tuple[int, int]] = []

    def compile(self) -> RenderPlan:
        """Build the filter graph, the files it reads, and the inputs it opens."""
        video, audio = self._base_chains()
        video, mixes = self._apply_overlays(video)
        video = self._apply_captions(video)
        video = self._apply_watermark(video)
        audio = self._mix_audio(audio, mixes)
        self._stages.append(f"{video}null[{VIDEO_OUTPUT_LABEL}]")
        self._stages.append(f"{audio}anull[{AUDIO_OUTPUT_LABEL}]")
        return RenderPlan(
            preset=self._preset,
            width=self._width,
            height=self._height,
            frame_rate=RENDER_FRAME_RATE,
            duration_ms=self._composition.duration_ms,
            inputs=tuple(self._inputs),
            filter_script=";\n".join(self._stages),
            files=tuple(self._files),
            video_label=VIDEO_OUTPUT_LABEL,
            audio_label=AUDIO_OUTPUT_LABEL,
        )

    def _base_chains(self) -> tuple[str, str]:
        """Trim, frame, and concatenate every item of the timeline's video track."""
        items = [
            item
            for track in self._composition.tracks
            if track.type is TrackType.VIDEO
            for item in track.items
        ]
        if not items:
            raise RenderCompilationError(FEATURE_UNSUPPORTED, "no video track to render")
        video_labels: list[str] = []
        audio_labels: list[str] = []
        for index, item in enumerate(sorted(items, key=lambda entry: entry.timeline_start_ms)):
            if item.keyframes:
                raise RenderCompilationError(
                    FEATURE_UNSUPPORTED, "keyframes on a timeline item are not supported"
                )
            if item.blend_mode is not BlendMode.NORMAL:
                raise RenderCompilationError(FEATURE_UNSUPPORTED, "blend modes are not supported")
            if item.motion is not MotionPreset.NONE:
                raise RenderCompilationError(
                    FEATURE_UNSUPPORTED, "motion on a timeline item is not supported"
                )
            asset = self._asset(item.source_asset_id)
            stream = self._open(asset)
            start = _seconds(item.source_in_ms)
            end = _seconds(item.source_out_ms)
            crop = _crop_filter(item.crop, asset)
            video_label = f"v{index}"
            audio_label = f"a{index}"
            self._stages.append(
                f"[{stream}:v]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
                f"{crop}{self._frame_filters()}[{video_label}]"
            )
            self._stages.append(
                f"[{stream}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                f"aresample={RENDER_AUDIO_SAMPLE_RATE}[{audio_label}]"
            )
            video_labels.append(video_label)
            audio_labels.append(audio_label)
        if len(video_labels) == 1:
            return f"[{video_labels[0]}]", f"[{audio_labels[0]}]"
        pairs = "".join(
            f"[{video}][{audio}]" for video, audio in zip(video_labels, audio_labels, strict=True)
        )
        self._stages.append(f"{pairs}concat=n={len(video_labels)}:v=1:a=1[vbase][abase]")
        return "[vbase]", "[abase]"

    def _apply_overlays(self, video: str) -> tuple[str, list[str]]:
        """Place every overlay over the base video and collect the audio it contributes."""
        mixes: list[str] = []
        ducks: list[tuple[int, int]] = []
        for index, overlay in enumerate(self._composition.overlays):
            if isinstance(overlay, VideoOverlay | ImageOverlay):
                video = self._media_overlay(video, overlay, index)
                if isinstance(overlay, VideoOverlay) and not overlay.preserve_dialogue_audio:
                    mixes.append(self._overlay_audio(overlay, index))
                    ducks.append((overlay.timeline_start_ms, overlay.timeline_end_ms))
            else:
                video = self._text_overlay(video, overlay, index)
        self._ducks = ducks
        return video, mixes

    def _media_overlay(self, video: str, overlay: VideoOverlay | ImageOverlay, index: int) -> str:
        """Draw one image or video overlay inside its own window on the timeline."""
        supported = IMAGE_MOTIONS if isinstance(overlay, ImageOverlay) else VIDEO_MOTIONS
        if overlay.motion not in supported:
            raise RenderCompilationError(
                FEATURE_UNSUPPORTED, f"motion {overlay.motion.value} is not supported here"
            )
        if overlay.blend_mode is not BlendMode.NORMAL:
            raise RenderCompilationError(FEATURE_UNSUPPORTED, "blend modes are not supported")
        asset = self._asset(overlay.asset_id)
        duration_ms = overlay.timeline_end_ms - overlay.timeline_start_ms
        stream = self._open(asset, duration_ms=duration_ms)
        label = f"ov{index}"
        chain = [f"[{stream}:v]"]
        if isinstance(overlay, VideoOverlay):
            chain.append(
                f"trim=start={_seconds(overlay.source_in_ms)}:"
                f"end={_seconds(overlay.source_out_ms)},setpts=PTS-STARTPTS,"
            )
        chain.append(self._frame_filters(pixel_format="yuva420p"))
        chain.append(self._motion_filters(overlay, duration_ms))
        chain.append(self._alpha_filter(overlay))
        self._stages.append("".join(chain) + f"[{label}]")
        position = self._overlay_position(overlay)
        window = _window(overlay.timeline_start_ms, overlay.timeline_end_ms)
        result = f"vov{index}"
        self._stages.append(
            f"{video}[{label}]overlay={position}:enable={window}:eof_action=pass"
            f":format=auto[{result}]"
        )
        return f"[{result}]"

    def _text_overlay(
        self, video: str, overlay: TextOverlay | CitationOverlayModel, index: int
    ) -> str:
        """Draw one member-written overlay from a file, never from an argument."""
        if overlay.keyframes:
            raise RenderCompilationError(
                FEATURE_UNSUPPORTED, "keyframes on a text overlay are not supported"
            )
        if isinstance(overlay, TextOverlay) and overlay.motion not in VIDEO_MOTIONS:
            raise RenderCompilationError(
                FEATURE_UNSUPPORTED, f"motion {overlay.motion.value} is not supported here"
            )
        path = self._write(f"overlay-{index}.txt", overlay.text)
        style = overlay.style
        label = f"vtext{index}"
        box = (
            f":box=1:boxcolor={_colour(style.background_color)}@0.6:boxborderw=12"
            if style.background_enabled
            else ""
        )
        self._stages.append(
            f"{video}drawtext=textfile={_escape(path)}:fontsize={style.font_size}"
            f":fontcolor={_colour(style.color)}:line_spacing={int(style.line_height * 8)}"
            f":x={_text_x(style.align)}:y={_text_y(overlay.placement.value)}{box}"
            f":enable={_window(overlay.timeline_start_ms, overlay.timeline_end_ms)}[{label}]"
        )
        return f"[{label}]"

    def _apply_captions(self, video: str) -> str:
        """Burn the caption layer in from a subtitle file, if there is one to burn."""
        captions = self._composition.captions
        if captions.mode is CaptionMode.OFF or not captions.words:
            return video
        path = self._write("captions.ass", self._subtitle_document())
        self._stages.append(f"{video}subtitles=filename={_escape(path)}[vcap]")
        return "[vcap]"

    def _apply_watermark(self, video: str) -> str:
        """Draw the brand mark, which is text and follows the same rule as any other."""
        if self._watermark is None:
            return video
        path = self._write("watermark.txt", self._watermark.text)
        self._stages.append(
            f"{video}drawtext=textfile={_escape(path)}:fontsize={self._watermark.font_size}"
            ":fontcolor=0xFFFFFF@0.8:x=w-tw-32:y=h-th-32[vmark]"
        )
        return "[vmark]"

    def _mix_audio(self, audio: str, mixes: list[str]) -> str:
        """Apply the dialogue gain, duck it under any overlay that replaces it, and mix."""
        gain = f"volume={self._composition.audio.gain_db:.2f}dB"
        ducking = "".join(
            f",volume=enable={_window(start, end)}:volume=0" for start, end in self._ducks
        )
        self._stages.append(f"{audio}{gain}{ducking}[adlg]")
        music = self._music_chains()
        sources = ["adlg", *mixes, *music]
        if len(sources) == 1:
            return "[adlg]"
        joined = "".join(f"[{label}]" for label in sources)
        self._stages.append(f"{joined}amix=inputs={len(sources)}:normalize=0:duration=first[amix]")
        return "[amix]"

    def _music_chains(self) -> list[str]:
        """Place every music track item at its own moment, under the configured gain."""
        labels: list[str] = []
        for track_index, track in enumerate(self._composition.tracks):
            if track.type not in {TrackType.MUSIC, TrackType.AUDIO}:
                continue
            for item_index, item in enumerate(track.items):
                asset = self._asset(item.source_asset_id)
                stream = self._open(asset)
                label = f"amus{track_index}_{item_index}"
                delay = item.timeline_start_ms
                self._stages.append(
                    f"[{stream}:a]atrim=start={_seconds(item.source_in_ms)}:"
                    f"end={_seconds(item.source_out_ms)},asetpts=PTS-STARTPTS,"
                    f"adelay={delay}|{delay},"
                    f"volume={self._composition.audio.music_gain_db:.2f}dB,"
                    f"aresample={RENDER_AUDIO_SAMPLE_RATE}[{label}]"
                )
                labels.append(label)
        return labels

    def _overlay_audio(self, overlay: VideoOverlay, index: int) -> str:
        """Bring one overlay's own audio in at the moment it appears."""
        label = f"aov{index}"
        delay = overlay.timeline_start_ms
        stream = self._stream_of(overlay.asset_id)
        self._stages.append(
            f"[{stream}:a]atrim=start={_seconds(overlay.source_in_ms)}:"
            f"end={_seconds(overlay.source_out_ms)},asetpts=PTS-STARTPTS,"
            f"adelay={delay}|{delay},aresample={RENDER_AUDIO_SAMPLE_RATE}[{label}]"
        )
        return label

    def _overlay_position(self, overlay: VideoOverlay | ImageOverlay) -> str:
        """Where the overlay sits, as a constant or as an expression over time."""
        moving = [frame for frame in overlay.keyframes if frame.transform is not None]
        if not moving:
            return "x=0:y=0"
        _reject_unanimatable(moving)
        start = overlay.timeline_start_ms
        x_points = [(start + frame.at_ms, _position(frame, "x", self._width)) for frame in moving]
        y_points = [(start + frame.at_ms, _position(frame, "y", self._height)) for frame in moving]
        return f"x='{_piecewise(x_points)}':y='{_piecewise(y_points)}'"

    def _alpha_filter(self, overlay: VideoOverlay | ImageOverlay) -> str:
        """Animate the overlay's alpha, or fade it, or leave it alone."""
        fading = [frame for frame in overlay.keyframes if frame.opacity is not None]
        if fading:
            points = [
                (overlay.timeline_start_ms + frame.at_ms, float(frame.opacity or 0.0))
                for frame in fading
            ]
            return f",colorchannelmixer=aa='{_piecewise(points)}'"
        if overlay.motion is MotionPreset.FADE:
            end = _seconds(overlay.timeline_end_ms - overlay.timeline_start_ms)
            return (
                f",fade=t=in:st=0:d={FADE_SECONDS}:alpha=1"
                f",fade=t=out:st={float(end) - FADE_SECONDS:.3f}:d={FADE_SECONDS}:alpha=1"
            )
        if overlay.opacity < 1.0:
            return f",colorchannelmixer=aa={overlay.opacity:.3f}"
        return ""

    def _motion_filters(self, overlay: VideoOverlay | ImageOverlay, duration_ms: int) -> str:
        """Give a still the drift a static frame would otherwise lack."""
        if not isinstance(overlay, ImageOverlay):
            return ""
        frames = max(int(duration_ms / 1000 * RENDER_FRAME_RATE), 1)
        if overlay.motion is MotionPreset.KEN_BURNS_IN:
            return (
                f",zoompan=z='min(zoom+{KEN_BURNS_STEP},{KEN_BURNS_LIMIT})'"
                f":d={frames}:s={self._width}x{self._height}:fps={RENDER_FRAME_RATE}"
            )
        if overlay.motion is MotionPreset.KEN_BURNS_OUT:
            return (
                f",zoompan=z='max({KEN_BURNS_LIMIT}-on*{KEN_BURNS_STEP},1.0)'"
                f":d={frames}:s={self._width}x{self._height}:fps={RENDER_FRAME_RATE}"
            )
        if overlay.motion in {MotionPreset.PAN_LEFT, MotionPreset.PAN_RIGHT}:
            direction = "on" if overlay.motion is MotionPreset.PAN_RIGHT else f"{frames}-on"
            return (
                f",zoompan=z={KEN_BURNS_LIMIT}:x='iw/2-(iw/zoom/2)+({direction})*2'"
                f":y='ih/2-(ih/zoom/2)':d={frames}"
                f":s={self._width}x{self._height}:fps={RENDER_FRAME_RATE}"
            )
        return ""

    def _frame_filters(self, *, pixel_format: str = "yuv420p") -> str:
        """Scale and crop any source to exactly the preset's frame."""
        return (
            f"scale={self._width}:{self._height}:force_original_aspect_ratio=increase,"
            f"crop={self._width}:{self._height},setsar=1,fps={RENDER_FRAME_RATE},"
            f"format={pixel_format}"
        )

    def _subtitle_document(self) -> str:
        """Write the caption layer as an ASS document, with member text escaped."""
        captions = self._composition.captions
        style = captions.style
        primary = _ass_colour(
            style.highlight_color if captions.mode is CaptionMode.KARAOKE else style.color
        )
        secondary = _ass_colour(style.color)
        alignment = {"left": 1, "center": 2, "right": 3}[style.align.value]
        header = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {self._width}",
            f"PlayResY: {self._height}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            (
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour,"
                " BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
                " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
            ),
            (
                f"Style: Clipah,{style.font_family.value},{style.font_size},{primary},{secondary},"
                f"&H00000000,&H80000000,{-1 if style.weight >= 600 else 0},"
                f"{1 if style.italic else 0},{1 if style.decoration.value == 'underline' else 0},"
                f"{1 if style.decoration.value == 'strikethrough' else 0},100,100,"
                f"{style.letter_spacing:.0f},0,{3 if style.background_enabled else 1},2,0,"
                f"{alignment},60,60,120,1"
            ),
            "",
            "[Events]",
            ("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"),
        ]
        events = [
            f"Dialogue: 0,{_ass_time(line.start_ms)},{_ass_time(line.end_ms)},Clipah,,0,0,0,,{text}"
            for line, text in _caption_lines(captions)
        ]
        return "\n".join((*header, *events, ""))

    def _asset(self, asset_id: UUID) -> RenderAsset:
        """Read one asset the caller authorized, or refuse the whole render."""
        asset = self._assets.get(asset_id)
        if asset is None:
            raise RenderCompilationError(ASSET_MISSING, f"asset {asset_id} was not provided")
        return asset

    def _open(self, asset: RenderAsset, *, duration_ms: int | None = None) -> int:
        """Add one input for this asset and return the index FFmpeg will know it by."""
        entry = RenderInput(
            asset_id=asset.asset_id,
            path=input_path(self._workspace, asset.asset_id),
            loop_image=asset.is_image,
            duration_ms=duration_ms if asset.is_image else None,
        )
        self._inputs.append(entry)
        return len(self._inputs) - 1

    def _stream_of(self, asset_id: UUID) -> int:
        """Return the input index one asset was most recently opened as."""
        for index in range(len(self._inputs) - 1, -1, -1):
            if self._inputs[index].asset_id == asset_id:
                return index
        raise RenderCompilationError(ASSET_MISSING, f"asset {asset_id} was never opened")

    def _write(self, name: str, contents: str) -> Path:
        """Record one file the graph reads, inside the Job workspace and nowhere else."""
        path = self._workspace / "text" / name
        self._files.append(RenderFile(path=path, contents=contents))
        return path


def _caption_lines(captions: Captions) -> list[tuple[_CaptionLine, str]]:
    """Group caption words into the lines a viewer reads, with karaoke timing tags."""
    lines: list[tuple[_CaptionLine, str]] = []
    words: list[CaptionWord] = list(captions.words)
    while words:
        group = [words.pop(0)]
        while words and len(group) < 5 and words[0].start_ms - group[-1].end_ms <= 400:
            group.append(words.pop(0))
        line = _CaptionLine(start_ms=group[0].start_ms, end_ms=group[-1].end_ms)
        if captions.mode is CaptionMode.KARAOKE:
            text = "".join(
                f"{{\\k{max(round((word.end_ms - word.start_ms) / 10), 1)}}}{_ass_text(word.text)} "
                for word in group
            ).strip()
        else:
            text = " ".join(_ass_text(word.text) for word in group)
        lines.append((line, text))
    return lines


class _CaptionLine:
    """One rendered caption line's own bounds."""

    __slots__ = ("end_ms", "start_ms")

    def __init__(self, *, start_ms: int, end_ms: int) -> None:
        """Hold the instants this line appears and disappears."""
        self.start_ms = start_ms
        self.end_ms = end_ms


def _reject_unanimatable(frames: Sequence[Keyframe]) -> None:
    """Refuse the transform properties this renderer cannot animate faithfully."""
    for frame in frames:
        transform = frame.transform
        if transform is None:
            continue
        if abs(transform.rotation) > 0.001:
            raise RenderCompilationError(
                FEATURE_UNSUPPORTED, "rotation keyframes are not supported"
            )
    scales = {round(frame.transform.scale, 4) for frame in frames if frame.transform is not None}
    if len(scales) > 1:
        raise RenderCompilationError(FEATURE_UNSUPPORTED, "scale keyframes are not supported")


def _position(frame: Keyframe, axis: str, extent: int) -> float:
    """Turn one normalized transform coordinate into a pixel offset on the canvas."""
    transform = frame.transform
    value = 0.5 if transform is None else getattr(transform, axis)
    return float(value) * extent - extent / 2


def _piecewise(points: Sequence[tuple[int, float]]) -> str:
    """Interpolate linearly between keyframes, and hold the value outside them."""
    ordered = sorted(points, key=lambda point: point[0])
    expression = f"{ordered[-1][1]:.3f}"
    for (start_ms, start_value), (end_ms, end_value) in zip(
        reversed(ordered[:-1]), reversed(ordered[1:]), strict=True
    ):
        start = _seconds(start_ms)
        end = _seconds(end_ms)
        span = max((end_ms - start_ms) / 1000, 0.001)
        segment = f"{start_value:.3f}+({end_value - start_value:.3f})*(t-{start})/{span:.3f}"
        expression = f"if(lt(t,{end}),{segment},{expression})"
    return expression


def _crop_filter(crop: Crop | None, asset: RenderAsset) -> str:
    """Resolve one normalized crop against the media it frames."""
    if crop is None:
        return ""
    width = asset.width or 0
    height = asset.height or 0
    if width <= 0 or height <= 0:
        raise RenderCompilationError(
            FEATURE_UNSUPPORTED, "a crop needs the dimensions of the media it frames"
        )
    box_width = max(round(crop.width * width), 2)
    box_height = max(round(crop.height * height), 2)
    offset_x = round(crop.x * width)
    offset_y = round(crop.y * height)
    return f"crop=w={box_width}:h={box_height}:x={offset_x}:y={offset_y},"


def _window(start_ms: int, end_ms: int) -> str:
    """The enable expression that holds one filter to its own moment."""
    return f"'between(t,{_seconds(start_ms)},{_seconds(end_ms)})'"


def _seconds(milliseconds: int) -> str:
    """Spell one duration the way every filter argument here expects it."""
    return f"{milliseconds / 1000:.3f}"


def _colour(value: str) -> str:
    """Turn one hex colour into the form FFmpeg's drawtext reads."""
    return f"0x{value.lstrip('#').upper()}"


def _ass_colour(value: str) -> str:
    """Turn one hex colour into ASS's own opaque blue-green-red spelling."""
    raw = value.lstrip("#").upper()
    return f"&H00{raw[4:6]}{raw[2:4]}{raw[0:2]}"


def _ass_text(value: str) -> str:
    """Neutralize every character ASS would read as formatting rather than as words."""
    return (
        value.replace("\\", "/")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _ass_time(milliseconds: int) -> str:
    """Spell one instant in ASS's hours, minutes, seconds, and hundredths."""
    hundredths = round(milliseconds / 10)
    hours, remainder = divmod(hundredths, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}"


def _escape(path: Path) -> str:
    """Spell one path so a filter option reads it as a path and nothing else."""
    return str(path).replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


def _text_x(align: TextAlign) -> str:
    """Place drawn text horizontally the way the style asks for."""
    if align is TextAlign.LEFT:
        return "64"
    if align is TextAlign.RIGHT:
        return "w-tw-64"
    return "(w-tw)/2"


def _text_y(placement: str) -> str:
    """Place drawn text vertically according to where the overlay sits."""
    if placement == "top":
        return "h*0.12"
    if placement == "center":
        return "(h-th)/2"
    return "h*0.75"
