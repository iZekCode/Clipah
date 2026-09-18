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
  {
    files: ['app/**/*.tsx', 'components/**/*.tsx', 'features/**/*.tsx'],
    ignores: ['components/ui/**'],
    rules: {
      // Native controls wear Signal's skin only through components/ui.
      'no-restricted-syntax': [
        'error',
        {
          selector: "JSXOpeningElement[name.name='select']",
          message: 'Use Select from @/components/ui/select.',
        },
        {
          selector:
            "JSXOpeningElement[name.name='input'] > JSXAttribute[name.name='type'][value.value=/^(checkbox|radio|range|color)$/]",
          message: 'Use Checkbox, Radio, or Slider from @/components/ui.',
        },
      ],
    },
  },
]
