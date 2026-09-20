import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    // ⚠️ `lib/` est scanné parce que des BARÈMES DE RENDU y vivent
    // (`lib/faiActions.ts`, `lib/topologyColors.ts`) : ce sont des tables qui
    // associent un état métier à ses classes Tailwind, partagées entre
    // plusieurs pages pour qu'elles ne puissent pas se contredire.
    //
    // Sans cette ligne, Tailwind ne voit jamais ces classes et ne les génère
    // pas — mais l'échec est SOURNOIS : les classes courantes (`text-white`,
    // `bg-red-50`) sont produites grâce à d'autres fichiers, seules les rares
    // manquent. Cas vécu le 2026-09-16 : `text-white` appliqué, `bg-slate-800`
    // absent → badge « Coupé » en texte blanc sur fond blanc, donc une pastille
    // VIDE. Rien n'échoue, rien n'est journalisé, et seul l'œil le voit.
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Couleur de marque — alignée sur le logo A2 ICT (a2ict.mr) :
        // bleu pétrole #295364 (lettres « ICT » = blue-900) et marine #143447
        // (ruban du symbole = blue-950). Override de la palette `blue` par
        // défaut de Tailwind : toutes les classes blue-50…blue-950 de l'app
        // suivent la teinte du logo.
        blue: {
          50:  '#eef6f9',
          100: '#d7eaf2',
          200: '#b3d6e6',
          300: '#86bbd3',
          400: '#4f8dab',
          500: '#3f7f9f',
          600: '#336c87',
          700: '#2f6177',
          800: '#2c5a6d',
          900: '#295364',
          950: '#143447',
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
