import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Brand blue — aligné exactement sur le logo A2 Holding (#263564 = blue-900).
        // Override de la palette `blue` par défaut de Tailwind : toutes les classes
        // blue-50…blue-950 de l'app suivent désormais la teinte marine du logo.
        blue: {
          50:  '#f0f3f9',
          100: '#dee3f2',
          200: '#bdc7e5',
          300: '#92a2d3',
          400: '#677dc1',
          500: '#435dad',
          600: '#354b8d',
          700: '#2e417a',
          800: '#2a3b6f',
          900: '#263564',
          950: '#192343',
        },
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
