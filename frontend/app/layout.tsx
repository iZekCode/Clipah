import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import { Providers } from '@/app/providers'

import './globals.css'

export const metadata: Metadata = {
  title: 'Clipah',
  description: 'Turn long-form video into ranked, review-ready short clips.',
}

/** The single document shell: fonts, global styles, and the data-fetching provider. */
export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
