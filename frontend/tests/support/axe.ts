import { axe } from 'vitest-axe'
import { expect } from 'vitest'

/**
 * Assert a rendered screen has no axe violations.
 *
 * Colour contrast is off because jsdom computes no styles; `tests/design-tokens.test.ts`
 * proves contrast from the tokens instead.
 */
export async function expectAccessible(container: Element): Promise<void> {
  const results = await axe(container, { rules: { 'color-contrast': { enabled: false } } })
  expect(
    results.violations.map((violation) => `${violation.id}: ${violation.nodes[0]?.target.join(' ')}`),
  ).toEqual([])
}
