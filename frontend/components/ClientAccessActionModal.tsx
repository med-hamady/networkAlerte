'use client'

import React from 'react'
import { setClientBlock, type ClientBlockResult } from '@/lib/api'
import type { BlockMode, Lr } from '@/lib/types'

interface Props {
  lr: Lr | null
  action: 'block' | 'unblock'
  onClose: () => void
  onSuccess: (result: ClientBlockResult) => void
}

/**
 * Confirmation modal for cutting / restoring a client's internet access.
 * Block mode: mode picker (full / whatsapp_only) + optional reason.
 * Unblock mode: simple confirmation (always undoes both mechanisms).
 *
 * Closed when `lr` is null. Resets its own state every time `lr` or
 * `action` changes.
 */
export default function ClientAccessActionModal({ lr, action, onClose, onSuccess }: Props) {
  const [mode, setMode] = React.useState<BlockMode>('full')
  const [reason, setReason] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (lr != null) {
      setMode(lr.block_mode ?? 'full')
      setReason('')
      setError(null)
      setBusy(false)
    }
  }, [lr?.id, action]) // eslint-disable-line react-hooks/exhaustive-deps

  if (lr == null) return null

  const isBlock = action === 'block'

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await setClientBlock(
        lr.id,
        isBlock,
        isBlock ? reason : undefined,
        isBlock ? mode : undefined,
      )
      onSuccess(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erreur réseau')
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 px-4">
      <div
        role="dialog"
        aria-modal="true"
        className="w-full max-w-md bg-white rounded-2xl shadow-2xl border border-blue-100 overflow-hidden"
      >
        <div className={`px-5 py-3 border-b border-blue-100 flex items-center justify-between ${
          isBlock ? 'bg-red-50' : 'bg-green-50'
        }`}>
          <h2 className={`font-semibold text-sm ${
            isBlock ? 'text-red-700' : 'text-green-700'
          }`}>
            {isBlock ? "Couper l'accès internet" : "Rétablir l'accès"}
          </h2>
          <button
            onClick={onClose}
            disabled={busy}
            className="text-blue-400 hover:text-blue-700 transition-colors disabled:opacity-40"
            aria-label="Fermer"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <div>
            <p className="text-xs text-blue-400">Client</p>
            <p className="text-base font-semibold text-slate-800">{lr.name}</p>
            <p className="text-xs text-blue-300 font-mono">{lr.ip_address}</p>
          </div>

          {isBlock ? (
            <>
              <div className="space-y-1.5">
                <p className="text-xs font-semibold text-blue-600">Mode de blocage</p>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setMode('full')}
                    disabled={busy}
                    className={`py-2 px-2 rounded-lg text-xs font-semibold border transition-colors disabled:opacity-40 ${
                      mode === 'full'
                        ? 'bg-red-600 text-white border-red-600'
                        : 'bg-white text-blue-600 border-blue-200 hover:bg-blue-50'
                    }`}
                  >
                    Coupure totale
                  </button>
                  <button
                    type="button"
                    onClick={() => setMode('whatsapp_only')}
                    disabled={busy}
                    className={`py-2 px-2 rounded-lg text-xs font-semibold border transition-colors disabled:opacity-40 ${
                      mode === 'whatsapp_only'
                        ? 'bg-amber-500 text-white border-amber-500'
                        : 'bg-white text-blue-600 border-blue-200 hover:bg-blue-50'
                    }`}
                  >
                    WhatsApp autorisé
                  </button>
                </div>
                <p className="text-[11px] text-blue-400 leading-relaxed pt-1">
                  {mode === 'full' ? (
                    <>Ferme le port LAN du LR via SSH. Le client perd <strong>tout internet</strong>.</>
                  ) : (
                    <>Filtre iptables laissant DNS + WhatsApp. Facebook/Instagram bloqués nominativement.
                    Le client garde WhatsApp pour le support / paiement.</>
                  )}
                </p>
              </div>

              <div className="space-y-1.5">
                <label htmlFor="block-reason" className="text-xs font-semibold text-blue-600 block">
                  Motif (optionnel)
                </label>
                <textarea
                  id="block-reason"
                  value={reason}
                  onChange={e => setReason(e.target.value)}
                  placeholder="Ex : impayé facture #1234"
                  rows={2}
                  disabled={busy}
                  className="w-full text-sm rounded-lg border border-blue-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-red-200 disabled:opacity-40"
                />
              </div>

              <p className="text-[11px] text-red-500">
                {mode === 'whatsapp_only' ? (
                  <><strong>{lr.name}</strong> perdra internet sauf WhatsApp (et DNS).</>
                ) : (
                  <><strong>{lr.name}</strong> perdra immédiatement tout internet.</>
                )}
              </p>
            </>
          ) : (
            <p className="text-sm text-green-700">
              L'accès internet de <strong>{lr.name}</strong> sera rétabli (port LAN remonté
              et filtre WhatsApp retiré si présent).
            </p>
          )}

          {error && (
            <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
              {error}
            </div>
          )}
        </div>

        <div className="px-5 py-3 border-t border-blue-100 bg-blue-50/40 flex gap-2 justify-end">
          <button
            onClick={onClose}
            disabled={busy}
            className="px-4 py-2 rounded-lg bg-white border border-blue-200 text-blue-600 text-sm font-semibold hover:bg-blue-100 disabled:opacity-40"
          >
            Annuler
          </button>
          <button
            onClick={submit}
            disabled={busy}
            className={`px-4 py-2 rounded-lg text-white text-sm font-semibold disabled:opacity-40 ${
              isBlock
                ? 'bg-red-600 hover:bg-red-700'
                : 'bg-green-600 hover:bg-green-700'
            }`}
          >
            {busy
              ? (isBlock ? 'Coupure…' : 'Rétablissement…')
              : (isBlock ? 'Confirmer la coupure' : 'Confirmer le rétablissement')}
          </button>
        </div>
      </div>
    </div>
  )
}
