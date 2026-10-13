import { fetcher } from './api'
import type { FaiJournalEntry, FaiJournalResponse } from './types'
import { isCutCompleted } from './faiRouterWait'

export function filterHistory(entries: FaiJournalEntry[], status: string) {
  return entries.filter(entry => !status || (status === 'completed'
    ? isCutCompleted(entry.current) : !isCutCompleted(entry.current)))
}

// The existing API groups router and CPE enforcement under "cut". Fetch all
// matching events before applying the two UI statuses and local pagination.
export async function fetchCompleteHistory(url: string): Promise<FaiJournalResponse> {
  const first: FaiJournalResponse = await fetcher(url)
  const entries = [...first.entries]
  const total = first.matching
  if (total === undefined) throw new Error('Nombre total des événements indisponible')
  while (entries.length < total) {
    const nextUrl = new URL(url, 'http://frontend.local')
    nextUrl.searchParams.set('offset', String(entries.length))
    const next: FaiJournalResponse = await fetcher(nextUrl.pathname + nextUrl.search)
    if (next.matching !== total || next.entries.length === 0) {
      throw new Error('Historique modifié pendant le chargement ; réessayez')
    }
    entries.push(...next.entries)
  }
  if (entries.length !== total) throw new Error('Historique incomplet')
  return { ...first, entries }
}
