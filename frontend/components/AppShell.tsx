'use client'

import { useEffect, useState } from 'react'
import { usePathname } from 'next/navigation'
import Sidebar from '@/components/Sidebar'

/**
 * Decides whether to render the dashboard chrome (Sidebar + main column) or
 * the bare child (used for the /login full-screen form). Doing this in a
 * client wrapper avoids having to move every existing page into a Next.js
 * route group just to swap the layout for one page.
 *
 * Porte aussi le **repli du menu**, qui n'a de sens qu'ici : c'est le seul
 * composant qui possède à la fois la barre et la colonne de contenu.
 */

// Pages qui réclament toute la largeur : le menu s'y replie À L'ARRIVÉE et la
// colonne de contenu perd son `max-w-6xl`. Une seule aujourd'hui — la topologie,
// dont le graphe dépasse largement la largeur d'une colonne centrée.
const FULL_WIDTH_ROUTES = new Set(['/topology'])

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  // Paths that render full-screen, without the dashboard chrome.
  // (/topo-preview = aperçu temporaire sans auth ni sidebar)
  const isChromeless = pathname === '/login' || pathname === '/topo-preview'
  const fullWidth = FULL_WIDTH_ROUTES.has(pathname)

  const [menuHidden, setMenuHidden] = useState(fullWidth)

  // Repli à CHAQUE arrivée sur une page pleine largeur, retour du menu en
  // quittant. Clé volontairement `pathname` seul : tant qu'on reste sur la page,
  // un clic manuel de l'utilisateur n'est jamais écrasé par cet effet.
  useEffect(() => {
    setMenuHidden(FULL_WIDTH_ROUTES.has(pathname))
  }, [pathname])

  if (isChromeless) {
    return <>{children}</>
  }

  const show = () => setMenuHidden(false)
  const hide = () => setMenuHidden(true)

  return (
    <div className="bg-white min-h-screen flex">
      {!menuHidden && <Sidebar onCollapse={hide} />}
      <main className="flex-1 overflow-auto min-h-screen bg-slate-50 relative">
        {/* Le bouton de retour ne flotte QUE menu replié. Menu visible, le
            contrôle vit dans l'en-tête de la barre : un bouton flottant en haut
            à gauche du contenu recouvrirait le titre de chacune des pages. */}
        {menuHidden && (
          <button
            onClick={show}
            title="Afficher le menu"
            aria-label="Afficher le menu"
            className="absolute top-4 left-4 z-20 flex items-center justify-center w-9 h-9
                       rounded-lg border border-slate-200 bg-white/90 backdrop-blur
                       text-slate-600 shadow-sm hover:bg-white hover:text-blue-700
                       hover:border-blue-200 transition-colors"
          >
            <ExpandIcon />
          </button>
        )}
        {/* `pl-16` dégage la place du bouton flottant — uniquement quand il est
            là, pour ne décaler aucune page dans le cas courant. */}
        <div
          className={`px-6 py-6 ${fullWidth ? '' : 'max-w-6xl mx-auto'} ${
            menuHidden ? 'pl-16' : ''
          }`}
        >
          {children}
        </div>
      </main>
    </div>
  )
}

function ExpandIcon() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24"
         stroke="currentColor" strokeWidth={1.9}>
      <path strokeLinecap="round" strokeLinejoin="round"
            d="M4 6h16M10 12h10M4 18h16M5 9l3 3-3 3" />
    </svg>
  )
}
