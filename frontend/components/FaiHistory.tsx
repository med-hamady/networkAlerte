'use client'

import React from 'react'
import useSWR from 'swr'
import { endpoints } from '@/lib/api'
import type { FaiJournalResponse, FaiRequestsResponse } from '@/lib/types'
import { fetchRequestHistory, isCutCompleted, requestHistoryKey } from '@/lib/faiRouterWait'
import { fetchCompleteHistory, filterHistory } from '@/lib/faiHistoryFilter'
import FaiHistoryCard from '@/components/FaiHistoryCard'
import { buildRequestIndex, matchRequest } from '@/lib/faiRequestLink'

// Rattache un événement du journal à sa demande, sans se tromper de cycle.
// Un même MAC peut avoir plusieurs demandes successives : on exige qu'un des
// horodatages de la demande corresponde EXACTEMENT au timestamp de l'événement.
// L'index clé est `<mac normalisé>|<timestamp>`.
//
// ⚠️ Si DEUX demandes distinctes correspondent au même couple MAC/instant, on
// NE choisit PAS arbitrairement : l'entrée est marquée 'ambiguous' et aucun
// rattachement n'est retourné pour cet événement. Préférable d'afficher moins
// qu'attribuer une confirmation à la mauvaise demande.
const PAGE_SIZE = 200
// Ces filtres portent sur la SITUATION ACTUELLE du service (via
// isCutCompleted(current)), pas sur l'achèvement d'une demande précise.
// D'où un vocabulaire d'ACCÈS plutôt que de « Terminé / Non terminé » : le
// badge de la 3e zone, lui, décrit la DEMANDE affichée et peut légitimement
// différer (demande non terminée mais accès bloqué par ailleurs, etc.).
const situationFilters = {
  completed: 'Accès bloqué sur l’équipement',
  incomplete: 'Accès non bloqué (ou blocage routeur seul)',
}

export default function FaiHistory() {
  const [source, setSource] = React.useState('')
  const [situation, setSituation] = React.useState('')
  const [search, setSearch] = React.useState('')
  const [query, setQuery] = React.useState('')
  const [start, setStart] = React.useState('')
  const [end, setEnd] = React.useState('')
  const [range, setRange] = React.useState<{ start: string; end: string } | null>(null)
  const filters = JSON.stringify([source, situation, query, range])
  const [position, setPosition] = React.useState({ filters, offset: 0 })
  if (position.filters !== filters) setPosition({ filters, offset: 0 })
  const offset = position.filters === filters ? position.offset : 0
  React.useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 250)
    return () => clearTimeout(timer)
  }, [search])
  const { data, error, mutate } = useSWR<FaiJournalResponse>(endpoints.faiJournal({
    source, search: query, start: range?.start, end: range?.end,
    offset: 0, limit: 1000,
  }), fetchCompleteHistory, { refreshInterval: 30_000 })
  // Historique des demandes (shared SWR key, idem FaiRouterWaitDetails) : sert
  // à afficher le parcours complet dans le résumé de chaque fiche. Une erreur
  // ici ne doit pas casser la liste — le résumé retombe alors sur l'événement
  // seul.
  const { data: requestHistory } = useSWR<FaiRequestsResponse>(requestHistoryKey, fetchRequestHistory,
    { refreshInterval: 60_000, revalidateOnFocus: false })
  // The company connection may expose the database without its file journal.
  // An empty response in that case cannot establish zero historical events.
  const journalUnavailable = data?.entries.length === 0 && (
    requestHistory?.history_available === false || requestHistory?.capabilities?.legacy_journal === false
  )
  const requestIndex = React.useMemo(
    () => buildRequestIndex(requestHistory?.entries ?? []),
    [requestHistory],
  )
  const filtered = React.useMemo(() => filterHistory(data?.entries ?? [], situation), [data, situation])
  const total = filtered.length
  const completed = filtered.filter(entry => isCutCompleted(entry.current)).length
  const incomplete = filterHistory(filtered, 'incomplete').length
  const entries = filtered.slice(offset, offset + PAGE_SIZE)
  const last = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1) * PAGE_SIZE
  React.useEffect(() => {
    if (data && offset > last) setPosition({ filters, offset: last })
  }, [data, offset, last, filters])
  const navigation = <nav aria-label="Pagination de l'historique" className="flex flex-wrap gap-2 items-center text-sm">
    <p className="mr-auto text-slate-600" aria-live="polite">
      {error ? 'Chargement impossible.' : !data ? 'Chargement…' : journalUnavailable ? 'Journal indisponible.' : total
        ? `Événements ${offset + 1} à ${offset + entries.length} sur ${total} — page ${Math.floor(offset / PAGE_SIZE) + 1} sur ${Math.ceil(total / PAGE_SIZE)}`
        : 'Aucun événement'}
    </p>
    {[
      ['Première', 0, offset === 0], ['Précédente', Math.max(0, offset - PAGE_SIZE), offset === 0],
      ['Suivante', offset + PAGE_SIZE, offset >= last], ['Dernière', last, offset >= last],
    ].map(([label, target, disabled]) => <button key={String(label)} disabled={!data || Boolean(disabled)}
      onClick={() => { setPosition({ filters, offset: Number(target) }); window.scrollTo({ top: 0 }) }}
      className="border border-blue-200 rounded-lg px-3 py-2 text-blue-900 disabled:opacity-40">{label}</button>)}
  </nav>
  return <section className="space-y-5" aria-label="Toutes les demandes et actions enregistrées">
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
      {[
        ['Événements correspondant aux filtres', data && !journalUnavailable ? total : undefined],
        ['Avec accès bloqué sur l’équipement', data && !journalUnavailable ? completed : undefined],
        ['Avec accès non bloqué (ou blocage routeur seul)', data && !journalUnavailable ? incomplete : undefined],
      ].map(([label, value]) => <div key={label} className="bg-white border border-blue-100 rounded-xl px-4 py-3">
        <p className="text-xs text-slate-600">{label}</p>
        <p className="mt-1 text-2xl font-bold text-blue-900 tabular-nums">{value ?? '—'}</p>
      </div>)}
    </div>
    {journalUnavailable && <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
      Le journal historique n’est pas disponible pour cette connexion. Ses événements sont conservés dans un fichier distinct de la base de données ; les compteurs ne peuvent pas être établis ici.
    </p>}
    <p className="text-xs text-slate-500">Chaque fiche décrit un événement daté et l’état du client aujourd’hui. Les compteurs portent sur les événements filtrés ; un client peut apparaître plusieurs fois.</p>
    <div className="flex flex-wrap gap-3 items-center">
      <input aria-label="Rechercher dans tout l'historique" value={search} onChange={e => setSearch(e.target.value)}
        placeholder="Nom, MAC ou agent…" className="border rounded-lg p-2 text-sm" />
      <select aria-label="Origine" value={source} onChange={e => setSource(e.target.value)} className="border rounded-lg p-2 text-sm">
        <option value="">Toutes les origines</option>
        <option value="Block_all.php">Block_all.php (campagne impayés)</option><option value="payment">Système de paiement (non attribué)</option>
        <option value="enforce">Renforcement automatique</option><option value="script">Blocage de masse</option>
      </select>
      <label className="text-xs text-slate-600">Situation du service{' '}
      <select aria-label="Situation actuelle du service" value={situation} onChange={e => setSituation(e.target.value)} className="border rounded-lg p-2 text-sm">
        <option value="">Tout</option>
        {Object.entries(situationFilters).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <label className="text-xs">Du (UTC) <input type="date" aria-label="Début" value={start} onChange={e => setStart(e.target.value)} className="border rounded p-2" /></label>
      <label className="text-xs">Au (UTC) <input type="date" aria-label="Fin" value={end} onChange={e => setEnd(e.target.value)} className="border rounded p-2" /></label>
      <button disabled={!start || !end || end < start} onClick={() => setRange({ start, end })} className="bg-blue-900 text-white rounded-lg p-2 text-sm disabled:opacity-40">Appliquer</button>
      {range && <button onClick={() => setRange(null)} className="text-blue-700 underline text-sm">Tout l'historique</button>}
    </div>
    {error && <p role="alert" className="text-red-700">L'historique n'a pas pu être chargé. <button onClick={() => mutate()} className="underline">Réessayer</button></p>}
    {navigation}
    <div className="space-y-4">
      {entries.map((entry, index) => <FaiHistoryCard
        key={`${offset + index}-${entry.timestamp}-${entry.mac}-${entry.action}`}
        entry={entry} linkedRequest={matchRequest(entry, requestIndex)} />)}
      {data && !journalUnavailable && !entries.length && <p className="py-8 text-center text-slate-600">Aucun événement ne correspond à ces filtres.</p>}
    </div>
    {navigation}
  </section>
}

