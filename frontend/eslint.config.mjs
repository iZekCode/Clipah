import { FlatCompat } from '@eslint/eslintrc'

const compat = new FlatCompat({ baseDirectory: import.meta.dirname })

export default [
  {
    ignores: ['.next/**', 'node_modules/**', 'lib/api/generated/**'],
  },
  ...compat.extends('next/core-web-vitals', 'next/typescript'),
  {
    rules: {
      // Provider and user text is rendered as React children, never as markup.
      'react/no-danger': 'error',
    },
  },
]
