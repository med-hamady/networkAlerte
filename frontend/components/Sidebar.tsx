'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { HealthResponse } from '@/lib/types'

const links = [
  { href: '/',                      label: 'Dashboard',   icon: DashboardIcon  },
  { href: '/devices',               label: 'Équipements', icon: ServerIcon     },
  { href: '/incidents',             label: 'Incidents',   icon: WarningIcon,   exact: true },
  { href: '/incidents/archive',     label: 'Archive',     icon: ArchiveIcon    },
  { href: '/topology',              label: 'Topologie',   icon: TopologyIcon   },
  { href: '/alert-policies',        label: 'Policies',    icon: PolicyIcon     },
  { href: '/notification-channels', label: 'Canaux',      icon: ChannelIcon    },
  { href: '/reports',               label: 'Rapports',    icon: ReportIcon     },
  { href: '/settings',              label: 'Seuils',      icon: SettingsIcon   },
]

export default function Sidebar() {
  const pathname = usePathname()
  const { data: health } = useSWR<HealthResponse>(
    endpoints.health,
    fetcher,
    { refreshInterval: 30_000 },
  )
  const dbOk = health?.database === 'connected'

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
      <nav className="flex-1 px-3 py-5 space-y-1">
        {links.map(({ href, label, icon: Icon, exact }) => {
          const isActive = exact ? pathname === href : pathname === href || pathname.startsWith(href + '/')
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                href === '/incidents/archive' ? 'pl-8' : ''
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
      </nav>

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

function A2LogoMark() {
  return (
    <div className="w-10 h-10 rounded-xl bg-white flex items-center justify-center shrink-0 relative overflow-hidden">
      <svg className="absolute inset-0 w-full h-full opacity-10" viewBox="0 0 40 40">
        <circle cx="8"  cy="8"  r="3" fill="#1d4ed8" />
        <circle cx="32" cy="8"  r="2" fill="#1d4ed8" />
        <circle cx="8"  cy="32" r="2" fill="#1d4ed8" />
        <circle cx="32" cy="32" r="3" fill="#1d4ed8" />
        <circle cx="20" cy="4"  r="2" fill="#1d4ed8" />
        <circle cx="36" cy="20" r="2" fill="#1d4ed8" />
      </svg>
      <span className="relative text-blue-900 font-black text-base tracking-tight z-10">A2</span>
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

function TopologyIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <circle cx="12" cy="5"  r="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="5"  cy="19" r="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="19" cy="19" r="2" strokeLinecap="round" strokeLinejoin="round" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 7v4m0 0l-5.5 6M12 11l5.5 6" />
    </svg>
  )
}

function PolicyIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
    </svg>
  )
}

function ArchiveIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
    </svg>
  )
}

function ChannelIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
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

function SettingsIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
    </svg>
  )
}
