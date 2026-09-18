import type { Config } from 'tailwindcss'

/** One Signal colour, read from its RGB triplet so opacity modifiers keep working. */
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './features/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        background: token('background'),
        foreground: token('foreground'),
        card: { DEFAULT: token('card'), foreground: token('card-foreground') },
        popover: { DEFAULT: token('popover'), foreground: token('popover-foreground') },
        primary: {
          DEFAULT: token('primary'),
          hover: token('primary-hover'),
          foreground: token('primary-foreground'),
          soft: token('primary-soft'),
        },
        secondary: { DEFAULT: token('secondary'), foreground: token('secondary-foreground') },
        muted: { DEFAULT: token('muted'), foreground: token('muted-foreground') },
        'subtle-foreground': token('subtle-foreground'),
        accent: { DEFAULT: token('accent'), foreground: token('accent-foreground') },
        destructive: {
          DEFAULT: token('destructive'),
          foreground: token('destructive-foreground'),
          soft: token('destructive-soft'),
        },
        warning: { DEFAULT: token('warning'), soft: token('warning-soft') },
        success: { DEFAULT: token('success'), soft: token('success-soft') },
        info: { DEFAULT: token('info'), soft: token('info-soft') },
        border: token('border'),
        'line-strong': token('line-strong'),
        input: token('input'),
        ring: token('ring'),
        stage: token('stage'),
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: '4px',
        sm: '2px',
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        caption: ['12px', { lineHeight: '16px' }],
        small: ['13px', { lineHeight: '20px' }],
        body: ['15px', { lineHeight: '22px' }],
        title: ['18px', { lineHeight: '24px', fontWeight: '650' }],
        h2: ['24px', { lineHeight: '28px' }],
        h1: ['36px', { lineHeight: '36px' }],
        display: ['56px', { lineHeight: '52px' }],
        hero: ['88px', { lineHeight: '80px' }],
      },
      maxWidth: {
        studio: '1600px',
      },
      transitionDuration: {
        fast: '120ms',
        panel: '180ms',
        dialog: '240ms',
      },
      transitionTimingFunction: {
        signal: 'cubic-bezier(0.2, 0, 0, 1)',
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.18s cubic-bezier(0.2, 0, 0, 1)',
        'accordion-up': 'accordion-up 0.18s cubic-bezier(0.2, 0, 0, 1)',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
}

export default config
