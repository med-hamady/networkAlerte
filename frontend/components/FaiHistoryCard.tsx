'use client'

import React from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { FaiEvidence, FaiJournalEntry, FaiRequestEntry } from '@/lib/types'
import { readableMessage, sourceLabel, userLabel } from '@/lib/faiActions'
import { currentCutLabel, isCutCompleted } from '@/lib/faiRouterWait'
import { currentExplanation, eventExplanation, eventTitles, explainDifference } from '@/lib/faiHistoryPresentation'
import FaiRouterWaitDetails from '@/components/FaiRouterWaitDetails'
import FaiOperationSummary from '@/components/FaiOperationSummary'
import { requestMatchesEvent } from '@/lib/faiRequestLink'

const date = (value: string) => Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'medium' }) : 'Date non disponible'

export default function FaiHistoryCard({ entry: e, exampleRequest, linkedRequest }:
  { entry: FaiJournalEntry; exampleRequest?: FaiRequestEntry; linkedRequest?: FaiRequestEntry | null }) {
  const [proof, setProof] = React.useState(false)
  // L'exemple pédagogique s'ouvre déjà déplié pour montrer les deux colonnes ;
  // en usage réel, le détail est caché derrière « Voir plus ».
  const [expanded, setExpanded] = React.useState(Boolean(exampleRequest))
  const { data, error, isLoading, mutate } = useSWR<FaiEvidence>(proof ? endpoints.faiJournalEvidence(e.timestamp, e.action, e.mac) : null, fetcher)
  const current = currentExplanation(e)
  const difference = explainDifference(e)
  const active = e.current.known && e.current.client_blocked
  const completed = isCutCompleted(e.current)
  const message = readableMessage(e.message).text.replace(/\bLR\b/g, 'équipement du client').replace(/SSH/g, 'de gestion')
  const candidate = exampleRequest ?? linkedRequest ?? null
  const request = requestMatchesEvent(e, candidate) ? candidate : null
  const eventCutConfirmed = e.mode === 'full' && e.ok && request?.cut_at === e.timestamp && request.actions.some(
    action => action.key === 'client' && action.result === 'confirmed' && action.confirmed_at === e.timestamp,
  )
  const title = eventCutConfirmed ? 'Coupure confirmée sur l’équipement' : eventTitles[e.action] || 'Événement enregistré'
  const detailsId = `fai-history-details-${e.timestamp}-${e.action}-${e.mac ?? 'nomac'}`

  return <article className="bg-white border border-blue-100 rounded-xl overflow-hidden" aria-label={`${e.name} — ${title}`}>
    {/* Bande des 3 zones + bouton « Voir plus », tout dans la même carte */}
    <div className="p-4 sm:p-5 space-y-4">
      <FaiOperationSummary entry={e} request={request} />
      <div className="flex items-center justify-between gap-3 pt-1 border-t border-slate-100">
        <p className="text-[11px] text-slate-500 truncate">
          <span className="font-semibold text-slate-700">{e.name || 'Client non renseigné'}</span>
          {e.current.site ? ` · ${e.current.site}` : ''}{e.mac ? ` · ${e.mac}` : ''}
        </p>
        <button type="button" aria-expanded={expanded} aria-controls={detailsId}
          onClick={() => setExpanded(!expanded)}
          className="shrink-0 rounded-lg border border-blue-200 bg-white px-3 py-1.5 text-xs font-semibold text-blue-800 hover:bg-blue-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-700">
          {expanded ? 'Voir moins' : 'Voir plus'}
        </button>
      </div>
    </div>

    {/* Détails (anciennes 2 colonnes) — repliés par défaut */}
    {expanded && <div id={detailsId}>
      <div className="grid lg:grid-cols-2 border-t border-blue-100">
        <section className="p-5 min-w-0" aria-label="Historique de cette tentative">
          <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wide">À cette date · Ce qui s’est passé</h3>
          <p className="mt-2 text-xs text-slate-500">Événement du <time dateTime={e.timestamp}>{date(e.timestamp)}</time></p>
          <p className="mt-3 text-base font-semibold text-slate-800">{title}</p>
          <p className={`mt-2 text-xs font-semibold ${e.ok ? 'text-green-800' : 'text-amber-900'}`}>
            {e.action === 'ABANDON' ? 'Tentative arrêtée' : e.ok ? 'Réussite indiquée dans cet événement' : 'Échec indiqué dans cet événement'}
          </p>
          <p className="mt-3 text-sm leading-relaxed text-slate-700">{eventCutConfirmed
            ? 'La coupure sur l’équipement du client est confirmée à cette date.' : eventExplanation(e)}</p>
          {!e.ok && message && <p className="mt-3 text-sm text-amber-900 break-words"><span className="font-semibold">Motif à cette date : </span>{message}</p>}
          <dl className="text-xs text-slate-600 mt-4 space-y-2">
            <div><dt className="inline font-semibold">Agent de cet événement : </dt><dd className="inline break-words">{userLabel(e.user) || 'Non renseigné'}</dd></div>
            <div><dt className="inline font-semibold">Origine : </dt><dd className="inline break-words">{sourceLabel(e.source)}</dd></div>
            <div><dt className="inline font-semibold">Accès demandé à cette date : </dt><dd className="inline">{e.mode === 'full' ? 'Coupure totale' : e.mode === 'whatsapp_only' ? 'WhatsApp uniquement' : e.mode}</dd></div>
          </dl>
        </section>
        <section className="p-5 min-w-0 bg-slate-50 border-t lg:border-t-0 lg:border-l border-blue-100" aria-label="État actuel du client">
          <h3 className="text-xs font-bold text-blue-900 uppercase tracking-wide">État actuel de la coupure</h3>
            <p className={`inline-block mt-3 rounded-lg px-3 py-1 text-sm font-semibold ${completed ? 'bg-green-50 text-green-800' : 'bg-amber-50 text-amber-900'}`}>{currentCutLabel(e.current)}</p>
          <p className="mt-3 text-sm leading-relaxed text-slate-700">{current.text}</p>
          {active && e.current.blocked_reason && <p className="mt-3 text-xs text-slate-600 break-words">Motif de l’ordre actuel : {e.current.blocked_reason}</p>}
          {active && !completed && e.current.unenforceable_reason && <p className="mt-2 text-xs text-amber-900">L’accès de gestion à l’équipement nécessite une intervention.</p>}
          {active && e.current.enforced_by === 'router' && <p className="mt-3 text-xs text-slate-600">La durée d’attente sera affichée après la coupure confirmée sur l’équipement, si les deux confirmations sont enregistrées.</p>}
          {completed && e.mac && <FaiRouterWaitDetails key={e.mac} mac={e.mac} exampleRequest={exampleRequest} />}
        </section>
      </div>
      {difference && <p className="mx-5 mb-5 rounded-lg border border-blue-100 bg-blue-50 p-3 text-sm text-blue-900"><strong>Comment lire cette fiche : </strong>{difference}</p>}
      <details className="mx-5 mb-5 border-t border-blue-100 pt-3 text-xs text-slate-600">
        <summary className="cursor-pointer font-semibold text-blue-700">Détails de l’événement et preuve archivée</summary>
        <p className="mt-3">Date de l’événement : {date(e.timestamp)}. La date de réception du besoin et la date de coupure confirmée ne sont pas déduites de cet instant.</p>
        <p className="mt-3">Action d’origine : {e.action}</p>
        <p className="mt-2 whitespace-pre-wrap break-words">{e.message || 'Aucun message complémentaire.'}</p>
        {e.has_evidence ? <button onClick={() => setProof(!proof)} className="mt-3 underline text-blue-700">{proof ? 'Masquer la preuve' : 'Voir la preuve archivée'}</button>
          : <p className="mt-3">Aucune preuve d’exécution archivée pour cet événement.</p>}
        {proof && isLoading && <p role="status" className="mt-2">Chargement de la preuve…</p>}
        {proof && error && <p role="alert" className="mt-2 text-red-700">Preuve indisponible. <button className="underline" onClick={() => mutate()}>Réessayer</button></p>}
        {proof && data && <pre className="mt-3 p-3 bg-slate-50 whitespace-pre-wrap break-all max-h-96 overflow-auto">{data.transcript}</pre>}
      </details>
    </div>}
  </article>
}
