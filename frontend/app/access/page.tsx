'use client'

import React from 'react'
import useSWR from 'swr'
import { endpoints, fetcher, type ClientBlockResult } from '@/lib/api'
import type { Device, Lr } from '@/lib/types'
import ClientAccessActionModal from '@/components/ClientAccessActionModal'

type Filter = 'all' | 'active' | 'blocked_full' | 'blocked_whatsapp' | 'bridge'

const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all',              label: 'Tous'             },
  { value: 'active',           label: 'Accès actif'      },
  { value: 'blocked_full',     label: 'Coupure totale'   },
  { value: 'blocked_whatsapp', label: 'WhatsApp autorisé' },
  { value: 'bridge',           label: 'Mode bridge ⚠'    },
]

function timeAgo(iso: string | null): string {
  if (!iso) return '—'
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60)        return `il y a ${Math.round(seconds)} s`
  if (seconds < 3600)      return `il y a ${Math.round(seconds / 60)} min`
  if (seconds < 86400)     return `il y a ${Math.round(seconds / 3600)} h`
  return `il y a ${Math.round(seconds / 86400)} j`
}

export default function AccessPage() {
  const { data: devices, mutate, isLoading } = useSWR<Device[]>(
    endpoints.devices,
    fetcher,
    { refreshInterval: 30_000 },
  )
  const [filter, setFilter] = React.useState<Filter>('all')
  const [search, setSearch] = React.useState('')

  // Modal state
  const [modalLr, setModalLr] = React.useState<Lr | null>(null)
  const [modalAction, setModalAction] = React.useState<'block' | 'unblock'>('block')

  const lrs = React.useMemo(
    () => (devices ?? []).filter((d): d is Lr => d.device_type === 'lr'),
    [devices],
  )

  const stats = React.useMemo(() => {
    const active = lrs.filter(l => !l.client_blocked).length
    const blockedFull = lrs.filter(l => l.client_blocked && l.block_mode === 'full').length
    const blockedWa = lrs.filter(l => l.client_blocked && l.block_mode === 'whatsapp_only').length
    const bridge = lrs.filter(l => l.topology_mode === 'bridge').length
    return { total: lrs.length, active, blockedFull, blockedWa, bridge }
  }, [lrs])

  const filtered = React.useMemo(() => {
    const q = search.trim().toLowerCase()
    return lrs.filter(lr => {
      if (q && !lr.name.toLowerCase().includes(q) && !lr.ip_address.includes(q)) return false
      switch (filter) {
        case 'active':           return !lr.client_blocked
        case 'blocked_full':     return lr.client_blocked && lr.block_mode === 'full'
        case 'blocked_whatsapp': return lr.client_blocked && lr.block_mode === 'whatsapp_only'
        case 'bridge':           return lr.topology_mode === 'bridge'
        case 'all':
        default:                 return true
      }
    })
  }, [lrs, filter, search])

  // Sort: bridge first, then blocked, then name
  const sorted = React.useMemo(() => {
    return [...filtered].sort((a, b) => {
      const aBridge = a.topology_mode === 'bridge' ? 0 : 1
      const bBridge = b.topology_mode === 'bridge' ? 0 : 1
      if (aBridge !== bBridge) return aBridge - bBridge
      const aBlocked = a.client_blocked ? 0 : 1
      const bBlocked = b.client_blocked ? 0 : 1
      if (aBlocked !== bBlocked) return aBlocked - bBlocked
      return a.name.localeCompare(b.name)
    })
  }, [filtered])

  const onActionSuccess = (_result: ClientBlockResult) => {
    setModalLr(null)
    mutate() // refresh the devices list
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Accès clients</h1>
        <p className="text-blue-400 text-sm mt-1">
          Gère l'accès internet de chaque client en coupant ou rétablissant depuis son LR.
          Deux modes au choix : <strong>coupure totale</strong> (shutdown du port LAN) ou
          <strong> WhatsApp autorisé</strong> (filtre laissant DNS + WhatsApp, FB/Insta bloqués).
        </p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Clients (total)" value={stats.total} tone="blue" />
        <StatCard label="Accès actif" value={stats.active} tone="green" />
        <StatCard
          label="Bloqués"
          value={stats.blockedFull + stats.blockedWa}
          tone="red"
          sub={stats.blockedFull + stats.blockedWa > 0
            ? `${stats.blockedFull} total · ${stats.blockedWa} WhatsApp`
            : undefined}
        />
        <StatCard
          label="Mode bridge"
          value={stats.bridge}
          tone={stats.bridge > 0 ? 'amber' : 'slate'}
          sub={stats.bridge > 0 ? 'à reconfigurer' : undefined}
        />
      </div>

      {/* Filters + search */}
      <div className="flex flex-wrap gap-3 items-center justify-between">
        <div className="flex flex-wrap gap-1 rounded-lg bg-white border border-blue-100 p-1 shadow-sm">
          {FILTERS.map(({ value, label }) => (
            <button
              key={value}
              onClick={() => setFilter(value)}
              className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
                filter === value
                  ? 'bg-blue-600 text-white'
                  : 'text-blue-600 hover:bg-blue-50'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <input
          type="search"
          placeholder="Recherche par nom ou IP…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full md:w-80 px-3 py-2 text-sm rounded-lg border border-blue-200 focus:outline-none focus:ring-2 focus:ring-blue-200"
        />
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center text-blue-300 shadow-sm">
          Chargement…
        </div>
      ) : sorted.length === 0 ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm">
          <p className="text-blue-400 text-sm">
            {lrs.length === 0
              ? 'Aucun LR enregistré.'
              : 'Aucun LR ne correspond au filtre / à la recherche.'}
          </p>
        </div>
      ) : (
        <div className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-blue-50 border-b border-blue-100">
                <tr>
                  {['Client', 'Topologie', 'État', 'Motif', 'Coupé depuis', 'Renforcé', 'Action'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-blue-50">
                {sorted.map(lr => {
                  const isBridge = lr.topology_mode === 'bridge'
                  const isBlocked = lr.client_blocked
                  const pendingEnforcement = isBlocked && lr.client_block_enforced_at == null
                  return (
                    <tr key={lr.id} className="hover:bg-blue-50/60 align-top">
                      <td className="px-4 py-3">
                        <div className="text-slate-800 font-medium">{lr.name}</div>
                        <div className="text-blue-300 font-mono text-[11px]">{lr.ip_address}</div>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <TopologyBadge mode={lr.topology_mode} />
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        {isBlocked ? (
                          <div className="flex flex-col gap-1">
                            <span className="text-red-500 font-semibold text-xs">● Accès coupé</span>
                            <ModeBadge mode={lr.block_mode} />
                          </div>
                        ) : (
                          <span className="text-green-600 font-semibold text-xs">● Accès actif</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-700 max-w-xs">
                        {lr.client_blocked_reason || <span className="text-blue-300">—</span>}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-xs text-slate-700">
                        {isBlocked
                          ? timeAgo(lr.client_blocked_at)
                          : <span className="text-blue-300">—</span>}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-xs">
                        {!isBlocked ? (
                          <span className="text-blue-300">—</span>
                        ) : pendingEnforcement ? (
                          <span className="text-amber-600 font-semibold">en attente (LR injoignable)</span>
                        ) : (
                          <span className="text-slate-700">{timeAgo(lr.client_block_enforced_at)}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        {isBlocked ? (
                          <button
                            onClick={() => { setModalLr(lr); setModalAction('unblock') }}
                            className="px-3 py-1.5 rounded-lg bg-green-600 text-white text-xs font-semibold hover:bg-green-700"
                          >
                            Rétablir
                          </button>
                        ) : isBridge ? (
                          <button
                            disabled
                            title="LR en mode bridge — repasser en routeur via airOS"
                            className="px-3 py-1.5 rounded-lg bg-blue-50 text-blue-300 text-xs font-semibold cursor-not-allowed"
                          >
                            Couper
                          </button>
                        ) : (
                          <button
                            onClick={() => { setModalLr(lr); setModalAction('block') }}
                            className="px-3 py-1.5 rounded-lg bg-red-600 text-white text-xs font-semibold hover:bg-red-700"
                          >
                            Couper
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <p className="text-[11px] text-blue-400 leading-relaxed">
        Les blocages sont ré-appliqués automatiquement sur le LR toutes les 120 s (survivent au reboot
        du LR). Le mode bridge est détecté toutes les 60 min — les LR en bridge ne peuvent pas être
        bloqués depuis cette page (iptables et dnsmasq sont contournés par leur configuration). Repasse
        ces LR en mode routeur via leur interface airOS.
      </p>

      <ClientAccessActionModal
        lr={modalLr}
        action={modalAction}
        onClose={() => setModalLr(null)}
        onSuccess={onActionSuccess}
      />
    </div>
  )
}

/* ─── Stat card ──────────────────────────────────────────────────────── */

type StatTone = 'blue' | 'green' | 'red' | 'amber' | 'slate'

function StatCard({ label, value, tone, sub }: {
  label: string
  value: number
  tone: StatTone
  sub?: string
}) {
  const toneClasses: Record<StatTone, string> = {
    blue:   'border-blue-200 bg-blue-50 text-blue-800',
    green:  'border-green-200 bg-green-50 text-green-800',
    red:    'border-red-200 bg-red-50 text-red-700',
    amber:  'border-amber-200 bg-amber-50 text-amber-800',
    slate:  'border-slate-200 bg-slate-50 text-slate-700',
  }
  return (
    <div className={`rounded-xl border px-4 py-3 ${toneClasses[tone]}`}>
      <p className="text-[11px] font-semibold uppercase tracking-wider opacity-80">{label}</p>
      <p className="text-2xl font-bold tabular-nums mt-0.5">{value}</p>
      {sub && <p className="text-[10px] opacity-70 mt-0.5">{sub}</p>}
    </div>
  )
}

/* ─── Badges ─────────────────────────────────────────────────────────── */

function TopologyBadge({ mode }: { mode: 'router' | 'bridge' | 'unknown' }) {
  if (mode === 'bridge') {
    return (
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-50 border border-amber-300 text-amber-800 text-[11px] font-semibold"
        title="Mauvaise configuration — le blocage ne peut pas fonctionner. Repasse le LR en mode routeur."
      >
        ⚠ Bridge
      </span>
    )
  }
  if (mode === 'router') {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-slate-50 border border-slate-200 text-slate-600 text-[11px] font-semibold">
        Routeur
      </span>
    )
  }
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-md bg-blue-50 border border-blue-200 text-blue-400 text-[11px] font-semibold"
      title="Topologie pas encore détectée (pas de credentials SSH ou première détection en attente)."
    >
      Inconnue
    </span>
  )
}

function ModeBadge({ mode }: { mode: 'full' | 'whatsapp_only' }) {
  if (mode === 'whatsapp_only') {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-amber-100 text-amber-800 text-[11px] font-semibold">
        WhatsApp autorisé
      </span>
    )
  }
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-red-100 text-red-700 text-[11px] font-semibold">
      Coupure totale
    </span>
  )
}
