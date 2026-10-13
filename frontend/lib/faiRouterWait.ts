import type { FaiEntryCurrent, FaiRequestEntry, FaiRequestsResponse } from './types'
import { endpoints, fetcher } from './api'

export function isCutCompleted(current: FaiEntryCurrent): boolean {
  return current.known && current.client_blocked && current.enforced_by === 'lr'
}

export function currentCutLabel(current: FaiEntryCurrent): string {
  if (isCutCompleted(current)) return 'Terminé'
  if (!current.known || !current.client_blocked) return 'Non terminé'
  if (current.enforced_by === 'router') return 'Non terminé (blocage routeur)'
  return 'Non terminé'
}

export function currentCutResult(current: FaiEntryCurrent): { done: boolean; note: string } {
  if (isCutCompleted(current)) return { done: true, note: 'État actuel : coupure confirmée sur l’équipement.' }
  if (!current.known) return { done: false, note: 'État actuel inconnu : fiche absente de la base.' }
  if (!current.client_blocked) return { done: false, note: 'État actuel : non coupé, aucun ordre de coupure actif.' }
  if (current.enforced_by === 'router') return { done: false, note: 'État actuel : blocage routeur uniquement ; coupure sur l’équipement non confirmée.' }
  return { done: false, note: 'État actuel : ordre de coupure actif, application sur l’équipement non confirmée.' }
}

// Each bound belongs to the same structured request. A return observation,
// journal timestamp or waiting.total_seconds must never replace either bound.
export function routerWaitSeconds(entry: FaiRequestEntry): number | null {
  // CPE confirmation is the requested endpoint, not completion of unrelated
  // remaining actions. Do not hide an already proven interval by status alone.
  if (entry.legacy || entry.mode !== 'full') return null
  const router = entry.actions.find(a => a.key === 'router' && a.result === 'confirmed')
  const client = entry.actions.find(a => a.key === 'client' && a.result === 'confirmed')
  if (!router?.confirmed_at || !client?.confirmed_at || !entry.cut_at) return null
  const start = Date.parse(router.confirmed_at)
  const end = Date.parse(client.confirmed_at)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start || Date.parse(entry.cut_at) !== end) return null
  return Math.floor((end - start) / 1000)
}

export const sameClientMac = (left: string | null, right: string | null) => {
  const normalize = (value: string | null) => (value ?? '').trim().replace(/[:.\-]/g, '').toLowerCase()
  const mac = normalize(left)
  return /^[0-9a-f]{12}$/.test(mac) && mac === normalize(right)
}

export const requestHistoryKey = endpoints.faiRequests({ limit: 1000, offset: 0 })

// One shared SWR request for all open cards. Searching raw MACs server-side
// misses separator variants; filtering before pagination misses later matches.
export async function fetchRequestHistory(url: string): Promise<FaiRequestsResponse> {
  const first: FaiRequestsResponse = await fetcher(url)
  const entries = [...first.entries]
  if (!Number.isInteger(first.matching) || first.matching < 0) throw new Error('Historique incomplet')
  while (entries.length < first.matching) {
    const next: FaiRequestsResponse = await fetcher(endpoints.faiRequests({ limit: 1000, offset: entries.length }))
    if (next.matching !== first.matching || !next.entries.length) throw new Error('Historique modifié ; réessayez')
    entries.push(...next.entries)
  }
  if (entries.length !== first.matching || new Set(entries.map(e => e.id)).size !== entries.length)
    throw new Error('Historique incomplet ; réessayez')
  return { ...first, entries }
}
