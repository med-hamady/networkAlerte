'use client'

import type { ReactNode } from 'react'
import type { Device } from '@/lib/types'
import { deviceLabel, timeAgo } from '@/lib/types'
import DeviceImage, { devicePhotoVariant } from './DeviceImage'
import IpLink from './IpLink'

interface Props {
  device: Device
  onClick: (device: Device) => void
  linkedLRCount?: number
}

export default function DeviceCard({ device, onClick, linkedLRCount = 0 }: Props) {
  const isUp   = device.status === 'up'
  const isDown = device.status === 'down'

  return (
    <button
      onClick={() => onClick(device)}
      className={`
        w-full text-left rounded-xl border transition-all duration-200
        hover:shadow-md hover:-translate-y-0.5 active:translate-y-0 cursor-pointer group
        bg-white
        ${isDown ? 'border-red-300' : 'border-blue-100 hover:border-blue-300'}
      `}
    >
      {/* Image area */}
      <div className={`rounded-t-xl flex items-center justify-center py-6 px-4 ${
        isDown ? 'bg-red-50' : 'bg-blue-50'
      }`}>
        <DeviceImage type={device.device_type} variant={devicePhotoVariant(device)} size="md" />
      </div>

      {/* Info area */}
      <div className="p-4 space-y-3">

        {/* Name + status */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="font-semibold text-slate-800 text-sm leading-tight truncate">{device.name}</p>
            <p className="text-blue-400 text-xs mt-0.5">{deviceLabel(device)}</p>
          </div>
          <StatusPill status={device.status} outOfSupervision={device.device_type === 'lr' && device.out_of_supervision} />
        </div>

        {/* Metadata rows */}
        <div className="space-y-1.5">
          <Row label="IP" value={<IpLink ip={device.ip_address} className="font-mono" />} />
          <Row
            label="Vu"
            value={
              <span className={isDown ? 'text-red-500' : 'text-slate-600'}>
                {timeAgo(device.last_seen)}
              </span>
            }
          />
          {device.location && <Row label="Site"   value={device.location} />}
        </div>

        {/* LR badge (only for Rockets) */}
        {device.device_type === 'rocket' && (
          <div className="flex items-center gap-1.5">
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold border ${
              linkedLRCount > 0
                ? 'bg-blue-50 text-blue-600 border-blue-200'
                : 'bg-slate-50 text-slate-400 border-slate-200'
            }`}>
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
              {linkedLRCount > 0 ? `${linkedLRCount} LR lié${linkedLRCount > 1 ? 's' : ''}` : 'Aucun LR'}
            </span>
          </div>
        )}

        {/* Click hint */}
        <div className="pt-2 border-t border-blue-50 flex items-center justify-between">
          <span className="text-xs text-blue-300">Voir les détails</span>
          <span className="text-blue-300 group-hover:text-blue-500 transition-colors text-sm">→</span>
        </div>
      </div>
    </button>
  )
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between text-xs gap-2">
      <span className="text-blue-300 shrink-0">{label}</span>
      <span className="text-slate-600 text-right truncate">{value}</span>
    </div>
  )
}

function StatusPill({ status, outOfSupervision }: { status: string; outOfSupervision?: boolean }) {
  if (status === 'up') return (
    <div className="flex items-center gap-1.5 shrink-0">
      <span className="relative flex h-2.5 w-2.5">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-60" />
        <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-green-500" />
      </span>
      <span className="text-green-600 text-xs font-bold">UP</span>
    </div>
  )
  if (status === 'down') return (
    <div className="flex items-center gap-1.5 shrink-0">
      <span className="inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
      <span className="text-red-500 text-xs font-bold">DOWN</span>
    </div>
  )
  // Aucune source ne parle de cet abonné : ni ping (pas d'IP) ni UISP depuis
  // des jours. Ce n'est PAS une panne constatée — le rouge le faisait lire
  // comme telle, alors qu'ils représentaient 12 % du parc. Ambre + libellé
  // explicite : on ne sait pas, et on le dit.
  if (outOfSupervision) return (
    <div className="flex items-center gap-1.5 shrink-0" title="Sans IP et non vu par UISP — aucune mesure possible">
      <span className="inline-flex h-2.5 w-2.5 rounded-full bg-amber-400" />
      <span className="text-amber-600 text-xs font-bold">HORS SUPERVISION</span>
    </div>
  )
  // Statut non mesurable mais récent (LR qui vient de perdre son IP) : rouge,
  // pas un tiret neutre. Il n'est pas joignable — le "—" le rendait invisible.
  return (
    <div className="flex items-center gap-1.5 shrink-0">
      <span className="inline-flex h-2.5 w-2.5 rounded-full bg-red-400" />
      <span className="text-red-500 text-xs font-bold">INCONNU</span>
    </div>
  )
}
