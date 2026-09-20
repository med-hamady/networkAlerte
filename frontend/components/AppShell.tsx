'use client'

import { usePathname, useRouter } from 'next/navigation'
import Sidebar from '@/components/Sidebar'
import DeviceSearchBar from '@/components/DeviceSearchBar'
import AlertBanner from '@/components/AlertBanner'
import { PERM, firstAllowedRoute, usePermissions } from '@/lib/permissions'

/**
 * Decides whether to render the dashboard chrome (Sidebar + main column) or
 * the bare child (used for the /login full-screen form). Doing this in a
 * client wrapper avoids having to move every existing page into a Next.js
 * route group just to swap the layout for one page.
 *
 * Porte aussi la **recherche globale**, qui n'a de sens qu'ici : c'est le seul
 * composant qui survive aux changements de page (il est monté par le layout
 * racine — le champ garde donc son focus et son contenu d'une page à l'autre).
 */

// Pages qui réclament toute la largeur : la colonne de contenu y perd son
// `max-w-6xl`. Une seule aujourd'hui — la topologie, dont le graphe dépasse
// largement la largeur d'une colonne centrée. (Le menu n'a plus à s'y replier :
// c'est une barre d'icônes de 76 px, qui ne prend presque rien au graphe.)
const FULL_WIDTH_ROUTES = new Set(['/topology'])

/**
 * Le droit qui ouvre chaque page du dashboard.
 *
 * ⚠️ **Ce contrôle est un CONFORT, pas une protection.** Il évite qu'un compte
 * arrivant par un signet sur une page qui ne lui est pas ouverte n'y trouve un
 * écran cassé, dont chaque requête répond 403. La protection réelle est sur les
 * routes du backend — le proxy relaie le cookie de session, donc tout ce qui
 * n'est gardé qu'ici reste appelable à la main.
 *
 * ⚠️ Le contrôle NE PEUT PAS vivre dans `middleware.ts` : celui-ci tourne à
 * l'edge, ne parle pas à la base, et ne connaît donc que la PRÉSENCE du cookie
 * — jamais les droits qu'il porte. C'est déjà la raison pour laquelle il ne
 * vérifie pas la validité de la session.
 *
 * ⚠️ Une page absente de cette table est ouverte à tout compte connecté. C'est
 * volontaire pour `/topo-preview` et `/login` (traités en amont), et ce doit
 * être un choix conscient pour toute page ajoutée plus tard.
 */
const PAGE_PERMISSIONS: Record<string, string> = {
  '/': PERM.dashboard,
  '/sites': PERM.sites,
  '/lr-health': PERM.lrHealth,
  '/site-links': PERM.lrHealth,
  '/clients': PERM.clients,
  '/capacity': PERM.capacity,
  '/topology': PERM.topology,
  '/map': PERM.map,
  '/traffic': PERM.traffic,
  '/access': PERM.fai,
  '/fai-requests': PERM.faiRequests,
  '/fai-journal': PERM.faiJournal,
  '/router-rules': PERM.routerRules,
  '/content-block': PERM.contentFilter,
  '/incidents': PERM.incidents,
  '/access-diagnostics': PERM.accessDiagnostics,
  '/reports': PERM.reports,
  '/settings': PERM.thresholds,
  '/admin': PERM.adminAccess,
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const { can, loading: permsLoading } = usePermissions()
  // Paths that render full-screen, without the dashboard chrome.
  // (/topo-preview = aperçu temporaire sans auth ni sidebar)
  const isChromeless = pathname === '/login' || pathname === '/topo-preview'
  const fullWidth = FULL_WIDTH_ROUTES.has(pathname)

  if (isChromeless) {
    return <>{children}</>
  }

  // ⚠️ Rien ne se décide avant que /auth/me ait répondu : au premier rendu
  // aucun droit n'est connu, et trancher là afficherait « accès refusé » à un
  // administrateur pendant une fraction de seconde à chaque navigation.
  const requiredPermission = PAGE_PERMISSIONS[pathname]
  const pageRefused = !permsLoading && requiredPermission !== undefined
    && !can(requiredPermission)

  // Destination d'un résultat de recherche : la fiche de l'équipement, ouverte
  // dans le contexte de son site. `/sites?device=` est le deep-link qui existe
  // déjà (utilisé par « Voir l'équipement → » de /lr-health) — la barre globale
  // n'invente donc aucun chemin d'ouverture qui lui soit propre.
  const openDevice = (deviceId: number) => {
    router.push(`/sites?device=${deviceId}`)
  }

  return (
    <div className="bg-white min-h-screen flex">
      <Sidebar />
      <main className="flex-1 overflow-auto min-h-screen bg-slate-50">
        {/* Bandeau collant : présent sur TOUTES les pages du dashboard, il reste
            visible au défilement (`sticky` sur le conteneur qui défile, c.-à-d.
            ce <main>). */}
        <div className="sticky top-0 z-30 bg-white/95 backdrop-blur border-b border-slate-200">
          {/* Recherche CENTRÉE dans le bandeau, comme celle du contrôleur UISP,
              et la CLOCHE des anomalies à acquitter à son extrémité droite.
              ⚠️ La recherche reste centrée sur la PAGE : la cloche est posée en
              absolu, sinon elle décalerait le champ vers la gauche. */}
          <div className="relative flex items-center justify-center px-6 h-16">
            <DeviceSearchBar
              onSelect={openDevice}
              shortcut
              className="w-full max-w-lg"
            />
            {/* Anomalies à acquitter à la main. DANS l'en-tête collant : elles
                n'ont d'intérêt que si l'opérateur les voit quelle que soit la
                page où il travaille et où il en est du défilement. */}
            <div className="absolute right-6 top-1/2 -translate-y-1/2">
              <AlertBanner />
            </div>
          </div>
        </div>

        <div className={`px-6 py-6 ${fullWidth ? '' : 'max-w-6xl mx-auto'}`}>
          {pageRefused ? <PageRefused router={router} can={can} /> : children}
        </div>
      </main>
    </div>
  )
}

/**
 * Écran servi à la place d'une page que le profil n'ouvre pas.
 *
 * Il propose une PORTE DE SORTIE plutôt qu'un cul-de-sac : un compte arrivé là
 * par un signet doit pouvoir rejoindre son travail sans deviner quelle page lui
 * est ouverte. La destination est la première de l'ordre du menu qu'il a le
 * droit de voir — jamais `/` en dur, qui est lui-même une permission et
 * renverrait un agent sur un second refus.
 */
function PageRefused({
  router, can,
}: {
  router: ReturnType<typeof useRouter>
  can: (...keys: string[]) => boolean
}) {
  const fallback = firstAllowedRoute(can)
  return (
    <div className="max-w-xl mx-auto mt-16 rounded-xl border border-slate-200
                    bg-white p-8 text-center">
      <h1 className="text-lg font-bold text-slate-900">Accès refusé</h1>
      <p className="mt-2 text-sm text-slate-600">
        Ton profil ne donne pas accès à cette page. Si tu penses en avoir besoin,
        demande-le à un administrateur.
      </p>
      {fallback && (
        <button
          onClick={() => router.push(fallback)}
          className="mt-5 px-4 py-2 rounded-lg text-sm font-semibold text-white
                     bg-blue-700 hover:bg-blue-800 transition-colors"
        >
          Retour à une page autorisée
        </button>
      )}
    </div>
  )
}
