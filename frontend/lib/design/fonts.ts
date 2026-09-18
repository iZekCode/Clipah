import localFont from 'next/font/local'

/**
 * Signal's two faces, served from the repository rather than fetched at build time, so a
 * production build is reproducible offline and every environment draws the same glyphs.
 */
const archivo = localFont({
  src: './fonts/Archivo-Variable.ttf',
  variable: '--font-sans',
  display: 'swap',
  weight: '100 900',
  style: 'normal',
  declarations: [{ prop: 'font-stretch', value: '62% 125%' }],
})

const jetbrainsMono = localFont({
  src: './fonts/JetBrainsMono-Variable.ttf',
  variable: '--font-mono',
  display: 'swap',
  weight: '100 800',
  style: 'normal',
})

/** Both font variables, for the root element. */
export const fontVariables = `${archivo.variable} ${jetbrainsMono.variable}`
