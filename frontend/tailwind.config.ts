import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        a2: {
          50:  '#eaf4fe',
          100: '#c3e0f9',
          200: '#8dc5f2',
          300: '#5da6e8',
          400: '#3a85cb',
          500: '#2567a4',
          600: '#1d4a78',
          700: '#163356',
          800: '#0e2240',
          900: '#081628',
          950: '#040d18',
        },
      },
      animation: {
        'slide-in': 'slideIn 0.25s ease-out',
        'fade-in':  'fadeIn 0.2s ease-out',
      },
      keyframes: {
        slideIn: {
          '0%':   { transform: 'translateX(100%)' },
          '100%': { transform: 'translateX(0)' },
        },
        fadeIn: {
          '0%':   { opacity: '0' },
          '100%': { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}

export default config
