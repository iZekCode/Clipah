"""Compiling a composition into an FFmpeg plan, safely and faithfully.

Two questions are asked of every plan here. Does it render what the composition says —
the trim, the crop, the captions, the overlays, the gains? And can it be run over text a
member wrote without that text ever reaching a filter argument or a shell? The second
question is why the compiler exists at all: FFmpeg's filter syntax is a language, and
member-supplied words must never be parsed as one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from clipah.editor.models import CompositionV1, parse_composition
from clipah.models import AssetKind
from clipah.renders.compiler import compile_render_plan, input_path
from clipah.renders.ffmpeg_renderer import build_render_arguments
from clipah.renders.models import (
    ASSET_MISSING,
    FEATURE_UNSUPPORTED,
    PRESET_CANVAS,
    RenderAsset,
    RenderCompilationError,
    RenderPlan,
    RenderPreset,
    Watermark,
)

SOURCE_ASSET_ID = UUID("11111111-1111-4111-8111-111111111111")
BROLL_VIDEO_ID = UUID("22222222-2222-4222-8222-222222222222")
BROLL_IMAGE_ID = UUID("33333333-3333-4333-8333-333333333333")
SUGGESTION_ID = UUID("44444444-4444-4444-8444-444444444444")
EVIDENCE_ID = UUID("55555555-5555-4555-8555-555555555555")
PROVENANCE_ID = UUID("88888888-8888-4888-8888-888888888888")
HOSTILE_TEXT = "Rp1.000 :drop; {\\an8} 'quoted' %s [0:v] \n second line"
WORKSPACE = Path("/tmp/clipah-render-workspace")


def source_asset(**overrides: Any) -> RenderAsset:
    """The Project's own source media, as the compiler is told about it."""
    values: dict[str, Any] = {
        "asset_id": SOURCE_ASSET_ID,
        "kind": AssetKind.SOURCE,
        "content_type": "video/mp4",
        "duration_ms": 600_000,
        "width": 1920,
        "height": 1080,
    }
    values.update(overrides)
    return RenderAsset(**values)


def assets(*extra: RenderAsset) -> dict[UUID, RenderAsset]:
    """The asset table one render is allowed to read."""
    return {asset.asset_id: asset for asset in (source_asset(), *extra)}


def broll_video() -> RenderAsset:
    """One accepted B-roll video the composition may place over the source."""
    return RenderAsset(
        asset_id=BROLL_VIDEO_ID,
        kind=AssetKind.SOURCE,
        content_type="video/mp4",
        duration_ms=8_000,
        width=1920,
        height=1080,
    )


def broll_image() -> RenderAsset:
    """One accepted still the composition may place over the source."""
    return RenderAsset(
        asset_id=BROLL_IMAGE_ID,
        kind=AssetKind.SOURCE,
        content_type="image/jpeg",
        duration_ms=None,
        width=1600,
        height=1200,
    )


def composition_document(**overrides: Any) -> dict[str, Any]:
    """One composition in exactly the shape an Edit Revision stores."""
    document: dict[str, Any] = {
        "schemaVersion": 1,
        "sourceAssetId": str(SOURCE_ASSET_ID),
        "durationMs": 30_000,
        "canvas": {"width": 1080, "height": 1920, "background": "#000000"},
        "sourceRange": {"inMs": 5_000, "outMs": 35_000},
        "template": None,
        "brandKit": None,
        "tracks": [
            {
                "id": "main-video",
                "type": "video",
                "items": [item()],
            }
        ],
        "captions": {
            "mode": "karaoke",
            "words": [
                {"id": "w1", "startMs": 0, "endMs": 900, "text": "Ini", "speaker": "SPEAKER_00"},
                {"id": "w2", "startMs": 900, "endMs": 1_800, "text": "cara", "speaker": None},
            ],
            "style": caption_style(),
        },
        "overlays": [],
        "audio": {"gainDb": 0.0, "musicGainDb": -18.0},
        "bookmarks": [],
    }
    document.update(overrides)
    return document


def item(**overrides: Any) -> dict[str, Any]:
    """One video item on the main track."""
    values: dict[str, Any] = {
        "id": "scene-1",
        "sourceAssetId": str(SOURCE_ASSET_ID),
        "timelineStartMs": 0,
        "sourceInMs": 5_000,
        "sourceOutMs": 35_000,
        "transform": {"x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 0.0},
        "crop": None,
        "opacity": 1.0,
        "blendMode": "normal",
        "motion": "none",
        "origin": {"type": "source", "suggestionId": None, "provenanceId": None},
        "keyframes": [],
    }
    values.update(overrides)
    return values


def caption_style(**overrides: Any) -> dict[str, Any]:
    """The caption type a first Edit starts from."""
    values: dict[str, Any] = {
        "fontFamily": "Montserrat",
        "fontSize": 64,
        "color": "#FFFFFF",
        "highlightColor": "#FFD166",
        "align": "center",
        "weight": 700,
        "italic": False,
        "decoration": "none",
        "letterSpacing": 0.0,
        "lineHeight": 1.2,
        "backgroundEnabled": False,
        "backgroundColor": "#000000",
    }
    values.update(overrides)
    return values


def text_style(**overrides: Any) -> dict[str, Any]:
    """The type an overlay draws its own text in."""
    values = {key: value for key, value in caption_style().items() if key != "highlightColor"}
    values.update(overrides)
    return values


def video_overlay(**overrides: Any) -> dict[str, Any]:
    """One B-roll video placed over the main timeline."""
    values: dict[str, Any] = {
        "id": "broll-1",
        "type": "video",
        "assetId": str(BROLL_VIDEO_ID),
        "timelineStartMs": 4_000,
        "timelineEndMs": 8_000,
        "sourceInMs": 0,
        "sourceOutMs": 4_000,
        "placement": "cover",
        "opacity": 1.0,
        "blendMode": "normal",
        "motion": "none",
        "preserveDialogueAudio": True,
        "origin": {
            "type": "brollSuggestion",
            "suggestionId": str(SUGGESTION_ID),
            "provenanceId": str(PROVENANCE_ID),
        },
        "keyframes": [],
    }
    values.update(overrides)
    return values


def image_overlay(**overrides: Any) -> dict[str, Any]:
    """One still placed over the main timeline."""
    values: dict[str, Any] = {
        "id": "still-1",
        "type": "image",
        "assetId": str(BROLL_IMAGE_ID),
        "timelineStartMs": 10_000,
        "timelineEndMs": 14_000,
        "placement": "cover",
        "opacity": 1.0,
        "blendMode": "normal",
        "motion": "kenBurnsIn",
        "origin": {
            "type": "userAsset",
            "suggestionId": None,
            "provenanceId": str(PROVENANCE_ID),
        },
        "keyframes": [],
    }
    values.update(overrides)
    return values


def text_overlay(text: str, **overrides: Any) -> dict[str, Any]:
    """One text overlay a member wrote themselves."""
    values: dict[str, Any] = {
        "id": "text-1",
        "type": "text",
        "timelineStartMs": 1_000,
        "timelineEndMs": 5_000,
        "placement": "lowerThird",
        "opacity": 1.0,
        "motion": "none",
        "text": text,
        "style": text_style(),
        "keyframes": [],
    }
    values.update(overrides)
    return values


def citation_overlay(text: str, **overrides: Any) -> dict[str, Any]:
    """One citation overlay bound to the evidence it cites."""
    values: dict[str, Any] = {
        "id": "citation-1",
        "type": "citation",
        "timelineStartMs": 6_000,
        "timelineEndMs": 9_000,
        "placement": "lowerThird",
        "opacity": 1.0,
        "claimEvidenceId": str(EVIDENCE_ID),
        "text": text,
        "style": text_style(),
        "keyframes": [],
    }
    values.update(overrides)
    return values


def keyframe(at_ms: int, **overrides: Any) -> dict[str, Any]:
    """One animated value at one instant."""
    values: dict[str, Any] = {
        "atMs": at_ms,
        "easing": "linear",
        "transform": None,
        "opacity": None,
        "style": None,
    }
    values.update(overrides)
    return values


def plan_for(
    document: dict[str, Any] | None = None,
    *,
    table: dict[UUID, RenderAsset] | None = None,
    preset: RenderPreset = RenderPreset.PORTRAIT,
    watermark: Watermark | None = None,
) -> RenderPlan:
    """Compile one composition into a plan the renderer could execute."""
    composition: CompositionV1 = parse_composition(document or composition_document())
    return compile_render_plan(
        composition,
        assets=table or assets(),
        preset=preset,
        workspace=WORKSPACE,
        watermark=watermark,
    )


def refusal(document: dict[str, Any], *, table: dict[UUID, RenderAsset] | None = None) -> str:
    """Assert one composition is refused and return the stable code it carries."""
    with pytest.raises(RenderCompilationError) as error:
        plan_for(document, table=table)
    return error.value.code


@pytest.mark.unit
@pytest.mark.parametrize("preset", list(RenderPreset))
def test_every_export_preset_produces_its_own_canvas(preset: RenderPreset) -> None:
    """Section 9 names four presets, and a member may export a clip into any of them."""
    plan = plan_for(preset=preset)

    width, height = PRESET_CANVAS[preset]
    assert (plan.width, plan.height) == (width, height)
    assert f"scale={width}:{height}" in plan.filter_script
    assert plan.duration_ms == 30_000


@pytest.mark.unit
def test_a_trim_is_rendered_as_the_source_range_the_item_names() -> None:
    """The export must contain the moment the composition cut, and nothing around it."""
    document = composition_document(
        tracks=[
            {
                "id": "main-video",
                "type": "video",
                "items": [item(sourceInMs=8_000, sourceOutMs=20_000)],
            }
        ],
        durationMs=12_000,
        captions={"mode": "off", "words": [], "style": caption_style()},
    )

    plan = plan_for(document)

    assert "trim=start=8.000:end=20.000" in plan.filter_script
    assert "atrim=start=8.000:end=20.000" in plan.filter_script
    assert plan.duration_ms == 12_000


@pytest.mark.unit
def test_a_crop_is_rendered_in_pixels_of_the_source_frame() -> None:
    """A normalized crop only means something once it is resolved against real media."""
    document = composition_document(
        tracks=[
            {
                "id": "main-video",
                "type": "video",
                "items": [item(crop={"x": 0.25, "y": 0.0, "width": 0.5, "height": 1.0})],
            }
        ]
    )

    plan = plan_for(document)

    assert "crop=w=960:h=1080:x=480:y=0" in plan.filter_script


@pytest.mark.unit
def test_captions_are_written_as_a_subtitle_file_the_graph_reads() -> None:
    """Caption text is content, so it travels as a file rather than as filter syntax."""
    plan = plan_for()

    subtitles = next(entry for entry in plan.files if entry.path.suffix == ".ass")
    assert "Dialogue: 0,0:00:00.00,0:00:01.80" in subtitles.contents
    assert "Ini" in subtitles.contents
    assert "cara" in subtitles.contents
    assert f"subtitles=filename={_escaped(subtitles.path)}" in plan.filter_script


@pytest.mark.unit
def test_karaoke_captions_carry_the_highlight_colour_and_block_captions_do_not() -> None:
    """Karaoke is a caption mode, not a second caption document."""
    karaoke = plan_for()
    block = plan_for(
        composition_document(
            captions={
                "mode": "block",
                "words": composition_document()["captions"]["words"],
                "style": caption_style(),
            }
        )
    )

    karaoke_file = next(entry for entry in karaoke.files if entry.path.suffix == ".ass")
    block_file = next(entry for entry in block.files if entry.path.suffix == ".ass")
    assert "&H0066D1FF" in karaoke_file.contents
    assert "&H0066D1FF" not in block_file.contents


@pytest.mark.unit
def test_captions_switched_off_produce_no_subtitle_stage_at_all() -> None:
    """A member who turned captions off must not get them burned in anyway."""
    plan = plan_for(
        composition_document(captions={"mode": "off", "words": [], "style": caption_style()})
    )

    assert "subtitles=" not in plan.filter_script
    assert all(entry.path.suffix != ".ass" for entry in plan.files)


@pytest.mark.unit
def test_member_text_never_reaches_a_filter_argument() -> None:
    """FFmpeg's filter syntax is a language; a member's words are not written in it."""
    document = composition_document(
        overlays=[text_overlay(HOSTILE_TEXT), citation_overlay(HOSTILE_TEXT)],
        captions={
            "mode": "karaoke",
            "words": [
                {
                    "id": "w1",
                    "startMs": 0,
                    "endMs": 900,
                    "text": HOSTILE_TEXT.replace("\n", " "),
                    "speaker": None,
                }
            ],
            "style": caption_style(),
        },
    )

    plan = plan_for(document, table=assets())

    assert HOSTILE_TEXT not in plan.filter_script
    for fragment in ("drop;", "{\\an8}", "%s", "'quoted'"):
        assert fragment not in plan.filter_script
    text_files = [entry for entry in plan.files if entry.path.suffix == ".txt"]
    assert len(text_files) == 2
    assert all(entry.contents.startswith("Rp1.000") for entry in text_files)
    assert f"textfile={_escaped(text_files[0].path)}" in plan.filter_script


@pytest.mark.unit
def test_caption_text_is_escaped_where_the_subtitle_format_would_read_it() -> None:
    """An ASS override block inside a caption would restyle the whole export."""
    document = composition_document(
        captions={
            "mode": "karaoke",
            "words": [
                {
                    "id": "w1",
                    "startMs": 0,
                    "endMs": 900,
                    "text": "{\\an8}Ini",
                    "speaker": None,
                }
            ],
            "style": caption_style(),
        }
    )

    plan = plan_for(document)

    subtitles = next(entry for entry in plan.files if entry.path.suffix == ".ass")
    dialogue = next(
        line for line in subtitles.contents.splitlines() if line.startswith("Dialogue:")
    )
    text = dialogue.split(",", 9)[-1]
    assert "{\\an8}" not in text
    assert "\\an8" not in text
    assert "an8" in text
    assert "Ini" in text


@pytest.mark.unit
def test_a_watermark_is_drawn_from_a_file_like_any_other_text() -> None:
    """Brand text is text, and it earns no exception from the escaping rule."""
    plan = plan_for(watermark=Watermark(text="clipah.com"))

    watermark = next(entry for entry in plan.files if entry.path.name.startswith("watermark"))
    assert watermark.contents == "clipah.com"
    assert f"drawtext=textfile={_escaped(watermark.path)}" in plan.filter_script


@pytest.mark.unit
def test_audio_gain_is_applied_to_the_dialogue_the_clip_carries() -> None:
    """A member who lowered the dialogue must hear that in the file they publish."""
    plan = plan_for(composition_document(audio={"gainDb": -6.0, "musicGainDb": -18.0}))

    assert "volume=-6.00dB" in plan.filter_script


@pytest.mark.unit
def test_a_video_overlay_plays_only_inside_its_own_window() -> None:
    """B-roll covers a moment; outside that moment the source has to be visible again."""
    plan = plan_for(
        composition_document(overlays=[video_overlay()]),
        table=assets(broll_video()),
    )

    assert "overlay=" in plan.filter_script
    assert "enable='between(t,4.000,8.000)'" in plan.filter_script
    assert len(plan.inputs) == 2
    assert plan.inputs[1].asset_id == BROLL_VIDEO_ID
    assert plan.inputs[1].loop_image is False


@pytest.mark.unit
def test_an_image_overlay_is_looped_for_exactly_as_long_as_it_is_on_screen() -> None:
    """A still has no duration of its own, so the plan has to give it one."""
    plan = plan_for(
        composition_document(overlays=[image_overlay()]),
        table=assets(broll_image()),
    )

    still = plan.inputs[1]
    assert still.loop_image is True
    assert still.duration_ms == 4_000
    assert still.arguments()[:4] == ("-loop", "1", "-t", "4.000")
    assert "zoompan=" in plan.filter_script


@pytest.mark.unit
def test_preserving_dialogue_audio_keeps_the_speaker_and_mutes_the_broll() -> None:
    """The plan's promise is in its name: the dialogue survives the overlay."""
    plan = plan_for(
        composition_document(overlays=[video_overlay(preserveDialogueAudio=True)]),
        table=assets(broll_video()),
    )

    assert "amix" not in plan.filter_script
    assert "volume=enable=" not in plan.filter_script


@pytest.mark.unit
def test_not_preserving_dialogue_audio_ducks_the_speaker_under_the_broll() -> None:
    """An overlay that replaces the dialogue must actually replace it."""
    plan = plan_for(
        composition_document(overlays=[video_overlay(preserveDialogueAudio=False)]),
        table=assets(broll_video()),
    )

    assert "volume=enable='between(t,4.000,8.000)':volume=0" in plan.filter_script
    assert "adelay=4000|4000" in plan.filter_script
    assert "amix=inputs=2" in plan.filter_script


@pytest.mark.unit
def test_provenance_and_evidence_references_never_enter_the_command() -> None:
    """A composition stores references to records; a render reads media, not identifiers."""
    document = composition_document(
        overlays=[video_overlay(), image_overlay(), citation_overlay("Source: Example, 2026")]
    )

    plan = plan_for(document, table=assets(broll_video(), broll_image()))

    rendered = plan.filter_script + json.dumps([entry.contents for entry in plan.files])
    for identifier in (str(SUGGESTION_ID), str(PROVENANCE_ID), str(EVIDENCE_ID)):
        assert identifier not in rendered


@pytest.mark.unit
def test_transform_keyframes_become_a_piecewise_expression_over_time() -> None:
    """A keyframed overlay moves; a plan that ignored that would export a still one."""
    document = composition_document(
        overlays=[
            video_overlay(
                keyframes=[
                    keyframe(0, transform={"x": 0.2, "y": 0.5, "scale": 1.0, "rotation": 0.0}),
                    keyframe(4_000, transform={"x": 0.8, "y": 0.5, "scale": 1.0, "rotation": 0.0}),
                ]
            )
        ]
    )

    plan = plan_for(document, table=assets(broll_video()))

    assert "x='if(lt(t,8.000)" in plan.filter_script
    assert "lerp" in plan.filter_script or "+(" in plan.filter_script


@pytest.mark.unit
def test_opacity_keyframes_animate_the_overlay_alpha() -> None:
    """Fading a clip in is an opacity animation, not a second copy of the media."""
    document = composition_document(
        overlays=[video_overlay(keyframes=[keyframe(0, opacity=0.0), keyframe(1_000, opacity=1.0)])]
    )

    plan = plan_for(document, table=assets(broll_video()))

    assert "colorchannelmixer=aa=" in plan.filter_script


@pytest.mark.unit
@pytest.mark.parametrize(
    "document",
    [
        pytest.param(
            composition_document(
                overlays=[
                    video_overlay(
                        keyframes=[
                            keyframe(
                                0, transform={"x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 0}
                            ),
                            keyframe(
                                2_000, transform={"x": 0.5, "y": 0.5, "scale": 2.0, "rotation": 0}
                            ),
                        ]
                    )
                ]
            ),
            id="scale-keyframe",
        ),
        pytest.param(
            composition_document(overlays=[video_overlay(blendMode="multiply")]),
            id="blend-mode",
        ),
        pytest.param(
            composition_document(overlays=[video_overlay(motion="slideUp")]),
            id="motion-preset",
        ),
        pytest.param(
            composition_document(
                tracks=[
                    {
                        "id": "main-video",
                        "type": "video",
                        "items": [
                            item(
                                keyframes=[
                                    keyframe(
                                        0,
                                        transform={
                                            "x": 0.4,
                                            "y": 0.5,
                                            "scale": 1.0,
                                            "rotation": 0.0,
                                        },
                                    ),
                                    keyframe(
                                        1_000,
                                        transform={
                                            "x": 0.6,
                                            "y": 0.5,
                                            "scale": 1.0,
                                            "rotation": 0.0,
                                        },
                                    ),
                                ]
                            )
                        ],
                    }
                ]
            ),
            id="base-item-keyframe",
        ),
    ],
)
def test_a_feature_this_renderer_cannot_reproduce_is_refused(document: dict[str, Any]) -> None:
    """An export that quietly drops an effect is worse than one that refuses to start."""
    assert refusal(document, table=assets(broll_video())) == FEATURE_UNSUPPORTED


@pytest.mark.unit
def test_an_asset_the_render_was_not_given_is_refused() -> None:
    """A plan may only read media the caller proved this Project owns."""
    document = composition_document(overlays=[video_overlay()])

    assert refusal(document) == ASSET_MISSING


@pytest.mark.unit
def test_every_path_in_a_plan_lives_inside_the_job_workspace() -> None:
    """One render's files belong to one Job, and nothing it writes escapes that directory."""
    plan = plan_for(
        composition_document(
            overlays=[video_overlay(), image_overlay(), text_overlay("Hello")],
        ),
        table=assets(broll_video(), broll_image()),
        watermark=Watermark(text="clipah.com"),
    )

    for path in plan.paths():
        assert path.is_absolute()
        assert path.is_relative_to(WORKSPACE)


@pytest.mark.unit
def test_the_command_is_an_argument_vector_with_no_shell_and_no_member_text() -> None:
    """A render is one process with one argument list, never a string a shell parses."""
    plan = plan_for(
        composition_document(overlays=[text_overlay(HOSTILE_TEXT)]),
        watermark=Watermark(text="clipah.com"),
    )
    output = WORKSPACE / "render.mp4"

    arguments = build_render_arguments(plan, output=output, ffmpeg_path="ffmpeg")

    assert arguments[0] == "ffmpeg"
    script = next(
        arguments[index + 1]
        for index, value in enumerate(arguments)
        if value == "-filter_complex_script"
    )
    assert Path(script).is_relative_to(WORKSPACE)
    for argument in arguments:
        assert "drop;" not in argument
        assert "&&" not in argument
        assert argument != ""
    assert "-map" in arguments
    assert f"[{plan.video_label}]" in arguments
    assert "libx264" in arguments
    assert "yuv420p" in arguments
    assert "+faststart" in arguments
    assert str(output) == arguments[-1]


@pytest.mark.unit
def test_the_filter_script_is_written_to_a_file_rather_than_an_argument() -> None:
    """A long graph in an argument is both fragile and unreadable in a process list."""
    plan = plan_for()
    output = WORKSPACE / "render.mp4"

    arguments = build_render_arguments(plan, output=output, ffmpeg_path="ffmpeg")

    assert "-filter_complex" not in arguments
    assert "-filter_complex_script" in arguments
    assert plan.filter_script.count("[") > 0


@pytest.mark.unit
def test_the_input_path_of_one_asset_is_deterministic_and_workspace_local() -> None:
    """The compiler and the worker have to agree on where a downloaded asset lands."""
    asset_id = uuid4()

    path = input_path(WORKSPACE, asset_id)

    assert path == input_path(WORKSPACE, asset_id)
    assert path.is_relative_to(WORKSPACE)
    assert str(asset_id) in path.name


def _escaped(path: Path) -> str:
    """Spell one path the way the compiler writes it into a filter argument."""
    return str(path).replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


@pytest.mark.unit
def test_two_items_are_concatenated_into_one_continuous_export() -> None:
    """A split clip is still one file, and the join has to be in the graph."""
    document = composition_document(
        tracks=[
            {
                "id": "main-video",
                "type": "video",
                "items": [
                    item(id="scene-1", sourceInMs=5_000, sourceOutMs=20_000),
                    item(
                        id="scene-2",
                        timelineStartMs=15_000,
                        sourceInMs=20_000,
                        sourceOutMs=35_000,
                    ),
                ],
            }
        ]
    )

    plan = plan_for(document)

    assert "concat=n=2:v=1:a=1[vbase][abase]" in plan.filter_script
    assert len(plan.inputs) == 2


@pytest.mark.unit
def test_a_composition_with_no_video_track_cannot_be_rendered() -> None:
    """There is nothing to encode, and pretending otherwise would produce a black file."""
    document = composition_document(
        tracks=[{"id": "music", "type": "music", "items": []}],
        captions={"mode": "off", "words": [], "style": caption_style()},
    )

    assert refusal(document) == FEATURE_UNSUPPORTED


@pytest.mark.unit
def test_a_music_track_is_delayed_to_its_own_moment_and_mixed_under_the_dialogue() -> None:
    """Music is placed on the timeline, so it starts where the composition says it does."""
    document = composition_document(
        tracks=[
            *composition_document()["tracks"],
            {
                "id": "music",
                "type": "music",
                "items": [
                    item(
                        id="bed-1",
                        sourceAssetId=str(BROLL_VIDEO_ID),
                        timelineStartMs=2_000,
                        sourceInMs=0,
                        sourceOutMs=6_000,
                    )
                ],
            },
        ]
    )

    plan = plan_for(document, table=assets(broll_video()))

    assert "adelay=2000|2000" in plan.filter_script
    assert "volume=-18.00dB" in plan.filter_script
    assert "amix=inputs=2" in plan.filter_script


@pytest.mark.unit
def test_extracted_audio_is_mixed_at_the_dialogue_gain_rather_than_the_music_gain() -> None:
    """Audio extracted from speech is dialogue, so a music bed's gain would mis-level it."""
    document = composition_document(
        audio={"gainDb": -6.0, "musicGainDb": -18.0},
        tracks=[
            *composition_document()["tracks"],
            {
                "id": "extracted-1",
                "type": "extractedAudio",
                "items": [
                    item(
                        id="extract-1",
                        sourceAssetId=str(BROLL_VIDEO_ID),
                        timelineStartMs=3_000,
                        sourceInMs=0,
                        sourceOutMs=6_000,
                    )
                ],
            },
        ],
    )

    plan = plan_for(document, table=assets(broll_video()))

    assert "adelay=3000|3000" in plan.filter_script
    assert plan.filter_script.count("volume=-6.00dB") == 2
    assert "-18.00dB" not in plan.filter_script
    assert "amix=inputs=2" in plan.filter_script


@pytest.mark.unit
def test_a_gap_between_two_video_items_is_refused_rather_than_silently_closed() -> None:
    """Concatenation would move the second item earlier than the preview showed it."""
    document = composition_document(
        tracks=[
            {
                "id": "main-video",
                "type": "video",
                "items": [
                    item(id="scene-1", sourceInMs=5_000, sourceOutMs=20_000),
                    item(
                        id="scene-2",
                        timelineStartMs=16_000,
                        sourceInMs=20_000,
                        sourceOutMs=34_000,
                    ),
                ],
            }
        ]
    )

    assert refusal(document) == FEATURE_UNSUPPORTED


@pytest.mark.unit
def test_a_base_timeline_that_does_not_start_at_zero_is_refused() -> None:
    """A clip that begins with a hole would begin early in the file instead."""
    document = composition_document(
        durationMs=35_000,
        tracks=[
            {
                "id": "main-video",
                "type": "video",
                "items": [
                    item(id="scene-1", timelineStartMs=5_000, sourceInMs=5_000, sourceOutMs=35_000)
                ],
            }
        ],
    )

    assert refusal(document) == FEATURE_UNSUPPORTED


@pytest.mark.unit
def test_a_second_video_track_is_refused_because_this_renderer_plays_one_lane() -> None:
    """Two video lanes are composited, not concatenated, and this graph concatenates."""
    document = composition_document(
        tracks=[
            *composition_document()["tracks"],
            {
                "id": "second-video",
                "type": "video",
                "items": [
                    item(
                        id="scene-2",
                        sourceAssetId=str(BROLL_VIDEO_ID),
                        timelineStartMs=0,
                        sourceInMs=0,
                        sourceOutMs=6_000,
                    )
                ],
            },
        ]
    )

    assert refusal(document, table=assets(broll_video())) == FEATURE_UNSUPPORTED


@pytest.mark.unit
def test_a_duplicated_item_is_rendered_as_its_own_segment_of_the_export() -> None:
    """Duplication is three plays of the same media, so the graph carries three segments."""
    document = composition_document(
        tracks=[
            {
                "id": "main-video",
                "type": "video",
                "items": [
                    item(id="scene-1", sourceInMs=5_000, sourceOutMs=15_000),
                    item(
                        id="scene-1-copy",
                        timelineStartMs=10_000,
                        sourceInMs=5_000,
                        sourceOutMs=15_000,
                    ),
                    item(
                        id="scene-2",
                        timelineStartMs=20_000,
                        sourceInMs=15_000,
                        sourceOutMs=25_000,
                    ),
                ],
            }
        ]
    )

    plan = plan_for(document)

    assert "concat=n=3:v=1:a=1[vbase][abase]" in plan.filter_script
    assert len(plan.inputs) == 3


@pytest.mark.unit
def test_a_faded_overlay_fades_its_alpha_in_and_out() -> None:
    """A fade is an alpha animation the renderer can reproduce exactly."""
    document = composition_document(overlays=[video_overlay(motion="fade")])

    plan = plan_for(document, table=assets(broll_video()))

    assert "fade=t=in:st=0:d=0.3:alpha=1" in plan.filter_script
    assert "fade=t=out:st=3.700:d=0.3:alpha=1" in plan.filter_script


@pytest.mark.unit
def test_a_semi_transparent_overlay_keeps_its_own_opacity() -> None:
    """An overlay a member dimmed must be dimmed in the export too."""
    document = composition_document(overlays=[video_overlay(opacity=0.5)])

    plan = plan_for(document, table=assets(broll_video()))

    assert "colorchannelmixer=aa=0.500" in plan.filter_script


@pytest.mark.unit
@pytest.mark.parametrize(
    ("motion", "expected"),
    [("kenBurnsOut", "max(1.35"), ("panLeft", "-on)*2"), ("panRight", "(on)*2")],
)
def test_every_supported_still_motion_becomes_a_zoompan_expression(
    motion: str, expected: str
) -> None:
    """A still that drifts is the cheapest way to make B-roll feel deliberate."""
    document = composition_document(overlays=[image_overlay(motion=motion)])

    plan = plan_for(document, table=assets(broll_image()))

    assert expected in plan.filter_script


@pytest.mark.unit
def test_a_rotation_keyframe_is_refused_rather_than_flattened() -> None:
    """Rotating an overlay over time is an effect this renderer cannot reproduce."""
    document = composition_document(
        overlays=[
            video_overlay(
                keyframes=[
                    keyframe(0, transform={"x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 0.0}),
                    keyframe(2_000, transform={"x": 0.5, "y": 0.5, "scale": 1.0, "rotation": 45.0}),
                ]
            )
        ]
    )

    assert refusal(document, table=assets(broll_video())) == FEATURE_UNSUPPORTED


@pytest.mark.unit
def test_a_keyframed_text_overlay_is_refused() -> None:
    """Animated type belongs to the advanced editor, and is refused rather than dropped."""
    document = composition_document(
        overlays=[
            text_overlay(
                "Hello",
                keyframes=[keyframe(0, opacity=0.0), keyframe(500, opacity=1.0)],
            )
        ]
    )

    assert refusal(document) == FEATURE_UNSUPPORTED


@pytest.mark.unit
def test_a_text_overlay_with_an_unsupported_motion_is_refused() -> None:
    """Type that slides in is an effect the export would otherwise silently drop."""
    document = composition_document(overlays=[text_overlay("Hello", motion="kenBurnsIn")])

    assert refusal(document) == FEATURE_UNSUPPORTED


@pytest.mark.unit
def test_a_crop_of_media_with_no_known_dimensions_is_refused() -> None:
    """A normalized crop is meaningless until the frame it applies to is known."""
    document = composition_document(
        tracks=[
            {
                "id": "main-video",
                "type": "video",
                "items": [item(crop={"x": 0.0, "y": 0.0, "width": 0.5, "height": 1.0})],
            }
        ]
    )

    with pytest.raises(RenderCompilationError) as error:
        plan_for(document, table={SOURCE_ASSET_ID: source_asset(width=None, height=None)})

    assert error.value.code == FEATURE_UNSUPPORTED


@pytest.mark.unit
@pytest.mark.parametrize(
    ("align", "expected"), [("left", "x=64"), ("right", "x=w-tw-64"), ("center", "x=(w-tw)/2")]
)
def test_drawn_text_is_placed_where_its_own_style_asks(align: str, expected: str) -> None:
    """Alignment is a member's decision, and the export honours it."""
    document = composition_document(overlays=[text_overlay("Hello", style=text_style(align=align))])

    assert expected in plan_for(document).filter_script


@pytest.mark.unit
@pytest.mark.parametrize(
    ("placement", "expected"),
    [("top", "y=h*0.12"), ("center", "y=(h-th)/2"), ("lowerThird", "y=h*0.75")],
)
def test_drawn_text_sits_where_the_overlay_was_placed(placement: str, expected: str) -> None:
    """Placement decides where type lands, and a citation belongs at the bottom."""
    document = composition_document(overlays=[text_overlay("Hello", placement=placement)])

    assert expected in plan_for(document).filter_script


@pytest.mark.unit
def test_a_text_overlay_with_a_background_draws_its_own_box() -> None:
    """A caption box is what makes type readable over bright footage."""
    document = composition_document(
        overlays=[text_overlay("Hello", style=text_style(backgroundEnabled=True))]
    )

    assert "box=1:boxcolor=0x000000@0.6" in plan_for(document).filter_script


@pytest.mark.unit
def test_a_plan_naming_a_file_outside_the_job_workspace_is_refused_before_it_runs(
    tmp_path: Path,
) -> None:
    """A render writes only inside its own Job directory, whatever a plan claims."""
    from clipah.renders.ffmpeg_renderer import RenderExecutionError, _write_plan_files
    from clipah.renders.models import RenderFile

    plan = plan_for()
    escaped = RenderFile(path=tmp_path.parent / "escaped.txt", contents="nope")
    hostile = RenderPlan(
        preset=plan.preset,
        width=plan.width,
        height=plan.height,
        frame_rate=plan.frame_rate,
        duration_ms=plan.duration_ms,
        inputs=plan.inputs,
        filter_script=plan.filter_script,
        files=(escaped,),
        video_label=plan.video_label,
        audio_label=plan.audio_label,
    )

    with pytest.raises(RenderExecutionError):
        _write_plan_files(hostile, tmp_path)

    assert not escaped.path.exists()


@pytest.mark.unit
def test_a_rendered_file_shorter_than_the_composition_is_a_failure_not_a_delivery(
    tmp_path: Path,
) -> None:
    """A truncated export would be published as though it were the approved clip."""
    from clipah.renders.ffmpeg_renderer import FFmpegRenderer, RenderExecutionError

    plan = _plan_in(tmp_path)
    executor = _RecordingExecutor(tmp_path)

    with pytest.raises(RenderExecutionError) as refused:
        FFmpegRenderer(executor=executor, duration_probe=lambda _path: 1_000).render(
            plan,
            workspace=tmp_path,
            cancellation_check=lambda: None,
            progress=lambda _ratio: None,
        )

    assert refused.value.code == "RENDER_DURATION_MISMATCH"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("version_line", "expected"),
    [
        ("ffmpeg version 7.1.5-0+deb13u1 Copyright", "-filter_complex_script"),
        ("ffmpeg version 9.0.1 Copyright", "-/filter_complex"),
        ("unreadable", "-filter_complex_script"),
    ],
)
def test_the_renderer_hands_the_graph_over_the_way_this_ffmpeg_expects(
    tmp_path: Path, version_line: str, expected: str
) -> None:
    """The pinned tool and a newer local build read the same file under different flags."""
    from clipah.renders.ffmpeg_renderer import FFmpegRenderer

    plan = _plan_in(tmp_path)
    executor = _RecordingExecutor(tmp_path, version_line=version_line)

    FFmpegRenderer(executor=executor, duration_probe=lambda _path: plan.duration_ms).render(
        plan,
        workspace=tmp_path,
        cancellation_check=lambda: None,
        progress=lambda _ratio: None,
    )

    assert expected in executor.commands[-1]


def _plan_in(workspace: Path) -> RenderPlan:
    """Compile the reference composition for one real directory on disk."""
    return compile_render_plan(
        parse_composition(composition_document()),
        assets=assets(),
        preset=RenderPreset.PORTRAIT,
        workspace=workspace,
    )


class _RecordingExecutor:
    """Answer the version query and pretend every render succeeded."""

    def __init__(self, workspace: Path, *, version_line: str = "ffmpeg version 7.1.5 Copyright"):
        """Bind the workspace an output is written into and the version to report."""
        self.commands: list[tuple[str, ...]] = []
        self._workspace = workspace
        self._version_line = version_line

    def run(
        self,
        arguments: Any,
        *,
        timeout_seconds: float,
        cancellation_check: Any,
        progress_duration_ms: int | None = None,
        progress: Any = None,
    ) -> bytes:
        """Record one command, and write the output file a real encoder would."""
        command = tuple(arguments)
        self.commands.append(command)
        if "-version" in command:
            return f"{self._version_line}\n".encode()
        (self._workspace / "render.mp4").write_bytes(b"rendered")
        return b""
