/**
 * The Signal palette as data.
 *
 * `app/globals.css` stores these values as RGB triplets for Tailwind; this table is the
 * readable copy a test holds the stylesheet to, and the place contrast is proven rather
 * than eyeballed. Variable names follow shadcn so every existing class adopts Signal.
 */
export const SIGNAL_TOKENS = {
  background: '#0E0E10',
  foreground: '#F2F2EE',
  card: '#16161A',
  'card-foreground': '#F2F2EE',
  popover: '#26262C',
  'popover-foreground': '#F2F2EE',
  primary: '#C6FF3D',
  'primary-hover': '#D4FF66',
  'primary-foreground': '#0E0E10',
  'primary-soft': '#2B321E',
  secondary: '#1E1E23',
  'secondary-foreground': '#F2F2EE',
  muted: '#1E1E23',
  'muted-foreground': '#A1A1A8',
  'subtle-foreground': '#8F8F98',
  accent: '#1E1E23',
  'accent-foreground': '#F2F2EE',
  destructive: '#FF5A50',
  'destructive-foreground': '#0E0E10',
  'destructive-soft': '#372022',
  warning: '#FFB020',
  'warning-soft': '#372C1B',
  success: '#C6FF3D',
  'success-soft': '#2B321E',
  info: '#A1A1A8',
  'info-soft': '#1E1E23',
  border: '#2A2A30',
  'line-strong': '#3A3A42',
  input: '#72727C',
  ring: '#C6FF3D',
  stage: '#0A0A0B',
} as const

export type SignalToken = keyof typeof SIGNAL_TOKENS

const SURFACES = ['background', 'card', 'secondary', 'popover'] as const satisfies readonly SignalToken[]
const TEXTS = [
  'foreground',
  'muted-foreground',
  'subtle-foreground',
  'primary',
  'destructive',
  'warning',
] as const satisfies readonly SignalToken[]

/** Every text colour against every surface text is placed on. */
export const TEXT_ON_SURFACE_PAIRS: ReadonlyArray<readonly [SignalToken, SignalToken]> =
  TEXTS.flatMap((text) => SURFACES.map((surface) => [text, surface] as const))

/** Text drawn on a filled control or a tinted banner. */
export const SOLID_PAIRS: ReadonlyArray<readonly [SignalToken, SignalToken]> = [
  ['primary-foreground', 'primary'],
  ['primary-foreground', 'primary-hover'],
  ['destructive-foreground', 'destructive'],
  ['foreground', 'primary-soft'],
  ['primary', 'primary-soft'],
  ['foreground', 'destructive-soft'],
  ['destructive', 'destructive-soft'],
  ['foreground', 'warning-soft'],
  ['warning', 'warning-soft'],
]

/** A control's border against every surface a control sits on. */
export const BOUNDARY_PAIRS: ReadonlyArray<readonly [SignalToken, SignalToken]> = SURFACES.map(
  (surface) => ['input', surface] as const,
)

/** Split `#RRGGBB` into its three channels. */
export function hexToRgb(hex: string): [number, number, number] {
  const match = /^#([0-9A-F]{2})([0-9A-F]{2})([0-9A-F]{2})$/i.exec(hex)
  if (match === null) {
    throw new Error(`not a six-digit hex colour: ${hex}`)
  }
  return [parseInt(match[1] ?? '0', 16), parseInt(match[2] ?? '0', 16), parseInt(match[3] ?? '0', 16)]
}

/** The WCAG 2.2 contrast ratio between two opaque colours. */
export function contrastRatio(first: string, second: string): number {
  const [lighter, darker] = [luminance(first), luminance(second)].sort((a, b) => b - a) as [
    number,
    number,
  ]
  return (lighter + 0.05) / (darker + 0.05)
}

/** Hue in degrees and saturation in percent, as HSL reports them. */
export function hslSaturationAndHue(hex: string): { hue: number; saturation: number } {
  const [r, g, b] = hexToRgb(hex).map((channel) => channel / 255) as [number, number, number]
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const lightness = (max + min) / 2
  const delta = max - min
  if (delta === 0) {
    return { hue: 0, saturation: 0 }
  }
  const saturation = delta / (1 - Math.abs(2 * lightness - 1))
  let hue: number
  if (max === r) {
    hue = ((g - b) / delta) % 6
  } else if (max === g) {
    hue = (b - r) / delta + 2
  } else {
    hue = (r - g) / delta + 4
  }
  return { hue: Math.round((hue * 60 + 360) % 360), saturation: Math.round(saturation * 100) }
}

/** Read `--name: r g b;` declarations from a stylesheet as upper-case hex by name. */
export function parseTokenTriplets(css: string): Record<string, string> {
  const tokens: Record<string, string> = {}
  for (const match of css.matchAll(/--([a-z-]+):\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s*;/g)) {
    const [, name, r, g, b] = match
    if (name === undefined || r === undefined || g === undefined || b === undefined) {
      continue
    }
    tokens[name] = `#${[r, g, b].map((channel) => Number(channel).toString(16).padStart(2, '0')).join('').toUpperCase()}`
  }
  return tokens
}

function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((channel) => {
    const value = channel / 255
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  }) as [number, number, number]
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
