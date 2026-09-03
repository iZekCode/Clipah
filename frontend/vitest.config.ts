import react from '@vitejs/plugin-react'
import tsconfigPaths from 'vite-tsconfig-paths'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [tsconfigPaths(), react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    // `@elah/core` publishes ESM with extensionless internal imports, which Node's own
    // resolver refuses. Letting Vite process it is what a bundler does in the browser;
    // the bake-off records the constraint rather than working around it silently.
    server: { deps: { inline: ['@elah/core'] } },
    restoreMocks: true,
  },
})
