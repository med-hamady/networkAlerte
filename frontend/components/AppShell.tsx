'use client'

import { useEffect, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import Sidebar from '@/components/Sidebar'
import DeviceSearchBar from '@/components/DeviceSearchBar'
import AlertBanner from '@/components/AlertBanner'

/**
 * Decides whether to render the dashboard chrome (Sidebar + main column) or
 * the bare child (used for the /login full-screen form). Doing this in a
 * client wrapper avoids having to move every existing page into a Next.js
 * route group just to swap the layout for one page.
 *
 * Porte aussi le **repli du menu** et la **recherche globale**, qui n'ont de
 * sens qu'ici : c'est le seul composant qui possède à la fois la barre et la
 * colonne de contenu, et le seul qui survive aux changements de page (il est
 * monté par le layout racine — le champ garde donc son focus et son contenu
 * d'une page à l'autre).
 */

// Pages qui réclament toute la largeur : le menu s'y replie À L'ARRIVÉE et la
// colonne de contenu perd son `max-w-6xl`. Une seule aujourd'hui — la topologie,
// dont le graphe dépasse largement la largeur d'une colonne centrée.
const FULL_WIDTH_ROUTES = new Set(['/topology'])

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
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

  // Destination d'un résultat de recherche : la fiche de l'équipement, ouverte
  // dans le contexte de son site. `/sites?device=` est le deep-link qui existe
  // déjà (utilisé par « Voir l'équipement → » de /lr-health) — la barre globale
  // n'invente donc aucun chemin d'ouverture qui lui soit propre.
  const openDevice = (deviceId: number) => {
    router.push(`/sites?device=${deviceId}`)
  }

  return (
    <div className="bg-white min-h-screen flex">
      {!menuHidden && <Sidebar onCollapse={hide} />}
      <main className="flex-1 overflow-auto min-h-screen bg-slate-50">
        {/* Bandeau collant : présent sur TOUTES les pages du dashboard, il reste
            visible au défilement (`sticky` sur le conteneur qui défile, c.-à-d.
            ce <main>). Il porte aussi le bouton de retour du menu quand celui-ci
            est replié — dans la barre plutôt qu'en flottant sur le contenu, où
            il recouvrait le titre de la page. */}
        <div className="sticky top-0 z-30 bg-white/95 backdrop-blur border-b border-blue-100">
          <div className="flex items-center gap-3 px-6 py-3">
            {menuHidden && (
              <button
                onClick={show}
                title="Afficher le menu"
                aria-label="Afficher le menu"
                className="shrink-0 flex items-center justify-center w-9 h-9
                           rounded-lg border border-slate-200 bg-white
                           text-slate-600 shadow-sm hover:bg-blue-50 hover:text-blue-700
                           hover:border-blue-200 transition-colors"
              >
                <ExpandIcon />
              </button>
            )}
            <DeviceSearchBar
              onSelect={openDevice}
              shortcut
              className="w-full max-w-xl"
            />
          </div>
          {/* Anomalies à acquitter à la main. DANS l'en-tête collant : elles
              n'ont d'intérêt que si l'opérateur les voit quelle que soit la
              page où il travaille et où il en est du défilement. Le composant
              ne rend RIEN quand il n'y a rien à acquitter — pas de bandeau
              vide, pas de place réservée. */}
          <AlertBanner />
        </div>

        <div className={`px-6 py-6 ${fullWidth ? '' : 'max-w-6xl mx-auto'}`}>
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
