/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: { sans: ['Inter', 'ui-sans-serif', 'system-ui'] },
      colors: {
        ink: '#14213d',
        paper: '#f5f7fb',
        cream: '#fffaf0',
        sage: '#2563eb',
        sun: '#d89b24',
        teal: '#168f83',
        coral: '#df6c55',
      },
    },
  },
  plugins: [],
};
