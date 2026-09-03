/**
 * The playback boundary the editor talks to, and the one implementation it ships with.
 *
 * The bake-off in Task 21 selected Mediabunny, and the ADR's whole point was that the
 * engine sits behind a port rather than inside the screen. The basic editor needs
 * bounded proxy playback and nothing more, so this port is small and the implementation
 * behind it is the browser's own media element. Frame-accurate compositing — captions,
 * crop, and overlays decoded into one canvas — is the advanced editor's, and it arrives
 * as a second implementation of this port rather than as a change to the screen.
 */

/** One time-bounded proxy the editor may play. */
export interface PreviewSource {
  url: string
  durationMs: number | null
  width: number | null
  height: number | null
}

/** What the editor asks of whatever is drawing the preview. */
export interface PreviewEngine {
  attach: (element: HTMLVideoElement | null) => void
  seek: (sourceMs: number) => void
  play: () => void
  pause: () => void
  isPlaying: () => boolean
  currentSourceMs: () => number
  dispose: () => void
}

/**
 * Play the proxy with the browser's own media element.
 *
 * Every call is tolerant of a detached element, because React mounts and unmounts the
 * video around this engine and a preview that throws during teardown would take the
 * editor with it.
 */
export function htmlVideoPreviewEngine(): PreviewEngine {
  let element: HTMLVideoElement | null = null
  return {
    attach: (next) => {
      element = next
    },
    seek: (sourceMs) => {
      if (element !== null) {
        element.currentTime = Math.max(0, sourceMs) / 1000
      }
    },
    play: () => {
      void element?.play?.()
    },
    pause: () => {
      element?.pause?.()
    },
    isPlaying: () => element !== null && !element.paused,
    currentSourceMs: () => Math.round((element?.currentTime ?? 0) * 1000),
    dispose: () => {
      element?.pause?.()
      element = null
    },
  }
}
