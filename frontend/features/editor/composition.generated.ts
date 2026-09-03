/**
 * How caption words are drawn, if they are drawn at all.
 */
export type CaptionMode = "off" | "block" | "karaoke";
/**
 * Horizontal alignment for caption and text-overlay type.
 */
export type TextAlign = "left" | "center" | "right";
/**
 * The decorations a supported renderer can draw on text.
 */
export type TextDecoration = "none" | "underline" | "strikethrough";
/**
 * The fonts Clipah ships to both the browser preview and the render worker.
 */
export type FontFamily =
  "Inter" | "Montserrat" | "Poppins" | "Roboto" | "Open Sans" | "Bebas Neue" | "Anton" | "Nunito";
/**
 * The compositing modes both the browser preview and FFmpeg can reproduce.
 */
export type BlendMode = "normal" | "multiply" | "screen" | "overlay" | "darken" | "lighten";
/**
 * How a value travels between two keyframes.
 */
export type Easing = "linear" | "easeIn" | "easeOut" | "easeInOut";
/**
 * The named animations an item or overlay may be given.
 */
export type MotionPreset = "none" | "kenBurnsIn" | "kenBurnsOut" | "panLeft" | "panRight" | "slideUp" | "fade";
/**
 * Where the media in one item or overlay came from.
 */
export type OriginType = "source" | "brollSuggestion" | "userAsset" | "generated";
/**
 * Where an overlay sits inside the canvas.
 */
export type Placement = "cover" | "pictureInPicture" | "lowerThird" | "top" | "center";
/**
 * The kinds of timeline lane a composition may carry.
 */
export type TrackType = "video" | "audio" | "music" | "extractedAudio";

/**
 * One immutable editing decision set, valid on its own terms.
 */
export interface CompositionV1 {
  audio: AudioMix;
  bookmarks: Bookmark[];
  brandKit: BrandKitReference | null;
  canvas: Canvas;
  captions: Captions;
  durationMs: number;
  overlays: (VideoOverlay | ImageOverlay | TextOverlay | CitationOverlay)[];
  schemaVersion: 1;
  sourceAssetId: string;
  sourceRange: SourceRange;
  template: TemplateReference | null;
  tracks: Track[];
}
/**
 * The two gains a version 1 composition can set.
 */
export interface AudioMix {
  gainDb: number;
  musicGainDb: number;
}
/**
 * A member's marker on the timeline.
 */
export interface Bookmark {
  id: string;
  label: string;
  timelineMs: number;
}
/**
 * The exact Brand Kit version this composition was built against.
 */
export interface BrandKitReference {
  id: string;
  logoAssetId: string | null;
  version: number;
}
/**
 * The output frame every item is composited into.
 */
export interface Canvas {
  background: string;
  height: number;
  width: number;
}
/**
 * The caption layer, its words, and the type they are drawn in.
 */
export interface Captions {
  mode: CaptionMode;
  style: CaptionStyle;
  words: CaptionWord[];
}
/**
 * Caption type, plus the colour karaoke highlighting paints the active word.
 */
export interface CaptionStyle {
  align: TextAlign;
  backgroundColor: string;
  backgroundEnabled: boolean;
  color: string;
  decoration: TextDecoration;
  fontFamily: FontFamily;
  fontSize: number;
  highlightColor: string;
  italic: boolean;
  letterSpacing: number;
  lineHeight: number;
  weight: number;
}
/**
 * One transcript word as it is drawn, timed against the composition.
 */
export interface CaptionWord {
  endMs: number;
  id: string;
  speaker: string | null;
  startMs: number;
  text: string;
}
/**
 * A B-roll or supporting video placed over the main timeline.
 */
export interface VideoOverlay {
  assetId: string;
  blendMode: BlendMode;
  id: string;
  keyframes: Keyframe[];
  motion: MotionPreset;
  opacity: number;
  origin: Origin;
  placement: Placement;
  preserveDialogueAudio: boolean;
  sourceInMs: number;
  sourceOutMs: number;
  timelineEndMs: number;
  timelineStartMs: number;
  type: "video";
}
/**
 * One animated value at one instant, relative to the element that carries it.
 */
export interface Keyframe {
  atMs: number;
  easing: Easing;
  opacity: number | null;
  style: TextStyle | null;
  transform: Transform | null;
}
/**
 * Every type attribute Section 9 requires the editor to expose.
 */
export interface TextStyle {
  align: TextAlign;
  backgroundColor: string;
  backgroundEnabled: boolean;
  color: string;
  decoration: TextDecoration;
  fontFamily: FontFamily;
  fontSize: number;
  italic: boolean;
  letterSpacing: number;
  lineHeight: number;
  weight: number;
}
/**
 * The position, scale, and rotation of one visual element.
 */
export interface Transform {
  rotation: number;
  scale: number;
  x: number;
  y: number;
}
/**
 * Where one element's media came from, and the records that attribute it.
 */
export interface Origin {
  provenanceId: string | null;
  suggestionId: string | null;
  type: OriginType;
}
/**
 * A still image placed over the main timeline.
 */
export interface ImageOverlay {
  assetId: string;
  blendMode: BlendMode;
  id: string;
  keyframes: Keyframe[];
  motion: MotionPreset;
  opacity: number;
  origin: Origin;
  placement: Placement;
  timelineEndMs: number;
  timelineStartMs: number;
  type: "image";
}
/**
 * Type the member wrote, drawn rather than loaded.
 */
export interface TextOverlay {
  id: string;
  keyframes: Keyframe[];
  motion: MotionPreset;
  opacity: number;
  placement: Placement;
  style: TextStyle;
  text: string;
  timelineEndMs: number;
  timelineStartMs: number;
  type: "text";
}
/**
 * An on-screen source note bound to the evidence record it cites.
 */
export interface CitationOverlay {
  claimEvidenceId: string;
  id: string;
  keyframes: Keyframe[];
  opacity: number;
  placement: Placement;
  style: TextStyle;
  text: string;
  timelineEndMs: number;
  timelineStartMs: number;
  type: "citation";
}
/**
 * The span of the source asset this clip was cut from.
 */
export interface SourceRange {
  inMs: number;
  outMs: number;
}
/**
 * The exact template version this composition was built against.
 */
export interface TemplateReference {
  id: string;
  version: number;
}
/**
 * One lane of the timeline, playing one item at a time.
 */
export interface Track {
  id: string;
  items: TrackItem[];
  type: TrackType;
}
/**
 * One slice of a source asset placed on a timeline lane.
 */
export interface TrackItem {
  blendMode: BlendMode;
  crop: Crop | null;
  id: string;
  keyframes: Keyframe[];
  motion: MotionPreset;
  opacity: number;
  origin: Origin;
  sourceAssetId: string;
  sourceInMs: number;
  sourceOutMs: number;
  timelineStartMs: number;
  transform: Transform;
}
/**
 * A normalized crop rectangle inside the source frame.
 */
export interface Crop {
  height: number;
  width: number;
  x: number;
  y: number;
}
