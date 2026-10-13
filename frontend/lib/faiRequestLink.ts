import type { FaiJournalEntry, FaiRequestEntry } from './types'
import { sameClientMac } from './faiRouterWait'

type IndexedRequest = FaiRequestEntry | 'ambiguous'
type RequestIndex = { ids: Map<string, IndexedRequest>; timestamps: Map<string, IndexedRequest> }

const macKey = (mac: string | null) => (mac ?? '').trim().replace(/[:.\-]/g, '').toLowerCase()

function add(index: Map<string, IndexedRequest>, key: string, request: FaiRequestEntry) {
  const existing = index.get(key)
  index.set(key, existing && (existing === 'ambiguous' || existing.id !== request.id)
    ? 'ambiguous' : request)
}

export function buildRequestIndex(requests: FaiRequestEntry[]): RequestIndex {
  const ids = new Map<string, IndexedRequest>()
  const timestamps = new Map<string, IndexedRequest>()
  for (const request of requests) {
    if (request.legacy) continue
    const mac = macKey(request.mac)
    if (!/^[0-9a-f]{12}$/.test(mac)) continue
    add(ids, request.id, request)
    // Old entries lack a request marker. Keep their fallback exact and reject
    // collisions; rounding to journal seconds could join distinct requests.
    const stamps = [request.received_at, request.cut_at,
      ...request.actions.flatMap(action => [action.confirmed_at, action.attempted_at])]
    for (const timestamp of new Set(stamps)) {
      if (timestamp) add(timestamps, `${mac}|${timestamp}`, request)
    }
  }
  return { ids, timestamps }
}

export function matchRequest(entry: FaiJournalEntry, index: RequestIndex): FaiRequestEntry | null {
  const match = entry.request_id
    ? index.ids.get(entry.request_id)
    : index.timestamps.get(`${macKey(entry.mac)}|${entry.timestamp}`)
  // An explicit marker must never fall back to another cycle when absent.
  return match && match !== 'ambiguous' && sameClientMac(entry.mac, match.mac) ? match : null
}

export function requestMatchesEvent(entry: FaiJournalEntry, request: FaiRequestEntry | null): boolean {
  return Boolean(request && matchRequest(entry, buildRequestIndex([request])))
}
