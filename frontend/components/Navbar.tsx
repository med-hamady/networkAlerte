'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { HealthResponse } from '@/lib/types'

const links = [
  { href: '/',               label: 'Dashboard'      },
  { href: '/devices',        label: 'Équipements'    },
  { href: '/incidents',      label: 'Incidents'      },
  { href: '/notifications',  label: 'Notifications'  },
]

export default function Navbar() {
  const pathname = usePathname()
  const { data: health } = useSWR<HealthResponse>(
    endpoints.health,
    fetcher,
    { refreshInterval: 30_000 },
  )

  const dbOk = health?.database === 'connected'

  return (
    <nav className="bg-gray-900 text-white">
      <div className="max-w-7xl mx-auto px-4 flex items-center justify-between h-14">

        {/* Logo */}
        <span className="font-bold text-lg tracking-tight">
          Network Supervisor
        </span>

        {/* Navigation links */}
        <div className="flex items-center gap-1">
          {links.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              className={`px-4 py-1.5 rounded text-sm font-medium transition-colors ${
                pathname === href || (href !== '/' && pathname.startsWith(href))
                  ? 'bg-gray-700 text-white'
                  : 'text-gray-300 hover:bg-gray-800 hover:text-white'
              }`}
            >
              {label}
            </Link>
          ))}
        </div>

        {/* System status indicator */}
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <span
            className={`w-2 h-2 rounded-full ${
              health === undefined
                ? 'bg-gray-500'
                : dbOk
                ? 'bg-green-400'
                : 'bg-red-500'
            }`}
          />
          {health === undefined ? 'Connexion...' : dbOk ? 'Système OK' : 'Erreur DB'}
        </div>
      </div>
    </nav>
  )
}
