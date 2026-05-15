'use client'

/**
 * Opened from the /devices list when the operator clicks the "discover" icon
 * on an LR row. Calls the backend (POST /devices/{lr_id}/discover-modems),
 * shows TP-Link candidates detected on the LR's LAN side, and on pick hands
 * a DeviceFormPrefill back to the parent so the create-modem form opens
 * pre-filled with the candidate's IP + best-guess name.
 */

import { useEffect, useState } from 'react'
import type { Lr } from '@/lib/types'
import type { LanNeighbor } from '@/lib/api'
import { discoverModemsViaLr } from '@/lib/api'
import type { DeviceFormPrefill } from './DeviceFormModal'

interface Props {
  lr: Lr | null
  onClose: () => void
  onPick: (prefill: DeviceFormPrefill) => void
}

export default function LrDiscoveryModal({ lr, onClose, onPick }: Props) {
  const [loading, setLoading] = useState(false)
  const [candidates, setCandidates] = useState<LanNeighbor[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const runDiscovery = async (lrId: number) => {
    setLoading(true)
    setError(null)
    setCandidates(null)
    try {
      const res = await discoverModemsViaLr(lrId)
      setCandidates(res.candidates)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur découverte')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (lr) runDiscovery(lr.id)
    else {
      setCandidates(null)
      setError(null)
    }
  }, [lr])

  if (!lr) return null

  const pick = (n: LanNeighbor) => {
    const name = n.model_guess ? `TP-Link ${n.model_guess}` : `Modem TP-Link ${n.ip}`
    onPick({
      device_type: 'client_modem',
      name,
      ip_address: n.ip,
      lr_id: lr.id,
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />

      <div className="relative bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-blue-100">
          <div>
            <h2 className="text-lg font-bold text-blue-900">Découverte des modems</h2>
            <p className="text-xs text-blue-400 mt-0.5">
              Via SSH depuis <span className="font-mono">{lr.name}</span> ({lr.ip_address})
            </p>
          </div>
          <button onClick={onClose} className="text-blue-300 hover:text-blue-600">
            <XIcon className="w-5 h-5" />
          </button>
        </div>

        <div className="overflow-y-auto flex-1 px-6 py-5 space-y-3">
          {loading && (
            <div className="text-sm text-blue-500">
              Interrogation du LR en cours — wget HTTP sur chaque voisin ARP (~quelques secondes).
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}

          {!loading && !error && candidates !== null && candidates.length === 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-sm text-amber-800">
              Aucun modem TP-Link détecté côté LAN du LR. Vérifie que le modem est branché et qu'il a eu du trafic récemment.
            </div>
          )}

          {!loading && candidates && candidates.length > 0 && (
            <div className="bg-white border border-purple-200 rounded-lg divide-y divide-purple-100">
              <div className="px-3 py-1.5 text-[11px] uppercase tracking-wide text-purple-600 font-semibold bg-purple-50">
                Candidats détectés — cliquer pour ouvrir le formulaire pré-rempli
              </div>
              {candidates.map(n => (
                <button
                  key={n.mac}
                  type="button"
                  onClick={() => pick(n)}
                  className="w-full text-left px-3 py-2 hover:bg-purple-50 flex items-center justify-between gap-3"
                >
                  <div className="flex flex-col">
                    <span className="font-mono text-sm text-slate-800">
                      {n.ip}
                      {n.model_guess && (
                        <span className="ml-2 font-sans text-xs text-purple-700 font-semibold">
                          {n.model_guess}
                        </span>
                      )}
                    </span>
                    <span className="font-mono text-[11px] text-blue-400">{n.mac} · {n.interface}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    {n.is_default_gateway && (
                      <span className="text-[10px] uppercase tracking-wide bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full font-semibold">
                        Gateway
                      </span>
                    )}
                    <span className="text-[11px] text-purple-700 font-medium">{n.vendor}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="px-6 py-4 border-t border-blue-100 flex items-center justify-between gap-3">
          <button
            onClick={() => runDiscovery(lr.id)}
            disabled={loading}
            className="text-sm text-blue-700 hover:text-blue-900 font-medium disabled:opacity-50"
          >
            {loading ? 'Recherche…' : '↻ Relancer'}
          </button>
          <button onClick={onClose} className="text-sm text-blue-500 hover:text-blue-700 px-4 py-2 rounded-lg">
            Fermer
          </button>
        </div>
      </div>
    </div>
  )
}

function XIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  )
}
