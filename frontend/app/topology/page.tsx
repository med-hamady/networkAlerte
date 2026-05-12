'use client'

import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Device, DeviceMetrics, SystemInfo } from '@/lib/types'
import TopologyMap from '@/components/TopologyMap'

const REFRESH = 30_000

export default function TopologyPage() {
  const { data: devices, isLoading, mutate } = useSWR<Device[]>(
    endpoints.devices,
    fetcher,
    { refreshInterval: REFRESH },
  )

  // Fetch latest metrics for every device in parallel
  const deviceIds = devices?.map(d => d.id) ?? []
  const metricsKey = deviceIds.length > 0 ? `topo-metrics:${deviceIds.join(',')}` : null

  const { data: metricsMap } = useSWR<Record<number, DeviceMetrics>>(
    metricsKey,
    async () => {
      const results = await Promise.all(
        deviceIds.map(id =>
          fetch(endpoints.deviceMetrics(id))
            .then(r => r.ok ? r.json() : {})
            .catch(() => ({}))
        )
      )
      return Object.fromEntries(deviceIds.map((id, i) => [id, results[i]]))
    },
    { refreshInterval: REFRESH },
  )

  const { data: systemInfo } = useSWR<SystemInfo>(
    endpoints.systemInfo,
    fetcher,
    { refreshInterval: 30_000 },
  )

  const up      = devices?.filter(d => d.status === 'up').length   ?? 0
  const down    = devices?.filter(d => d.status === 'down').length ?? 0
  const unknown = devices?.filter(d => d.status === 'unknown').length ?? 0

  return (
    <div className="space-y-5">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Topologie réseau</h1>
          <p className="text-blue-400 text-sm mt-1">
            Architecture des équipements et liens — actualisation toutes les {REFRESH / 1000}s
          </p>
        </div>
        <button
          onClick={() => mutate()}
          className="text-sm text-blue-600 hover:text-blue-800 font-medium bg-white border border-blue-200 px-3 py-1.5 rounded-lg transition-colors shadow-sm"
        >
          ↻ Rafraîchir
        </button>
      </div>

      {/* Summary pills */}
      {devices && (
        <div className="flex gap-3 flex-wrap">
          <Pill color="slate"  label="Total"   value={devices.length} />
          <Pill color="green"  label="UP"      value={up} />
          {down    > 0 && <Pill color="red"    label="DOWN"    value={down} />}
          {unknown > 0 && <Pill color="blue"   label="Inconnu" value={unknown} />}
        </div>
      )}

      {/* Map card */}
      <div className="bg-white border border-blue-100 rounded-2xl shadow-sm p-5">
        {isLoading ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3 text-blue-200">
            <svg className="w-8 h-8 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
            </svg>
            <p className="text-sm">Chargement de la topologie…</p>
          </div>
        ) : (
          <TopologyMap
            devices={devices ?? []}
            metricsMap={metricsMap ?? {}}
            systemInfo={systemInfo ?? null}
          />
        )}
      </div>

      {/* Help note */}
      <p className="text-xs text-slate-400 text-center">
        Les liens sont déterminés par la relation parent/enfant de chaque équipement.
        Configurez le champ <span className="font-mono bg-slate-100 px-1 rounded">rocket_id</span> sur un LR via l&apos;API pour modifier la topologie.
      </p>
    </div>
  )
}

function Pill({
  color,
  label,
  value,
}: {
  color: 'slate' | 'green' | 'red' | 'blue'
  label: string
  value: number
}) {
  const colors = {
    slate: 'bg-slate-100 text-slate-600 border-slate-200',
    green: 'bg-green-50  text-green-700 border-green-200',
    red:   'bg-red-50    text-red-700   border-red-200',
    blue:  'bg-blue-50   text-blue-500  border-blue-100',
  }
  return (
    <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border ${colors[color]}`}>
      {label}
      <span className="font-bold">{value}</span>
    </span>
  )
}
