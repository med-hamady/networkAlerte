import type { ReactNode } from 'react'

interface StatCardProps {
  label: string
  value: number | string
  accent?: string
  icon: ReactNode
}

function StatCard({ label, value, accent = 'text-blue-900', icon }: StatCardProps) {
  return (
    <div className="bg-white border border-blue-100 rounded-xl px-5 py-4 flex items-center gap-4 shadow-sm">
      <div className="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center shrink-0">
        {icon}
      </div>
      <div>
        <p className="text-xs font-medium text-blue-400 uppercase tracking-wider">{label}</p>
        <p className={`text-2xl font-bold mt-0.5 ${accent}`}>{value}</p>
      </div>
    </div>
  )
}

interface StatsBarProps {
  sites: number
  pannes: number
  clients: number
  total: number
  up: number
  down: number
  openIncidents: number
}

export default function StatsBar({ sites, pannes, clients, total, up, down, openIncidents }: StatsBarProps) {
  return (
    <div className="space-y-4">
      {/* Équipements / disponibilité / incidents */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Total équipements"
          value={total}
          accent="text-blue-900"
          icon={<GridIcon />}
        />
        <StatCard
          label="En ligne"
          value={up}
          accent="text-green-600"
          icon={<CheckIcon />}
        />
        <StatCard
          label="Hors ligne"
          value={down}
          accent={down > 0 ? 'text-red-500' : 'text-blue-900'}
          icon={<XIcon />}
        />
        <StatCard
          label="Incidents ouverts"
          value={openIncidents}
          accent={openIncidents > 0 ? 'text-orange-500' : 'text-blue-900'}
          icon={<WarningIcon />}
        />
      </div>

      {/* Vue par site */}
      <div className="grid grid-cols-3 gap-4">
        <StatCard
          label="Sites"
          value={sites}
          accent="text-blue-900"
          icon={<SiteIcon />}
        />
        <StatCard
          label="Pannes"
          value={pannes}
          accent={pannes > 0 ? 'text-red-500' : 'text-blue-900'}
          icon={<WarningIcon />}
        />
        <StatCard
          label="Clients"
          value={clients}
          accent="text-blue-900"
          icon={<UsersIcon />}
        />
      </div>
    </div>
  )
}

function GridIcon() {
  return (
    <svg className="w-5 h-5 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg className="w-5 h-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  )
}

function XIcon() {
  return (
    <svg className="w-5 h-5 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  )
}

function SiteIcon() {
  return (
    <svg className="w-5 h-5 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M17.657 16.657L13.414 20.9a2 2 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
    </svg>
  )
}

function UsersIcon() {
  return (
    <svg className="w-5 h-5 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a4 4 0 013-3.87m6-1.13a4 4 0 10-4-4 4 4 0 004 4zm6 0a3 3 0 10-2.83-4" />
    </svg>
  )
}

function WarningIcon() {
  return (
    <svg className="w-5 h-5 text-orange-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
    </svg>
  )
}
