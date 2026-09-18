import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'

import { describe, expect, test } from 'vitest'

const ROOT = resolve(__dirname, '..')

/** Phrases that describe the system to members, or apologise, instead of saying what to do. */
const BANNED: Array<[string, RegExp]> = [
  [
    'internal architecture',
    /a batch never hides a failure|so it opens only for a signed-in member|writes its type into a clip and records the version/i,
  ],
  // A label such as "Start (ms)", not a call like `round(ms)` or an `(ms) =>` parameter.
  ['millisecond labels', /\s\(ms\)(?!\s*=>)/],
  ['"successfully"', /\bsuccessfully\b/i],
  ['"Please"', /['">]\s*Please\b/],
  ['"Something went wrong. Please try again."', /Something went wrong\. Please try again\./],
  ['"No preview yet"', /No preview yet/],
]

function uiFiles(): string[] {
  const files: string[] = []
  const walk = (directory: string) => {
    for (const entry of readdirSync(directory)) {
      const path = join(directory, entry)
      const rel = relative(ROOT, path)
      if (rel.startsWith('lib/api/generated')) continue
      if (statSync(path).isDirectory()) walk(path)
      else if (entry.endsWith('.tsx')) files.push(rel)
    }
  }
  for (const directory of ['app', 'components', 'features']) walk(join(ROOT, directory))
  return files
}

describe('copy rules', () => {
  test.each(BANNED)('no screen uses %s', (_name, pattern) => {
    expect(uiFiles().filter((file) => pattern.test(readFileSync(join(ROOT, file), 'utf8')))).toEqual([])
  })
})
