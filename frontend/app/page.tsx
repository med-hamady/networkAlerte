'use client'

import React, { useState } from 'react'
import Link from 'next/link'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Device, Incident } from '@/lib/types'
import { alertTypeLabel, formatDate } from '@/lib/types'
import StatsBar from '@/components/StatsBar'
import SeverityBadge from '@/components/SeverityBadge'
import IncidentStatusBadge from '@/components/IncidentStatusBadge'
import DeviceCard from '@/components/DeviceCard'
import DeviceDetailModal from '@/components/DeviceDetailModal'

const REFRESH = 15_000

export default function DashboardPage() {
  const [selected, setSelected] = useState<Device | null>(null)

  const { data: devices, isLoading: loadingDevices } = useSWR<Device[]>(
    endpoints.devices, fetcher, { refreshInterval: REFRESH },
  )
  const { data: incidents, isLoading: loadingIncidents } = useSWR<Incident[]>(
    `${endpoints.incidents}?status=open&limit=10`, fetcher, { refreshInterval: REFRESH },
  )

  const total   = devices?.length  ?? 0
  const up      = devices?.filter(d => d.status === 'up').length   ?? 0
  const down    = devices?.filter(d => d.status === 'down').length ?? 0
  const openInc = incidents?.length ?? 0

  const deviceNames = Object.fromEntries(devices?.map(d => [d.id, d.name]) ?? [])

  const childrenMap: Record<number, number> = {}
  devices?.forEach(d => {
    if (d.parent_id != null) {
      childrenMap[d.parent_id] = (childrenMap[d.parent_id] ?? 0) + 1
    }
  })

  return (
    <>
      <div className="space-y-7">

        {/* Page header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Dashboard</h1>
            <p className="text-blue-400 text-sm mt-1">
              Supervision réseau — actualisation toutes les {REFRESH / 1000}s
            </p>
          </div>
          <div className="text-blue-400 text-xs bg-white border border-blue-100 px-3 py-1.5 rounded-lg shadow-sm">
            {new Date().toLocaleDateString('fr-FR', {
              weekday: 'long', day: 'numeric', month: 'long',
            })}
          </div>
        </div>

        {/* KPI bar */}
        <StatsBar total={total} up={up} down={down} openIncidents={openInc} />

        {/* Equipment grid */}
        <section>
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-blue-900 text-lg">État des équipements</h2>
            <Link href="/devices" className="text-sm text-blue-500 hover:text-blue-700 transition-colors">
              Vue tableau →
            </Link>
          </div>

          {loadingDevices ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
              {Array.from({ length: 4 }, (_, i) => (
                <div key={i} className="rounded-xl bg-white border border-blue-100 h-64 animate-pulse" />
              ))}
            </div>
          ) : !devices?.length ? (
            <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm space-y-2">
              <p className="text-blue-700 font-medium">Aucun équipement enregistré</p>
              <p className="text-blue-400 text-sm">
                <code className="bg-blue-50 px-2 py-0.5 rounded text-xs">POST /api/v1/devices</code>
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
              {devices.map(d => (
                <DeviceCard
                  key={d.id}
                  device={d}
                  onClick={setSelected}
                  linkedLRCount={childrenMap[d.id] ?? 0}
                />
              ))}
            </div>
          )}
        </section>

        {/* Open incidents */}
        <section>
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-blue-900 text-lg flex items-center gap-2">
              Incidents ouverts
              {openInc > 0 && (
                <span className="bg-red-100 text-red-600 text-xs font-bold px-2 py-0.5 rounded-full border border-red-200">
                  {openInc}
                </span>
              )}
            </h2>
            <Link href="/incidents" className="text-sm text-blue-500 hover:text-blue-700 transition-colors">
              Voir tout →
            </Link>
          </div>

          <div className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
            {loadingIncidents ? (
              <div className="px-6 py-10 text-center text-blue-300 text-sm">Chargement…</div>
            ) : !incidents?.length ? (
              <div className="px-6 py-10 text-center">
                <p className="text-green-600 font-semibold text-sm">✓ Aucun incident ouvert</p>
                <p className="text-blue-400 text-xs mt-1">Tous les équipements fonctionnent normalement</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-blue-50 border-b border-blue-100">
                  <tr>
                    {['Détecté le', 'Équipement', 'Type', 'Incident', 'Sévérité', 'Statut'].map(h => (
                      <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-blue-50">
                  {incidents.map(inc => (
                    <tr key={inc.id} className="hover:bg-blue-50/50 transition-colors">
                      <td className="px-5 py-3 text-blue-400 whitespace-nowrap text-xs">{formatDate(inc.detected_at)}</td>
                      <td className="px-5 py-3 font-medium text-slate-800 whitespace-nowrap">
                        {inc.device_name ?? deviceNames[inc.device_id] ?? `#${inc.device_id}`}
                      </td>
                      <td className="px-5 py-3 whitespace-nowrap">
                        {inc.alert_type ? (
                          <span
                            title={inc.alert_type}
                            className={`text-xs font-medium px-2 py-0.5 rounded-full border ${
                              inc.alert_type === 'lr_no_transit'
                                ? 'bg-orange-50 text-orange-700 border-orange-200'
                                : inc.severity === 'critical'
                                ? 'bg-red-50 text-red-700 border-red-200'
                                : 'bg-blue-50 text-blue-600 border-blue-200'
                            }`}
                          >
                            {alertTypeLabel(inc.alert_type)}
                          </span>
                        ) : (
                          <span className="text-blue-200 text-xs">—</span>
                        )}
                      </td>
                      <td className="px-5 py-3 text-slate-600 max-w-xs truncate" title={inc.title}>{inc.title}</td>
                      <td className="px-5 py-3"><SeverityBadge severity={inc.severity} /></td>
                      <td className="px-5 py-3"><IncidentStatusBadge status={inc.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      </div>

      <DeviceDetailModal
        device={selected}
        devices={devices ?? []}
        onClose={() => setSelected(null)}
        onNavigate={setSelected}
      />
    </>
  )
}
