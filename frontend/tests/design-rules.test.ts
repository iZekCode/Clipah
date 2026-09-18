import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'

import { describe, expect, test } from 'vitest'

const ROOT = resolve(__dirname, '..')
const SCANNED = ['app', 'components', 'features', 'lib']
const SKIPPED = ['lib/api/generated']

/** Files that still carry a banned pattern and are rebuilt by a named later plan. */
const PENDING_REDESIGN: Record<string, string> = {}

const BANNED: Array<[string, RegExp]> = [
  ['light/dark theme switching', /next-themes|\bdark:[a-z[]/],
  ['HSL token wiring', /hsl\(var\(/],
  ['violet, indigo, purple, or fuchsia colour classes', /\b(?:bg|text|from|via|to|border|ring|fill|stroke|shadow)-(?:violet|indigo|purple|fuchsia)-\d{2,3}\b/],
  ['gradient imagery', /\bbg-gradient-to-/],
]

function sourceFiles(): string[] {
  const files: string[] = []
  const walk = (directory: string) => {
    for (const entry of readdirSync(directory)) {
      const path = join(directory, entry)
      const rel = relative(ROOT, path)
      if (SKIPPED.some((skip) => rel.startsWith(skip))) continue
      if (statSync(path).isDirectory()) walk(path)
      else if (/\.(ts|tsx|css)$/.test(entry)) files.push(rel)
    }
  }
  SCANNED.forEach((directory) => walk(join(ROOT, directory)))
  return files
}

describe('Signal design rules', () => {
  test.each(BANNED)('no source file uses %s', (_name, pattern) => {
    const offenders = sourceFiles().filter(
      (file) => !(file in PENDING_REDESIGN) && pattern.test(readFileSync(join(ROOT, file), 'utf8')),
    )
    expect(offenders).toEqual([])
  })

  test('every screen has been redesigned, so nothing is exempt', () => {
    expect(Object.keys(PENDING_REDESIGN)).toEqual([])
  })

  test('the frontend no longer depends on a theme switcher', () => {
    const manifest = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8')) as {
      dependencies: Record<string, string>
    }
    expect(manifest.dependencies['next-themes']).toBeUndefined()
  })
})
