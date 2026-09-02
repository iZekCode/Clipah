import { defineConfig } from 'orval'

/**
 * The typed client is generated from the backend contract, never hand-written.
 * Regenerate with `scripts/export-openapi.sh` followed by `pnpm generate:api`.
 */
export default defineConfig({
  clipah: {
    input: {
      target: '../contracts/openapi.json',
    },
    output: {
      mode: 'tags-split',
      target: './lib/api/generated/clipah.ts',
      schemas: './lib/api/generated/model',
      client: 'react-query',
      httpClient: 'fetch',
      clean: true,
      prettier: false,
      override: {
        // The paths in the contract are already absolute and same-origin, and the
        // mutator resolves a call to its body or throws, so hooks return the body.
        fetch: {
          includeHttpResponseReturnType: false,
        },
        mutator: {
          path: './lib/api/client.ts',
          name: 'apiFetch',
        },
      },
    },
  },
})
