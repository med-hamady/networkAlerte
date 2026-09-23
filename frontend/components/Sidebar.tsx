'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import useSWR from 'swr'
import { ApiError, endpoints, fetcher, logout, type CurrentUser } from '@/lib/api'
import { PERM, usePermissions } from '@/lib/permissions'
import type { HealthResponse } from '@/lib/types'

type NavLink = {
  href: string
  label: string
  icon: (props: { className?: string }) => JSX.Element
  exact?: boolean
  /**
   * Droit qui ouvre cette entrée. ⚠️ OBLIGATOIRE sur toute nouvelle entrée :
   * une entrée sans `permission` s'afficherait pour TOUT LE MONDE, y compris
   * un compte à qui la page répond 403 — il cliquerait sur un écran d'erreur
   * sans comprendre. Le type l'impose, il n'est pas optionnel.
   */
  permission: string
  /**
   * Sous-pages : rendues dans un MENU VOLANT au survol de l'icône, jamais
   * comme icônes de la barre (qui passerait de 15 à 19 icônes pour une seule
   * famille de pages). L'icône du groupe est active sur chacune d'elles.
   */
  children?: NavLink[]
  /** Nom du GROUPE (bulle + titre du menu volant) quand il diffère du nom de
   *  sa première page — « Liaisons » regroupe « Liaisons clients » et
   *  « Point-à-Point ». */
  groupLabel?: string
}

type NavSection = {
  title: string
  links: NavLink[]
}

/**
 * Icône dessinée par un PNG de `public/brand/icons/`, utilisée comme MASQUE
 * CSS : la forme vient de l'image, la couleur de `currentColor`. L'icône suit
 * donc les états du lien (gris au repos, pétrole quand la page est active)
 * exactement comme les icônes SVG voisines — une `<img>` resterait noire.
 */
function maskIcon(file: string) {
  const src = `url(/brand/icons/${file})`
  function MaskIcon({ className }: { className?: string }) {
    return (
      <span
        aria-hidden
        className={`inline-block bg-current ${className ?? ''}`}
        style={{
          maskImage: src, WebkitMaskImage: src,
          maskSize: 'contain', WebkitMaskSize: 'contain',
          maskRepeat: 'no-repeat', WebkitMaskRepeat: 'no-repeat',
          maskPosition: 'center', WebkitMaskPosition: 'center',
        }}
      />
    )
  }
  return MaskIcon
}

const SiteIcon = maskIcon('site.png')
const ConsumptionIcon = maskIcon('consommation_client.png')
const CapacityIcon = maskIcon('capacite.png')
const TopologyIcon = maskIcon('topologie.png')

const sections: NavSection[] = [
  {
    title: 'Supervision',
    links: [
      { href: '/',           label: 'Dashboard',           icon: DashboardIcon, exact: true, permission: PERM.dashboard },
      { href: '/downtime-log', label: 'Journal des coupures', icon: OutageIcon,  permission: PERM.uptime },
      { href: '/sites',      label: 'Sites',               icon: SiteIcon,      permission: PERM.sites },
      {
        href: '/lr-health', label: 'Liaisons clients', groupLabel: 'Liaisons', icon: LinkIcon, permission: PERM.lrHealth,
        children: [
          { href: '/site-links', label: 'Point-à-Point', icon: LinkIcon, permission: PERM.lrHealth },
        ],
      },
      { href: '/clients',    label: 'Consommation clients', icon: ConsumptionIcon,  permission: PERM.clients },
      { href: '/capacity',   label: 'Capacité du réseau',  icon: CapacityIcon,  permission: PERM.capacity },
      { href: '/topology',   label: 'Topologie du réseau', icon: TopologyIcon,  permission: PERM.topology },
      { href: '/map',        label: 'Carte des clients',   icon: MapIcon,       permission: PERM.map },
      { href: '/traffic',    label: 'Destinations Internet', icon: GlobeIcon,   permission: PERM.traffic },
      {
        href: '/access', label: 'FAI', icon: ShieldIcon, permission: PERM.fai,
        children: [
          { href: '/fai-requests',  label: 'Demandes de coupure', icon: RequestIcon, permission: PERM.faiRequests },
          { href: '/fai-journal',   label: 'Activités du système', icon: JournalIcon, permission: PERM.faiJournal },
          { href: '/router-rules',  label: 'Règles du routeur',   icon: RouterIcon,  permission: PERM.routerRules },
          { href: '/content-block', label: 'Filtre de contenu',   icon: FilterIcon,  permission: PERM.contentFilter },
        ],
      },
    ],
  },
  {
    title: 'Anomalies',
    links: [
      { href: '/incidents',         label: 'Incidents',           icon: WarningIcon, exact: true, permission: PERM.incidents },
      { href: '/access-diagnostics', label: "Diagnostics d'accès", icon: PlugIcon, permission: PERM.accessDiagnostics },
    ],
  },
  {
    title: 'Configuration',
    links: [
      { href: '/reports',  label: 'Rapports', icon: ReportIcon,   permission: PERM.reports },
      { href: '/settings', label: 'Seuils',   icon: SettingsIcon, permission: PERM.thresholds },
    ],
  },
  {
    title: 'Administration',
    links: [
      { href: '/admin', label: 'Profils et accès', icon: KeyIcon, permission: PERM.adminAccess },
    ],
  },
]

/** Largeur de la barre, en px — les bulles et le menu volant s'y accrochent. */
const RAIL_WIDTH = 76

function isActiveLink(pathname: string, { href, exact }: NavLink) {
  return exact
    ? pathname === href
    : pathname === href || pathname.startsWith(href + '/')
}

/**
 * Une entrée telle que l'affiche la barre, droits appliqués : `href` est la
 * PREMIÈRE page du groupe que le compte peut ouvrir (un agent qui voit les
 * demandes de coupure sans voir la page FAI doit quand même avoir l'icône), et
 * `items` la liste du menu volant.
 */
type RailEntry = {
  key: string
  label: string
  href: string
  icon: NavLink['icon']
  items: NavLink[]
  active: boolean
}

/**
 * Barre de navigation en icônes, dans l'esprit du contrôleur UISP : 76 px,
 * fond blanc, le nom de chaque page dans une BULLE au survol, et les
 * sous-pages FAI dans un MENU VOLANT.
 *
 * ⚠️ Bulles et menu volant sont en `position: fixed`, calés sur la position
 * de l'icône à l'écran — pas en `absolute` dans la barre : la liste d'icônes
 * défile (`overflow-y-auto`) sur un petit écran, et un élément absolu y serait
 * rogné au bord de la barre, donc invisible.
 */
export default function Sidebar() {
  const pathname = usePathname()
  const router = useRouter()
  const { can, loading: permsLoading } = usePermissions()
  const { data: health } = useSWR<HealthResponse>(
    endpoints.health,
    fetcher,
    { refreshInterval: 30_000 },
  )
  // Identity of the logged-in operator. Used to (a) display who is logged in
  // in the user menu, (b) trigger a redirect to /login if the session is gone
  // (an expired cookie returns 401 → fetcher throws → SWR returns no data;
  // we treat that as "logged out" and bounce to the login page).
  const { data: currentUser, error: userError, isValidating: userValidating } =
    useSWR<CurrentUser>(
      endpoints.authMe,
      fetcher,
      { refreshInterval: 60_000, shouldRetryOnError: false },
    )
  // L'erreur que SWR avait DÉJÀ en cache au montage de ce composant. Elle ne
  // prouve rien sur la session courante : elle a pu être produite par la
  // session précédente, avant une reconnexion.
  //
  // ⚠️ C'est la condition qui corrige le défaut du 2026-09-21. SWR garde
  // l'erreur d'une clé dans son cache GLOBAL, et `router.replace` est une
  // navigation douce qui ne vide rien : après connexion, ce composant se
  // remontait et recevait le 401 de la session MORTE — instantanément, avant
  // d'avoir redemandé quoi que ce soit — donc repartait vers /login alors que
  // le cookie tout neuf était valide. L'opérateur devait s'y reprendre à
  // plusieurs fois, sans jamais voir d'erreur, ses identifiants étant corrects
  // depuis le début. Les logs nginx le montraient en creux : POST
  // /auth/login 200, puis AUCUN appel à /auth/me, puis un second POST — la
  // décision de le renvoyer au login était prise sans consulter le serveur.
  //
  // ⚠️ Comparer les IDENTITÉS d'objet, et pas `isValidating` seul : ce drapeau
  // peut encore valoir `false` au tout premier rendu, avant que SWR n'ait
  // lancé sa revalidation, et l'effet repartirait sur l'erreur du cache. Le
  // `fetcher` lève une ApiError NEUVE à chaque échec, donc un 401 réellement
  // reçu depuis le montage ne peut pas être celui-ci. Une session vraiment
  // expirée reste détectée : la revalidation du montage relève son 401, sous
  // une autre instance, et la redirection part.
  const cachedErrorAtMount = useRef(userError)
  // ⚠️ Le statut doit être 401. Avant, TOUTE erreur déconnectait — un 502 ou
  // un 504 passager sur ce seul appel suffisait à renvoyer un opérateur
  // authentifié sur l'écran de login, sans le moindre message.
  const sessionExpired =
    !userValidating
    && userError instanceof ApiError
    && userError.status === 401
    && userError !== cachedErrorAtMount.current
  const dbOk = health?.database === 'connected'

  // Survol : une bulle (entrée simple) ou un menu volant (entrée à sous-pages).
  const [hover, setHover] = useState<{ key: string; top: number; center: number } | null>(null)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const userMenuRef = useRef<HTMLDivElement>(null)

  const cancelClose = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current)
    closeTimer.current = null
  }
  // Fermeture différée : le temps de glisser de l'icône au menu volant sans
  // qu'il se referme sous le curseur.
  const scheduleClose = () => {
    cancelClose()
    closeTimer.current = setTimeout(() => setHover(null), 150)
  }
  const openHover = (key: string, el: HTMLElement) => {
    cancelClose()
    const r = el.getBoundingClientRect()
    setHover({ key, top: r.top, center: r.top + r.height / 2 })
  }

  useEffect(() => {
    setHover(null)
    setUserMenuOpen(false)
  }, [pathname])

  useEffect(() => () => cancelClose(), [])

  // Menu utilisateur : fermé par un clic ailleurs ou par Échap.
  useEffect(() => {
    if (!userMenuOpen) return
    const onDown = (e: MouseEvent) => {
      if (!userMenuRef.current?.contains(e.target as Node)) setUserMenuOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setUserMenuOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [userMenuOpen])

  const handleLogout = async () => {
    try {
      await logout()
    } finally {
      router.replace('/login')
    }
  }

  // Auto-redirect to /login if the session expired server-side (the cookie
  // exists so the middleware lets the page render, but /auth/me returns 401
  // and the fetcher throws). Cf. `sessionExpired` ci-dessus pour les deux
  // conditions — une erreur quelconque, ou une erreur encore en cache, ne
  // déconnecte plus personne.
  useEffect(() => {
    if (sessionExpired) {
      router.replace('/login')
    }
  }, [sessionExpired, router])

  // ⚠️ Pendant le chargement de /auth/me on n'affiche RIEN plutôt qu'un menu
  // complet : montrer puis retirer des entrées ferait clignoter la moitié du
  // menu à chaque navigation, et un agent y verrait passer des pages qui ne
  // lui sont pas ouvertes. Un groupe dont toutes les entrées sont masquées
  // disparaît avec son séparateur.
  const visibleGroups: RailEntry[][] = permsLoading
    ? []
    : sections
        .map(({ links }) =>
          links.flatMap((link): RailEntry[] => {
            const items = [link, ...(link.children ?? [])].filter((l) => can(l.permission))
            if (items.length === 0) return []
            return [{
              key: link.href,
              label: link.groupLabel ?? link.label,
              href: items[0].href,
              icon: link.icon,
              items,
              active: items.some((l) => isActiveLink(pathname, l)),
            }]
          }),
        )
        .filter((group) => group.length > 0)

  const hovered = visibleGroups.flat().find((e) => e.key === hover?.key)

  const statusLabel = health === undefined
    ? 'Connexion…'
    : dbOk ? 'Système opérationnel' : 'Erreur base de données'
  const statusDot = health === undefined
    ? 'bg-slate-300 animate-pulse'
    : dbOk ? 'bg-emerald-500' : 'bg-red-500'
  const initials = (currentUser?.username ?? '?').slice(0, 2).toUpperCase()

  return (
    <aside
      className="sticky top-0 h-screen bg-white border-r border-slate-200 flex flex-col items-center shrink-0 z-40"
      style={{ width: RAIL_WIDTH }}
    >
      {/* Marque */}
      <Link
        href={visibleGroups[0]?.[0]?.href ?? '/'}
        className="h-16 w-full flex items-center justify-center border-b border-slate-100 shrink-0"
        aria-label="A2 ICT — Network Management"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/brand/a2ict-mark.png" alt="A2 ICT" className="w-9 h-auto" />
      </Link>

      {/* Navigation */}
      <nav className="flex-1 w-full overflow-y-auto overflow-x-hidden py-3 flex flex-col items-center">
        {visibleGroups.map((group, i) => (
          <div key={group[0].key} className="w-full flex flex-col items-center gap-1">
            {i > 0 && <div className="w-10 border-t border-slate-200 my-2" />}
            {group.map((entry) => {
              const Icon = entry.icon
              return (
                <Link
                  key={entry.key}
                  href={entry.href}
                  aria-label={entry.label}
                  onMouseEnter={(e) => openHover(entry.key, e.currentTarget)}
                  onMouseLeave={scheduleClose}
                  onFocus={(e) => openHover(entry.key, e.currentTarget)}
                  onBlur={scheduleClose}
                  className={`relative w-12 h-12 shrink-0 flex items-center justify-center rounded-xl transition-colors ${
                    entry.active
                      ? 'bg-blue-50 text-blue-700'
                      : 'text-slate-500 hover:bg-slate-100 hover:text-blue-900'
                  }`}
                >
                  {entry.active && (
                    <span className="absolute -left-[14px] top-2 bottom-2 w-[3px] rounded-r bg-blue-700" />
                  )}
                  <Icon className="w-6 h-6" />
                  {entry.items.length > 1 && (
                    <span className="absolute bottom-1.5 right-1.5 w-1 h-1 rounded-full bg-current opacity-60" />
                  )}
                </Link>
              )
            })}
          </div>
        ))}
      </nav>

      {/* Utilisateur + état du système */}
      <div ref={userMenuRef} className="w-full py-3 border-t border-slate-100 flex flex-col items-center shrink-0">
        <button
          onClick={() => setUserMenuOpen((v) => !v)}
          aria-label="Compte et déconnexion"
          aria-expanded={userMenuOpen}
          className="relative w-10 h-10 rounded-full bg-blue-900 text-white text-sm font-bold
                     flex items-center justify-center hover:ring-4 hover:ring-blue-100 transition-shadow"
        >
          {initials}
          <span
            title={statusLabel}
            className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-white ${statusDot}`}
          />
        </button>

        {userMenuOpen && (
          <div
            className="fixed bottom-3 z-50 w-64 rounded-xl border border-slate-200 bg-white shadow-xl animate-fade-in"
            style={{ left: RAIL_WIDTH + 8 }}
          >
            <div className="px-4 py-3 border-b border-slate-100">
              <p className="text-sm font-semibold text-slate-800 truncate">
                {currentUser?.full_name || currentUser?.username || '—'}
              </p>
              <p className="text-xs text-slate-500 truncate">
                {currentUser?.profile_name
                  ? currentUser.profile_name
                  : currentUser?.full_name
                  ? currentUser.username
                  : ''}
              </p>
            </div>
            <div className="px-4 py-2.5 flex items-center gap-2 border-b border-slate-100">
              <span className={`w-2 h-2 rounded-full shrink-0 ${statusDot}`} />
              <span className="text-xs text-slate-600">{statusLabel}</span>
            </div>
            {sessionExpired && (
              <p className="px-4 pt-2 text-[11px] text-red-600">Session expirée — reconnecte-toi.</p>
            )}
            <button
              onClick={handleLogout}
              className="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-slate-700
                         hover:bg-slate-50 rounded-b-xl transition-colors"
            >
              <LogoutIcon className="w-4 h-4 text-slate-500" />
              Se déconnecter
            </button>
          </div>
        )}
      </div>

      {/* Bulle (entrée simple) */}
      {hover && hovered && hovered.items.length === 1 && (
        <div
          className="fixed z-50 -translate-y-1/2 pointer-events-none whitespace-nowrap
                     rounded-lg bg-slate-800 px-2.5 py-1.5 text-xs font-medium text-white shadow-lg"
          style={{ left: RAIL_WIDTH + 6, top: hover.center }}
        >
          {hovered.label}
        </div>
      )}

      {/* Menu volant (entrée à sous-pages) */}
      {hover && hovered && hovered.items.length > 1 && (
        <div
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
          className="fixed z-50 w-60 rounded-xl border border-slate-200 bg-white py-1.5 shadow-xl"
          style={{ left: RAIL_WIDTH + 4, top: Math.max(8, hover.top - 6) }}
        >
          <p className="px-3.5 pt-1 pb-1.5 text-[10px] font-bold uppercase tracking-widest text-slate-400">
            {hovered.label}
          </p>
          {hovered.items.map((item) => {
            const Icon = item.icon
            const active = isActiveLink(pathname, item)
            return (
              <Link
                key={item.href}
                href={item.href}
                onFocus={cancelClose}
                onBlur={scheduleClose}
                className={`mx-1.5 flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors ${
                  active
                    ? 'bg-blue-50 text-blue-800 font-semibold'
                    : 'text-slate-700 hover:bg-slate-50 hover:text-blue-900'
                }`}
              >
                <Icon className="w-4 h-4 shrink-0" />
                {item.label}
              </Link>
            )
          })}
        </div>
      )}
    </aside>
  )
}

function LogoutIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
    </svg>
  )
}

function DashboardIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
    </svg>
  )
}

function WarningIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
    </svg>
  )
}

function ReportIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M9 17v-6m3 6V7m3 10v-4M5 21h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v14a2 2 0 002 2z" />
    </svg>
  )
}

function LinkIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M13.828 10.172a4 4 0 00-5.656 0l-3 3a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l3-3a4 4 0 00-5.656-5.656l-1.1 1.1" />
    </svg>
  )
}

function MapIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M15 10.5a3 3 0 11-6 0 3 3 0 016 0z" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M19.5 10.5c0 7.142-7.5 11.25-7.5 11.25S4.5 17.642 4.5 10.5a7.5 7.5 0 1115 0z" />
    </svg>
  )
}

function GlobeIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M3.6 9h16.8M3.6 15h16.8M12 3a15 15 0 010 18M12 3a15 15 0 000 18" />
    </svg>
  )
}

function ShieldIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 3l8 4v5c0 5-3.5 8.5-8 9-4.5-.5-8-4-8-9V7l8-4z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 13l2 2 4-4" />
    </svg>
  )
}

function RequestIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M3 13h4l2 3h6l2-3h4M4 13l2.2-7.1A2 2 0 018.1 4.5h7.8a2 2 0 011.9 1.4L20 13v4.5a2 2 0 01-2 2H6a2 2 0 01-2-2V13z" />
    </svg>
  )
}

function JournalIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
    </svg>
  )
}

function OutageIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <circle cx="12" cy="12" r="8.5" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 7.5V12l3 2" />
    </svg>
  )
}

function RouterIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <rect x="3" y="13" width="18" height="7" rx="2" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M7 16.5h.01M10.5 16.5h.01M12 10V7m-4 3L6 5m10 5l2-5" />
    </svg>
  )
}

function FilterIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M3 4h18l-7 8v6l-4 2v-8L3 4z" />
    </svg>
  )
}

function PlugIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 22v-5m0 0a5 5 0 005-5V8H7v4a5 5 0 005 5zM9 8V3m6 5V3" />
    </svg>
  )
}

function SettingsIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
    </svg>
  )
}

/** Clé — section Administration (profils et comptes). */
function KeyIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24"
         stroke="currentColor" strokeWidth={1.9}>
      <path strokeLinecap="round" strokeLinejoin="round"
            d="M15 7a4 4 0 11-3.87 5H8v3H5v3H2v-3l6.13-6.13A4 4 0 0115 7z" />
      <circle cx="16.5" cy="7.5" r="1.1" fill="currentColor" stroke="none" />
    </svg>
  )
}
