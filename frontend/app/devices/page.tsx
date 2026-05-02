'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Device } from '@/lib/types'
import { deviceTypeLabel, formatDate, timeAgo } from '@/lib/types'
import StatusBadge from '@/components/StatusBadge'
import DeviceDetailModal from '@/components/DeviceDetailModal'
import DeviceFormModal from '@/components/DeviceFormModal'
import DeviceImage from '@/components/DeviceImage'

export default function DevicesPage() {
  const [selected, setSelected]       = useState<Device | null>(null)
  const [formOpen, setFormOpen]       = useState(false)
  const [editDevice, setEditDevice]   = useState<Device | null>(null)

  const { data: devices, isLoading, mutate } = useSWR<Device[]>(
    endpoints.devices,
    fetcher,
    { refreshInterval: 30_000 },
  )

  const up      = devices?.filter(d => d.status === 'up').length   ?? 0
  const down    = devices?.filter(d => d.status === 'down').length ?? 0
  const unknown = devices?.filter(d => d.status === 'unknown').length ?? 0

  const openCreate = () => { setEditDevice(null); setFormOpen(true) }
  const openEdit   = (d: Device, e: React.MouseEvent) => {
    e.stopPropagation()
    setEditDevice(d)
    setFormOpen(true)
  }

  return (
    <>
      <div className="space-y-6">

        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Équipements</h1>
            <p className="text-blue-400 text-sm mt-1">
              {devices ? (
                <span>
                  {devices.length} équipement(s) —{' '}
                  <span className="text-green-600 font-medium">{up} UP</span>
                  {down    > 0 && <span className="text-red-500 font-medium"> · {down} DOWN</span>}
                  {unknown > 0 && <span className="text-blue-300"> · {unknown} inconnu</span>}
                </span>
              ) : 'Chargement…'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => mutate()}
              className="text-sm text-blue-600 hover:text-blue-800 font-medium bg-white border border-blue-200 px-3 py-1.5 rounded-lg transition-colors shadow-sm"
            >
              ↻ Rafraîchir
            </button>
            <button
              onClick={openCreate}
              className="text-sm text-white bg-blue-700 hover:bg-blue-800 font-medium px-4 py-1.5 rounded-lg transition-colors shadow-sm flex items-center gap-1.5"
            >
              <PlusIcon className="w-4 h-4" />
              Ajouter un équipement
            </button>
          </div>
        </div>

        {/* Table */}
        <div className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
          {isLoading ? (
            <div className="px-6 py-12 text-center text-blue-300">Chargement…</div>
          ) : !devices?.length ? (
            <div className="px-6 py-12 text-center space-y-3">
              <p className="text-blue-700 font-medium">Aucun équipement enregistré</p>
              <p className="text-sm text-blue-400">
                Cliquez sur <strong className="text-blue-600">Ajouter un équipement</strong> pour commencer la supervision.
              </p>
              <button
                onClick={openCreate}
                className="inline-flex items-center gap-2 text-sm text-white bg-blue-700 hover:bg-blue-800 font-medium px-5 py-2 rounded-lg transition-colors"
              >
                <PlusIcon className="w-4 h-4" />
                Ajouter un équipement
              </button>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-blue-50 border-b border-blue-100">
                <tr>
                  {['', 'Nom', 'Adresse IP', 'Type', 'Modèle', 'Localisation', 'Statut', 'Dernière vue', 'Ajouté le', ''].map((h, i) => (
                    <th key={i} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-blue-50">
                {devices.map(d => (
                  <tr
                    key={d.id}
                    className="hover:bg-blue-50/60 transition-colors cursor-pointer"
                    onClick={() => setSelected(d)}
                  >
                    <td className="px-4 py-3">
                      <div className="w-10 h-10">
                        <DeviceImage type={d.device_type} size="sm" />
                      </div>
                    </td>
                    <td className="px-4 py-3 font-semibold text-slate-800">{d.name}</td>
                    <td className="px-4 py-3 font-mono text-blue-600 text-xs">{d.ip_address}</td>
                    <td className="px-4 py-3 text-slate-600">{deviceTypeLabel(d.device_type)}</td>
                    <td className="px-4 py-3 text-blue-400">{d.model ?? '—'}</td>
                    <td className="px-4 py-3 text-blue-400">{d.location ?? '—'}</td>
                    <td className="px-4 py-3"><StatusBadge status={d.status} /></td>
                    <td className="px-4 py-3 text-blue-400 whitespace-nowrap text-xs">{timeAgo(d.last_seen)}</td>
                    <td className="px-4 py-3 text-blue-300 whitespace-nowrap text-xs">{formatDate(d.created_at)}</td>
                    <td className="px-4 py-3">
                      <button
                        onClick={(e) => openEdit(d, e)}
                        title="Modifier"
                        className="p-1.5 rounded-lg text-blue-400 hover:text-blue-700 hover:bg-blue-100 transition-colors"
                      >
                        <PencilIcon className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <DeviceDetailModal
        device={selected}
        devices={devices ?? []}
        onClose={() => setSelected(null)}
        onNavigate={setSelected}
      />

      <DeviceFormModal
        open={formOpen}
        device={editDevice}
        onClose={() => setFormOpen(false)}
        onSaved={() => mutate()}
      />
    </>
  )
}

function PlusIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
    </svg>
  )
}

function PencilIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M15.232 5.232l3.536 3.536M9 13l6.586-6.586a2 2 0 012.828 2.828L11.828 15.828a2 2 0 01-1.414.586H7v-3a2 2 0 01.586-1.414z" />
    </svg>
  )
}
