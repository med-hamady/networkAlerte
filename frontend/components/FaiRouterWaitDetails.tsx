'use client'

import React from 'react'
import useSWR from 'swr'
import type { FaiRequestEntry, FaiRequestsResponse } from '@/lib/types'
import { duration } from '@/lib/faiRequestProgress'
import { fetchRequestHistory, requestHistoryKey, routerWaitSeconds, sameClientMac } from '@/lib/faiRouterWait'

const date = (value: string) => Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'medium' }) : 'Date non disponible'
const PAGE_SIZE = 20

export default function FaiRouterWaitDetails({ mac, exampleRequest }: { mac: string; exampleRequest?: FaiRequestEntry }) {
  const [open, setOpen] = React.useState(Boolean(exampleRequest))
  const [offset, setOffset] = React.useState(0)
  const { data: history, error, isLoading, mutate } = useSWR<FaiRequestsResponse>(open && !exampleRequest ? requestHistoryKey : null,
    fetchRequestHistory, { refreshInterval: open && !exampleRequest ? 30_000 : 0 })
  const data: FaiRequestsResponse | undefined = exampleRequest
    ? { entries: [exampleRequest], counts: null, matching: 1, limit: 1, offset: 0 } : history
  const clientRequests = data?.entries.filter(entry => sameClientMac(entry.mac, mac) && entry.mode === 'full') ?? []
  const tracked = clientRequests.filter(entry => !entry.legacy)
  const legacyCount = clientRequests.length - tracked.length
  const last = Math.max(0, Math.ceil(tracked.length / PAGE_SIZE) - 1) * PAGE_SIZE
  const pageOffset = Math.min(offset, last)
  const requests = tracked.slice(pageOffset, pageOffset + PAGE_SIZE)
  return <details className="mt-3 text-xs" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer font-semibold text-blue-700">Temps d’attente sur le routeur</summary>
    <p className="mt-2 text-slate-600">Du blocage routeur confirmé à la coupure confirmée sur l’équipement. Le retour de l’équipement seul n’arrête pas cette durée.</p>
    <p className="mt-1 text-slate-500">{exampleRequest
      ? 'Dates fictives de la même demande, utilisées uniquement pour cet exemple.'
      : 'Ces demandes sont identifiées séparément ; le journal ne permet pas de les rattacher à cet événement.'}</p>
    {isLoading && <p role="status" className="mt-2">Chargement des durées…</p>}
    {error && <p role="alert" className="mt-2 text-red-700">Durées indisponibles. <button className="underline" onClick={() => mutate()}>Réessayer</button></p>}
    {data && !error && <>
      {data.capabilities?.request_history === false && <p className="mt-2 text-amber-900">Le suivi détaillé des demandes n’est pas disponible dans cette source.</p>}
      {data.history_available === false && <p className="mt-2 text-amber-900">Aucun historique exploitable n’est disponible dans cette source.</p>}
      {!tracked.length && <p className="mt-2">{legacyCount
        ? 'Seul l’ancien journal est disponible pour ce client. Il ne contient pas les deux confirmations liées à une même demande nécessaires au calcul de la durée.'
        : 'Aucune demande avec suivi détaillé trouvée pour ce client dans l’historique disponible. Les dates de confirmation du blocage routeur et de la coupure sur l’équipement sont nécessaires au calcul.'}</p>}
      {legacyCount > 0 && <p className="mt-2 text-slate-500">{legacyCount} événement(s) ancien(s) sans durée vérifiable.</p>}
      <ul className="space-y-3 mt-3">{requests.map(entry => {
        const seconds = routerWaitSeconds(entry)
        const router = entry.actions.find(a => a.key === 'router' && a.result === 'confirmed')
        return <li key={entry.id} className="rounded-lg border border-blue-100 p-3 bg-blue-50/40">
          <p className="font-semibold break-all">Demande {entry.id}</p>
          {entry.received_at && <p className="mt-1">Reçue le : {date(entry.received_at)}</p>}
          {seconds !== null ? <>
            <p className="mt-2 font-bold text-blue-900">Temps passé sur le routeur : {duration(seconds)}</p>
            <p className="mt-1">Blocage routeur : {date(router!.confirmed_at!)}</p>
            <p className="mt-1">Coupure sur l’équipement : {date(entry.cut_at!)}</p>
          </> : <p className="mt-2 text-slate-600">{!entry.cut_at || !entry.actions.some(a => a.key === 'client' && a.result === 'confirmed')
            ? 'La coupure sur l’équipement n’est pas encore confirmée pour cette demande ; la durée finale n’est pas disponible.'
            : entry.waiting?.known && !entry.waiting.periods.length && !entry.actions.some(a => a.key === 'router')
            ? 'Aucune attente sur le routeur enregistrée pour cette demande.'
            : 'Durée non disponible : confirmations absentes ou dates incohérentes.'}</p>}
        </li>
      })}</ul>
      {tracked.length > PAGE_SIZE && <div className="flex gap-3 mt-3">
        <button disabled={pageOffset === 0} onClick={() => setOffset(Math.max(0, pageOffset - PAGE_SIZE))} className="underline disabled:opacity-40">Précédentes</button>
        <button disabled={pageOffset >= last} onClick={() => setOffset(pageOffset + PAGE_SIZE)} className="underline disabled:opacity-40">Suivantes</button>
      </div>}
    </>}
  </details>
}
