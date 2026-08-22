/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: { sans: ['Inter', 'ui-sans-serif', 'system-ui'] },
      colors: { ink: '#18231f', cream: '#f5f4ed', sage: '#4e715f', sun: '#e8ad4a' },
    },
  },
  plugins: [],
};
