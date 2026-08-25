/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      colors: {
        // Theme-aware tokens: light/dark values swap via CSS vars in index.css. The
        // <alpha-value> placeholder is Tailwind's mechanism for keeping /NN opacity
        // modifiers (e.g. text-fg/55) working on top of a CSS-variable color.
        paper: 'rgb(var(--paper) / <alpha-value>)',
        surface: 'rgb(var(--surface) / <alpha-value>)',
        surface2: 'rgb(var(--surface-2) / <alpha-value>)',
        fg: 'rgb(var(--fg) / <alpha-value>)',
        // Fixed-dark app sidebar band — a different shade per theme (see index.css) so
        // it stays a visually distinct panel without ever going pure black.
        sidebar: 'rgb(var(--sidebar) / <alpha-value>)',
        // Fixed regardless of theme: intentionally-always-dark bands (footer) and the
        // handful of semantic/brand colors.
        ink: '#0b0d12',
        accent: '#2563eb',
        success: '#16a34a',
        warning: '#d89b24',
        danger: '#dc2626',
        // Safety aliases for the old palette names during the redesign sweep, so a
        // missed spot still renders instead of silently dropping its color.
        sage: '#2563eb',
        sun: '#d89b24',
        teal: '#16a34a',
        coral: '#dc2626',
      },
      borderRadius: {
        // Sharper across the board — the previous 2xl/xl defaults are the single most
        // common "generated dashboard" tell (round-cornered card wall). Pills/avatars
        // still use `rounded-full`, unaffected by this.
        sm: '3px',
        DEFAULT: '5px',
        md: '6px',
        lg: '7px',
        xl: '8px',
        '2xl': '10px',
        '3xl': '14px',
      },
    },
  },
  plugins: [],
};
