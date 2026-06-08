'use client'

import React from 'react'
import type { Device } from '@/lib/types'
import { deviceLabel, formatUptime, formatDate } from '@/lib/types'
import IpLink from './IpLink'

interface Props {
  site: string | null     // site name; null = closed
  devices: Device[]       // infra devices currently down for this site
  onClose: () => void
  onSelect?: (device: Device) => void  // open the full device detail
}

function downForSeconds(lastSeen: string | null): number | null {
  if (!lastSeen) return null
  return Math.max(0, Math.floor((Date.now() - new Date(lastSeen).getTime()) / 1000))
}

export default function PanneDetailsModal({ site, devices, onClose, onSelect }: Props) {
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  if (site == null) return null

  // Longest outage first
  const sorted = [...devices].sort((a, b) => {
    const ta = a.last_seen ? new Date(a.last_seen).getTime() : 0
    const tb = b.last_seen ? new Date(b.last_seen).getTime() : 0
    return ta - tb
  })

  return (
    <>
      <div
        className="fixed inset-0 bg-blue-900/30 backdrop-blur-sm z-40 animate-fade-in"
        onClick={onClose}
      />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div className="bg-white rounded-2xl border border-blue-100 shadow-2xl w-full max-w-lg max-h-[85vh] flex flex-col pointer-events-auto animate-fade-in">

          {/* Header */}
          <div className="flex items-start justify-between gap-3 px-6 py-5 border-b border-blue-100">
            <div className="min-w-0">
              <h2 className="text-lg font-bold text-blue-900 flex items-center gap-2">
                <span className="flex items-center justify-center h-7 w-7 rounded-lg bg-red-100 text-red-600 shrink-0">
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round"
                      d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                </span>
                Pannes — {site}
              </h2>
              <p className="text-blue-400 text-sm mt-1">
                {sorted.length} équipement{sorted.length > 1 ? 's' : ''} hors ligne
              </p>
            </div>
            <button
              onClick={onClose}
              className="text-blue-300 hover:text-blue-600 transition-colors text-xl leading-none shrink-0"
              aria-label="Fermer"
            >
              ✕
            </button>
          </div>

          {/* List */}
          <div className="overflow-y-auto divide-y divide-blue-50">
            {sorted.length === 0 ? (
              <p className="px-6 py-10 text-center text-blue-300 text-sm">Aucune panne en cours.</p>
            ) : (
              sorted.map(d => {
                const secs = downForSeconds(d.last_seen)
                return (
                  <button
                    key={d.id}
                    onClick={() => onSelect?.(d)}
                    className="w-full text-left px-6 py-4 flex items-center justify-between gap-4 hover:bg-blue-50/50 transition-colors"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="inline-flex h-2.5 w-2.5 rounded-full bg-red-500 shrink-0" />
                        <p className="font-semibold text-slate-800 text-sm truncate">{d.name}</p>
                      </div>
                      <p className="text-blue-400 text-xs mt-0.5 ml-[18px]">
                        {deviceLabel(d)} · <IpLink ip={d.ip_address} className="font-mono" />
                      </p>
                    </div>
                    <div className="text-right shrink-0">
                      <p className="text-red-500 font-bold text-sm">
                        {secs != null ? formatUptime(secs) : '—'}
                      </p>
                      <p className="text-blue-300 text-[11px] mt-0.5">
                        depuis {formatDate(d.last_seen)}
                      </p>
                    </div>
                  </button>
                )
              })
            )}
          </div>
        </div>
      </div>
    </>
  )
}
