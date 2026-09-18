import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import { Providers } from '@/app/providers'
import { fontVariables } from '@/lib/design/fonts'

import './globals.css'

export const metadata: Metadata = {
  title: 'Clipah',
  description: 'Turn one long video into short clips worth posting.',
}

/** The single document shell: Signal's fonts, global styles, and the data providers. */
export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className={fontVariables}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
