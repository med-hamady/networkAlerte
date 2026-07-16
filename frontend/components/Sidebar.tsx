'use client'

import { useEffect } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import useSWR from 'swr'
import { endpoints, fetcher, logout, type CurrentUser } from '@/lib/api'
import type { HealthResponse } from '@/lib/types'

type NavLink = {
  href: string
  label: string
  icon: (props: { className?: string }) => JSX.Element
  exact?: boolean
  indent?: boolean
}

type NavSection = {
  title: string
  links: NavLink[]
}

const sections: NavSection[] = [
  {
    title: 'Supervision',
    links: [
      { href: '/',           label: 'Dashboard',           icon: DashboardIcon },
      { href: '/sites',      label: 'Sites',               icon: ServerIcon    },
      { href: '/lr-health',  label: 'Liaisons clients',    icon: LinkIcon      },
      { href: '/clients',    label: 'Consommation clients', icon: TrafficIcon  },
      { href: '/capacity',   label: 'Capacité du réseau',  icon: CapacityIcon },
      { href: '/traffic',    label: 'Destinations Internet', icon: GlobeIcon  },
      { href: '/access',     label: 'FAI',                 icon: ShieldIcon   },
      { href: '/fai-journal', label: 'Journal blocages',   icon: JournalIcon, indent: true },
      { href: '/content-block', label: 'Filtre de contenu', icon: FilterIcon, indent: true },
    ],
  },
  {
    title: 'Anomalies',
    links: [
      { href: '/incidents',         label: 'Incidents',       icon: WarningIcon, exact: true },
    ],
  },
  {
    title: 'Configuration',
    links: [
      { href: '/reports',  label: 'Rapports', icon: ReportIcon   },
      { href: '/settings', label: 'Seuils',   icon: SettingsIcon },
    ],
  },
]

export default function Sidebar() {
  const pathname = usePathname()
  const router = useRouter()
  const { data: health } = useSWR<HealthResponse>(
    endpoints.health,
    fetcher,
    { refreshInterval: 30_000 },
  )
  // Identity of the logged-in operator. Used to (a) display who is logged in
  // in the footer, (b) trigger a redirect to /login if the session is gone
  // (an expired cookie returns 401 → fetcher throws → SWR returns no data;
  // we treat that as "logged out" and bounce to the login page).
  const { data: currentUser, error: userError } = useSWR<CurrentUser>(
    endpoints.authMe,
    fetcher,
    { refreshInterval: 60_000, shouldRetryOnError: false },
  )
  const dbOk = health?.database === 'connected'

  const handleLogout = async () => {
    try {
      await logout()
    } finally {
      router.replace('/login')
    }
  }

  // Auto-redirect to /login if the session expired server-side (the cookie
  // exists so the middleware lets the page render, but /auth/me returns 401
  // and the fetcher throws). One redirect per failed lookup.
  useEffect(() => {
    if (userError) {
      router.replace('/login')
    }
  }, [userError, router])

  return (
    <aside className="w-60 min-h-screen bg-blue-900 flex flex-col shrink-0">

      {/* Brand */}
      <div className="px-5 py-5 border-b border-blue-800">
        <div className="flex items-center gap-3">
          <A2LogoMark />
          <div>
            <p className="text-white font-bold text-sm tracking-widest uppercase leading-none">
              A2 Holding
            </p>
            <p className="text-blue-300 text-xs mt-1">Network Supervisor</p>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-5 overflow-y-auto">
        {sections.map(({ title, links }) => (
          <div key={title} className="space-y-1">
            <p className="px-3 pb-1 text-[10px] font-bold tracking-widest uppercase text-blue-400">
              {title}
            </p>
            {links.map(({ href, label, icon: Icon, exact, indent }) => {
              const isActive = exact
                ? pathname === href
                : pathname === href || pathname.startsWith(href + '/')
              return (
                <Link
                  key={href}
                  href={href}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                    indent ? 'pl-8' : ''
                  } ${
                    isActive
                      ? 'bg-white text-blue-900'
                      : 'text-blue-200 hover:bg-blue-800 hover:text-white'
                  }`}
                >
                  <Icon className="w-4 h-4 shrink-0" />
                  {label}
                </Link>
              )
            })}
          </div>
        ))}
      </nav>

      {/* Logged-in user + logout */}
      <div className="px-4 py-3 border-t border-blue-800">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-blue-700 text-white text-xs font-bold flex items-center justify-center shrink-0">
            {(currentUser?.username ?? '?').slice(0, 2).toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm text-white font-medium truncate">
              {currentUser?.full_name || currentUser?.username || '—'}
            </p>
            {currentUser?.full_name && (
              <p className="text-[10px] text-blue-400 truncate">{currentUser.username}</p>
            )}
          </div>
          <button
            onClick={handleLogout}
            title="Se déconnecter"
            className="p-1.5 rounded-lg text-blue-300 hover:text-white hover:bg-blue-800 transition-colors shrink-0"
          >
            <LogoutIcon className="w-4 h-4" />
          </button>
        </div>
        {userError && (
          <p className="text-[10px] text-red-300 mt-1">Session expirée — reconnecte-toi.</p>
        )}
      </div>

      {/* System status footer */}
      <div className="px-4 py-4 border-t border-blue-800">
        <div className="flex items-center gap-2 px-1">
          <span className={`w-2 h-2 rounded-full shrink-0 ${
            health === undefined
              ? 'bg-blue-400 animate-pulse'
              : dbOk
              ? 'bg-green-400'
              : 'bg-red-400'
          }`} />
          <span className="text-xs text-blue-300">
            {health === undefined
              ? 'Connexion…'
              : dbOk
              ? 'Système opérationnel'
              : 'Erreur base de données'}
          </span>
        </div>
      </div>
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

function A2LogoMark() {
  return (
    <div className="w-10 h-10 rounded-xl bg-white flex items-center justify-center shrink-0 overflow-hidden p-1.5">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/a2-logo.png" alt="A2 Holding" className="w-full h-full object-contain" />
    </div>
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

function ServerIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01" />
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

function CapacityIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M11 3.055A9 9 0 1020.945 13H11V3.055z" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M20.488 9A9.004 9.004 0 0015 3.512V9h5.488z" />
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

function TrafficIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M3 17l4-4 4 4 7-7m0 0V5m0 5h-5" />
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

function JournalIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
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

function SettingsIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
    </svg>
  )
}
