'use client'

import React from 'react'
import useSWR from 'swr'
import {
  cancelBroadcast,
  createBroadcast,
  endpoints,
  fetcher,
  previewBroadcast,
  retryBroadcastFailures,
} from '@/lib/api'
import type {
  Broadcast,
  BroadcastAudience,
  BroadcastDetail,
  BroadcastList,
  BroadcastPreview,
  BroadcastStatus,
} from '@/lib/types'
import { PERM, usePermissions } from '@/lib/permissions'

// Les catégories de /access, avec la même règle (appliquée côté serveur).
const AUDIENCES: Array<{ key: BroadcastAudience; label: string; hint: string }> = [
  { key: 'active', label: 'Clients actifs', hint: 'Non bloqués et supervisés — la tuile « Accès actif ».' },
  { key: 'blocked', label: 'Clients bloqués', hint: 'Coupés (impayé ou autre).' },
  { key: 'out_of_supervision', label: 'Hors supervision', hint: "Sans IP et non vus par UISP depuis longtemps." },
]

const AUDIENCE_LABEL: Record<string, string> = {
  active: 'Actifs', blocked: 'Bloqués', out_of_supervision: 'Hors supervision',
}

const STATUS: Record<BroadcastStatus, { label: string; cls: string }> = {
  running: { label: 'En cours', cls: 'bg-blue-50 text-blue-700 border-blue-200' },
  done: { label: 'Terminé', cls: 'bg-green-50 text-green-700 border-green-200' },
  cancelled: { label: 'Arrêté', cls: 'bg-slate-100 text-slate-600 border-slate-200' },
}

const MAX_LENGTH = 4000

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds} s`
  const min = Math.round(seconds / 60)
  if (min < 60) return `${min} min`
  return `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, '0')}`
}

function formatTs(ts: string | null): string {
  if (!ts) return '—'
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  return d.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export default function BroadcastPage() {
  const { can } = usePermissions()
  const maySend = can(PERM.broadcastSend)

  const [fastRefresh, setFastRefresh] = React.useState(false)
  const { data, mutate } = useSWR<BroadcastList>(endpoints.clientBroadcasts, fetcher, {
    // Pendant un envoi on suit l'avancement de près ; au repos, rien ne bouge.
    refreshInterval: fastRefresh ? 5_000 : 30_000,
    keepPreviousData: true,
  })
  const runningId = data?.running_id ?? null
  React.useEffect(() => setFastRefresh(runningId !== null), [runningId])

  const [selected, setSelected] = React.useState<number | null>(null)
  // Par défaut on suit l'envoi en cours, sinon le plus récent.
  const shownId = selected ?? runningId ?? data?.broadcasts[0]?.id ?? null

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Message aux clients</h1>
        <p className="text-sm text-blue-400 mt-1">
          Un message WhatsApp envoyé à chaque abonné, depuis le numéro de l'entreprise.
        </p>
      </div>

      {maySend && (
        <Composer
          busy={runningId !== null}
          onCreated={(b) => { setSelected(b.id); mutate() }}
        />
      )}

      {shownId !== null && (
        <BroadcastPanel
          id={shownId}
          maySend={maySend}
          live={shownId === runningId}
          onChanged={() => mutate()}
        />
      )}

      <History
        broadcasts={data?.broadcasts ?? []}
        loading={!data}
        selectedId={shownId}
        onSelect={setSelected}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Rédaction : catégories, aperçu, message, confirmation
// ---------------------------------------------------------------------------

function Composer({ busy, onCreated }: { busy: boolean; onCreated: (b: BroadcastDetail) => void }) {
  const [audiences, setAudiences] = React.useState<BroadcastAudience[]>(['active'])
  const [message, setMessage] = React.useState('')
  const [preview, setPreview] = React.useState<BroadcastPreview | null>(null)
  const [previewError, setPreviewError] = React.useState<string | null>(null)
  const [confirming, setConfirming] = React.useState(false)
  const [sending, setSending] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [showMissing, setShowMissing] = React.useState(false)

  const allSelected = audiences.length === AUDIENCES.length

  React.useEffect(() => {
    if (audiences.length === 0) {
      setPreview(null)
      return
    }
    let cancelled = false
    setPreviewError(null)
    previewBroadcast(audiences)
      .then((p) => { if (!cancelled) setPreview(p) })
      .catch((e) => { if (!cancelled) setPreviewError(e instanceof Error ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [audiences])

  function toggle(key: BroadcastAudience) {
    setAudiences((cur) => {
      const next = cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]
      return AUDIENCES.map((a) => a.key).filter((k) => next.includes(k))
    })
  }

  const trimmed = message.trim()
  const recipients = preview?.recipient_count ?? 0
  const canSend = !busy && !sending && trimmed.length > 0 && recipients > 0
    && preview?.whatsapp_available !== false && message.length <= MAX_LENGTH

  async function send() {
    setSending(true)
    setError(null)
    try {
      const created = await createBroadcast(audiences, message)
      setConfirming(false)
      setMessage('')
      onCreated(created)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setConfirming(false)
    } finally {
      setSending(false)
    }
  }

  return (
    <section className="bg-white rounded-xl border border-blue-100 overflow-hidden">
      <header className="px-5 py-3 bg-blue-50 border-b border-blue-100">
        <h2 className="text-sm font-bold text-blue-900">Nouveau message</h2>
      </header>

      <div className="p-5 space-y-5">
        {/* 1. Destinataires */}
        <div>
          <p className="text-xs font-bold uppercase tracking-wide text-blue-900 mb-2">Destinataires</p>
          <div className="flex flex-wrap gap-2">
            {AUDIENCES.map((a) => (
              <label
                key={a.key}
                title={a.hint}
                className={`cursor-pointer select-none rounded-lg border px-3 py-2 text-sm transition
                  ${audiences.includes(a.key)
                    ? 'border-blue-600 bg-blue-600 text-white'
                    : 'border-blue-200 bg-white text-blue-900 hover:border-blue-400'}`}
              >
                <input
                  type="checkbox" className="sr-only"
                  checked={audiences.includes(a.key)} onChange={() => toggle(a.key)}
                />
                {a.label}
              </label>
            ))}
            <button
              type="button"
              onClick={() => setAudiences(allSelected ? [] : AUDIENCES.map((a) => a.key))}
              className="rounded-lg border border-dashed border-blue-300 px-3 py-2 text-sm text-blue-700 hover:bg-blue-50"
            >
              {allSelected ? 'Tout désélectionner' : 'Tous les clients'}
            </button>
          </div>

          <div className="mt-3 text-sm">
            {audiences.length === 0 ? (
              <p className="text-amber-700">Choisir au moins une catégorie.</p>
            ) : previewError ? (
              <p className="text-red-700">{previewError}</p>
            ) : !preview ? (
              <p className="text-blue-400">Calcul des destinataires…</p>
            ) : (
              <div className="space-y-1">
                <p className="text-blue-900">
                  <span className="text-lg font-bold tabular-nums">{preview.recipient_count}</span>{' '}
                  numéro{preview.recipient_count > 1 ? 's' : ''} recevr{preview.recipient_count > 1 ? 'ont' : 'a'} le message
                  <span className="text-blue-400">
                    {' '}· {preview.lr_count} client{preview.lr_count > 1 ? 's' : ''} dans la sélection
                    {' '}· durée estimée {formatDuration(preview.estimated_seconds)}
                  </span>
                </p>
                {preview.duplicate_count > 0 && (
                  <p className="text-blue-400">
                    {preview.duplicate_count} client{preview.duplicate_count > 1 ? 's partagent' : ' partage'} un
                    numéro déjà dans la liste : un seul message par numéro.
                  </p>
                )}
                {preview.without_phone.length > 0 && (
                  <div className="text-amber-800">
                    <button type="button" className="underline" onClick={() => setShowMissing((v) => !v)}>
                      {preview.without_phone.length} client{preview.without_phone.length > 1 ? 's' : ''} sans
                      numéro dans le nom — non prévenu{preview.without_phone.length > 1 ? 's' : ''}
                    </button>
                    {showMissing && (
                      <ul className="mt-1 max-h-40 overflow-y-auto rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs">
                        {preview.without_phone.map((n, i) => <li key={`${n}-${i}`}>{n}</li>)}
                      </ul>
                    )}
                  </div>
                )}
                {!preview.whatsapp_available && (
                  <p className="text-red-700">
                    WhatsApp n'est pas configuré sur le serveur : aucun message ne peut partir.
                  </p>
                )}
              </div>
            )}
          </div>
        </div>

        {/* 2. Message */}
        <div>
          <p className="text-xs font-bold uppercase tracking-wide text-blue-900 mb-2">Message</p>
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            dir="auto"
            rows={7}
            placeholder="Texte envoyé tel quel — français, arabe, emojis acceptés."
            className="w-full rounded-lg border border-blue-200 px-3 py-2 text-sm text-blue-900
                       focus:outline-none focus:ring-2 focus:ring-blue-300"
          />
          <p className={`text-right text-xs tabular-nums ${message.length > MAX_LENGTH ? 'text-red-600' : 'text-blue-400'}`}>
            {message.length} / {MAX_LENGTH}
          </p>
        </div>

        {busy && (
          <p className="text-sm text-amber-800">
            Un envoi est déjà en cours : attendre sa fin (ou l'arrêter) avant d'en lancer un autre.
          </p>
        )}
        {error && <p className="text-sm text-red-700">{error}</p>}

        <div className="flex justify-end">
          <button
            type="button"
            disabled={!canSend}
            onClick={() => setConfirming(true)}
            className="rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white
                       hover:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Envoyer à {recipients} client{recipients > 1 ? 's' : ''}
          </button>
        </div>
      </div>

      {confirming && preview && (
        <ConfirmDialog
          preview={preview}
          message={message}
          sending={sending}
          onCancel={() => setConfirming(false)}
          onConfirm={send}
        />
      )}
    </section>
  )
}

function ConfirmDialog({
  preview, message, sending, onCancel, onConfirm,
}: {
  preview: BroadcastPreview
  message: string
  sending: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl bg-white shadow-xl">
        <div className="px-5 py-4 border-b border-blue-100">
          <h3 className="text-base font-bold text-blue-900">Confirmer l'envoi</h3>
        </div>
        <div className="px-5 py-4 space-y-3 text-sm text-blue-900">
          <p>
            Vous allez écrire à <strong>{preview.recipient_count} clients</strong> (
            {preview.audiences.map((a) => AUDIENCE_LABEL[a]).join(', ')}) depuis le numéro WhatsApp de
            l'entreprise. Durée estimée : <strong>{formatDuration(preview.estimated_seconds)}</strong>.
          </p>
          <p className="text-blue-400">
            Une fois lancé, l'envoi continue côté serveur même si vous fermez cette page. Il peut être arrêté
            à tout moment ; les messages déjà partis ne se rattrapent pas.
          </p>
          <div dir="auto" className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg border border-blue-100 bg-blue-50 px-3 py-2">
            {message.trim()}
          </div>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-blue-100">
          <button type="button" onClick={onCancel} disabled={sending}
            className="rounded-lg px-4 py-2 text-sm text-blue-700 hover:bg-blue-50">
            Annuler
          </button>
          <button type="button" onClick={onConfirm} disabled={sending}
            className="rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-800 disabled:opacity-50">
            {sending ? 'Lancement…' : `Envoyer à ${preview.recipient_count} clients`}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Un envoi : avancement, résultat, échecs, relance
// ---------------------------------------------------------------------------

function BroadcastPanel({
  id, maySend, live, onChanged,
}: { id: number; maySend: boolean; live: boolean; onChanged: () => void }) {
  const { data, mutate } = useSWR<BroadcastDetail>(endpoints.clientBroadcast(id), fetcher, {
    refreshInterval: live ? 5_000 : 0,
    keepPreviousData: true,
  })
  const [acting, setActing] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  if (!data) {
    return <div className="bg-white rounded-xl border border-blue-100 p-5 text-sm text-blue-400">Chargement…</div>
  }

  const { counts } = data
  const done = counts.sent + counts.failed
  const pct = counts.total > 0 ? Math.round((done / counts.total) * 100) : 0
  const status = STATUS[data.status]

  async function act(fn: () => Promise<BroadcastDetail>) {
    setActing(true)
    setError(null)
    try {
      await mutate(await fn(), { revalidate: false })
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setActing(false)
    }
  }

  return (
    <section className="bg-white rounded-xl border border-blue-100 overflow-hidden">
      <header className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 bg-blue-50 border-b border-blue-100">
        <h2 className="text-sm font-bold text-blue-900">
          Envoi n° {data.id} — {formatTs(data.created_at)}
          {data.created_by && <span className="font-normal text-blue-400"> · par {data.created_by}</span>}
        </h2>
        <span className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${status.cls}`}>{status.label}</span>
      </header>

      <div className="p-5 space-y-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Stat label="Destinataires" value={counts.total} />
          <Stat label="Envoyés" value={counts.sent} tone="green" />
          <Stat label="Échecs" value={counts.failed} tone="red" />
          <Stat label={data.status === 'cancelled' ? 'Non envoyés (arrêt)' : 'En attente'}
            value={data.status === 'cancelled' ? counts.cancelled : counts.pending} />
        </div>

        <div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-blue-50">
            <div className="flex h-full">
              <div className="bg-green-500" style={{ width: `${counts.total ? (counts.sent / counts.total) * 100 : 0}%` }} />
              <div className="bg-red-400" style={{ width: `${counts.total ? (counts.failed / counts.total) * 100 : 0}%` }} />
            </div>
          </div>
          <p className="mt-1 text-xs text-blue-400">
            {pct} % traité
            {data.status === 'running' && data.remaining_seconds > 0 && ` · reste environ ${formatDuration(data.remaining_seconds)}`}
            {data.finished_at && ` · terminé le ${formatTs(data.finished_at)}`}
          </p>
        </div>

        <p className="text-xs text-blue-400">
          Destinataires : {data.audiences.map((a) => AUDIENCE_LABEL[a] ?? a).join(', ')}
        </p>
        <div dir="auto" className="whitespace-pre-wrap rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-900">
          {data.message}
        </div>

        {error && <p className="text-sm text-red-700">{error}</p>}

        {maySend && (
          <div className="flex flex-wrap justify-end gap-2">
            {data.status === 'running' && (
              <button type="button" disabled={acting}
                onClick={() => { if (confirm("Arrêter l'envoi ? Les messages pas encore partis ne partiront pas.")) act(() => cancelBroadcast(data.id)) }}
                className="rounded-lg border border-red-300 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50">
                Arrêter l'envoi
              </button>
            )}
            {data.status !== 'running' && counts.failed > 0 && (
              <button type="button" disabled={acting}
                onClick={() => act(() => retryBroadcastFailures(data.id))}
                className="rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-800 disabled:opacity-50">
                Renvoyer aux {counts.failed} échec{counts.failed > 1 ? 's' : ''}
              </button>
            )}
          </div>
        )}

        {data.failures.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-red-100">
            <table className="w-full text-sm">
              <thead className="bg-red-50 text-red-900">
                <tr>
                  <Th>Client</Th>
                  <Th>Numéro</Th>
                  <Th>Tentatives</Th>
                  <Th>Raison de l'échec</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-red-50">
                {data.failures.map((f) => (
                  <tr key={f.phone}>
                    <Td>{f.name ?? '—'}</Td>
                    <Td className="font-mono">+222 {f.phone}</Td>
                    <Td className="tabular-nums">{f.attempts}</Td>
                    <Td className="text-xs text-red-700">{f.error ?? '—'}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Historique
// ---------------------------------------------------------------------------

function History({
  broadcasts, loading, selectedId, onSelect,
}: {
  broadcasts: Broadcast[]
  loading: boolean
  selectedId: number | null
  onSelect: (id: number) => void
}) {
  return (
    <section className="bg-white rounded-xl border border-blue-100 overflow-hidden">
      <header className="px-5 py-3 bg-blue-50 border-b border-blue-100">
        <h2 className="text-sm font-bold text-blue-900">Envois précédents</h2>
      </header>
      {broadcasts.length === 0 ? (
        <p className="px-5 py-8 text-center text-sm text-blue-400">
          {loading ? 'Chargement…' : 'Aucun message envoyé pour le moment.'}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-blue-900">
              <tr>
                <Th>Date</Th>
                <Th>Par</Th>
                <Th>Destinataires</Th>
                <Th>Message</Th>
                <Th>Envoyés</Th>
                <Th>Échecs</Th>
                <Th>État</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              {broadcasts.map((b) => (
                <tr
                  key={b.id}
                  onClick={() => onSelect(b.id)}
                  className={`cursor-pointer hover:bg-blue-50 ${b.id === selectedId ? 'bg-blue-50' : ''}`}
                >
                  <Td className="whitespace-nowrap">{formatTs(b.created_at)}</Td>
                  <Td>{b.created_by ?? '—'}</Td>
                  <Td>{b.audiences.map((a) => AUDIENCE_LABEL[a] ?? a).join(', ')}</Td>
                  <Td className="max-w-xs truncate" ><span dir="auto">{b.message}</span></Td>
                  <Td className="tabular-nums text-green-700">{b.counts.sent} / {b.counts.total}</Td>
                  <Td className={`tabular-nums ${b.counts.failed > 0 ? 'text-red-700 font-semibold' : 'text-blue-400'}`}>
                    {b.counts.failed}
                  </Td>
                  <Td>
                    <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${STATUS[b.status].cls}`}>
                      {STATUS[b.status].label}
                    </span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: 'green' | 'red' }) {
  const cls = tone === 'green' ? 'text-green-700' : tone === 'red' && value > 0 ? 'text-red-700' : 'text-blue-900'
  return (
    <div className="rounded-xl border border-blue-100 px-4 py-3">
      <p className="text-xs text-blue-400">{label}</p>
      <p className={`mt-0.5 text-2xl font-bold tabular-nums ${cls}`}>{value}</p>
    </div>
  )
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-4 py-2.5 text-left text-xs font-bold uppercase tracking-wide">{children}</th>
}

function Td({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-4 py-2.5 align-top ${className}`}>{children}</td>
}
