import localFont from 'next/font/local'

import type { CompositionV1 } from '@/lib/api/generated/model'

export type CaptionFontFamily = CompositionV1['captions']['style']['fontFamily']

// Each face is the file the render image installs, so what the preview draws is what the
// renderer draws. Variable fonts cover their whole weight range; static families list files.
const inter = localFont({
  src: './fonts/Inter-Variable.ttf',
  variable: '--caption-font-inter',
  weight: '100 900',
  display: 'swap',
})
const montserrat = localFont({
  src: './fonts/Montserrat-Variable.ttf',
  variable: '--caption-font-montserrat',
  weight: '100 900',
  display: 'swap',
})
const poppins = localFont({
  src: [
    { path: './fonts/Poppins-Light.ttf', weight: '300' },
    { path: './fonts/Poppins-Regular.ttf', weight: '400' },
    { path: './fonts/Poppins-Medium.ttf', weight: '500' },
    { path: './fonts/Poppins-SemiBold.ttf', weight: '600' },
    { path: './fonts/Poppins-Bold.ttf', weight: '700' },
    { path: './fonts/Poppins-ExtraBold.ttf', weight: '800' },
    { path: './fonts/Poppins-Black.ttf', weight: '900' },
  ],
  variable: '--caption-font-poppins',
  display: 'swap',
})
const roboto = localFont({
  src: './fonts/Roboto-Variable.ttf',
  variable: '--caption-font-roboto',
  weight: '100 900',
  display: 'swap',
})
const openSans = localFont({
  src: './fonts/OpenSans-Variable.ttf',
  variable: '--caption-font-open-sans',
  weight: '300 800',
  display: 'swap',
})
const bebasNeue = localFont({
  src: './fonts/BebasNeue-Regular.ttf',
  variable: '--caption-font-bebas-neue',
  weight: '400',
  display: 'swap',
})
const anton = localFont({
  src: './fonts/Anton-Regular.ttf',
  variable: '--caption-font-anton',
  weight: '400',
  display: 'swap',
})
const nunito = localFont({
  src: './fonts/Nunito-Variable.ttf',
  variable: '--caption-font-nunito',
  weight: '200 1000',
  display: 'swap',
})

const FACES = {
  Inter: inter,
  Montserrat: montserrat,
  Poppins: poppins,
  Roboto: roboto,
  'Open Sans': openSans,
  'Bebas Neue': bebasNeue,
  Anton: anton,
  Nunito: nunito,
} satisfies Record<CaptionFontFamily, { variable: string }>

/** The families a composition may name, in the order the schema lists them. */
export const CAPTION_FONT_FAMILIES = Object.keys(FACES) as CaptionFontFamily[]

/** Every caption face's variable class, for the editor's root element. */
export const CAPTION_FONT_VARIABLES = Object.values(FACES)
  .map((face) => face.variable)
  .join(' ')

/** The CSS font stack that draws one composition family with its vendored face. */
export function captionFontStack(family: CaptionFontFamily): string {
  const slug = family.toLowerCase().replaceAll(' ', '-')
  return `var(--caption-font-${slug}), sans-serif`
}
