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
import { discoverModemsViaLr, pingTargetFromLr } from '@/lib/api'
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
    const name = n.model_guess
      ? `TP-Link ${n.model_guess}`
      : n.vendor
        ? `Modem ${n.vendor} ${n.ip}`
        : `Modem ${n.ip}`
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
              Balayage du sous-réseau LAN depuis le LR (ping-sweep + lecture ARP) — ~10–30 s selon la taille du subnet.
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}

          {!loading && !error && candidates !== null && candidates.length === 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-sm text-amber-800">
              Aucun voisin détecté sur le LAN du LR après balayage. Vérifie que le modem est branché et alimenté, puis relance.
            </div>
          )}

          {!loading && candidates && candidates.length > 0 && (
            <div className="bg-white border border-purple-200 rounded-lg divide-y divide-purple-100">
              <div className="px-3 py-1.5 text-[11px] uppercase tracking-wide text-purple-600 font-semibold bg-purple-50">
                Candidats détectés — cliquer pour ouvrir le formulaire pré-rempli
              </div>
              {candidates.map(n => (
                <CandidateRow key={n.mac} n={n} lrId={lr.id} onPick={() => pick(n)} />
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

type PingState = { status: 'idle' | 'loading' | 'done'; ok?: boolean; msg?: string }

function CandidateRow({
  n,
  lrId,
  onPick,
}: {
  n: LanNeighbor
  lrId: number
  onPick: () => void
}) {
  const [ping, setPing] = useState<PingState>({ status: 'idle' })

  const test = async () => {
    setPing({ status: 'loading' })
    try {
      const r = await pingTargetFromLr(lrId, n.ip)
      setPing({ status: 'done', ok: r.ok, msg: r.message })
    } catch {
      setPing({ status: 'done', ok: false, msg: 'Erreur réseau' })
    }
  }

  return (
    <div className="px-3 py-2 hover:bg-purple-50 flex items-center justify-between gap-3">
      <button type="button" onClick={onPick} className="text-left flex-1 min-w-0">
        <span className="block font-mono text-sm text-slate-800">
          {n.ip}
          {n.model_guess && (
            <span className="ml-2 font-sans text-xs text-purple-700 font-semibold">
              {n.model_guess}
            </span>
          )}
        </span>
        <span className="block font-mono text-[11px] text-blue-400">{n.mac} · {n.interface}</span>
      </button>
      <div className="flex items-center gap-2 shrink-0">
        {n.is_default_gateway && (
          <span className="text-[10px] uppercase tracking-wide bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full font-semibold">
            Gateway
          </span>
        )}
        {n.vendor && <span className="text-[11px] text-purple-700 font-medium">{n.vendor}</span>}
        {ping.status === 'done' && (
          <span
            title={ping.msg}
            className={`text-[11px] font-semibold ${ping.ok ? 'text-green-600' : 'text-red-500'}`}
          >
            {ping.ok ? '● Joignable' : '✗ Injoignable'}
          </span>
        )}
        <button
          type="button"
          onClick={test}
          disabled={ping.status === 'loading'}
          className="text-[11px] text-blue-600 hover:text-blue-800 underline disabled:opacity-40"
        >
          {ping.status === 'loading' ? 'Ping…' : ping.status === 'done' ? '↻ Ping' : 'Tester ping'}
        </button>
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
