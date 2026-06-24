'use client'

import { useEffect, useRef, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import { deviceTypeLabel, type DeviceSearchResult } from '@/lib/types'

interface Props {
  // Appelé quand un résultat est choisi → la page ouvre la fiche dans son site.
  onSelect: (deviceId: number, site: string | null) => void
}

const SITE_FALLBACK = 'Sans site'

export default function DeviceSearchBar({ onSelect }: Props) {
  const [query, setQuery]       = useState('')
  const [debounced, setDebounced] = useState('')
  const [open, setOpen]         = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  // Anti-rebond : on n'interroge l'API qu'après 250 ms sans frappe.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 250)
    return () => clearTimeout(t)
  }, [query])

  // Recherche serveur uniquement à partir de 2 caractères (cf. min_length API).
  const { data, isLoading } = useSWR<DeviceSearchResult[]>(
    debounced.length >= 2 ? endpoints.devicesSearch(debounced) : null,
    fetcher,
  )
  const results = data ?? []

  // Fermer le menu sur clic extérieur.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const choose = (r: DeviceSearchResult) => {
    setOpen(false)
    setQuery('')
    setDebounced('')
    onSelect(r.id, r.site)
  }

  const showDropdown = open && debounced.length >= 2

  return (
    <div ref={boxRef} className="relative w-full max-w-md">
      <div className="relative">
        <svg
          className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-blue-300 pointer-events-none"
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z" />
        </svg>
        <input
          type="text"
          value={query}
          onChange={e => { setQuery(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          onKeyDown={e => { if (e.key === 'Escape') setOpen(false) }}
          placeholder="Rechercher par nom, IP, ou téléphone d'un client…"
          className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-blue-200 bg-white shadow-sm
                     focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-blue-300 placeholder:text-blue-300"
        />
      </div>

      {showDropdown && (
        <div className="absolute z-20 mt-1 w-full bg-white border border-blue-100 rounded-lg shadow-lg max-h-80 overflow-auto">
          {isLoading ? (
            <p className="px-4 py-3 text-sm text-blue-300">Recherche…</p>
          ) : results.length === 0 ? (
            <p className="px-4 py-3 text-sm text-blue-300">Aucun résultat</p>
          ) : (
            <ul className="divide-y divide-blue-50">
              {results.map(r => (
                <li key={r.id}>
                  <button
                    onClick={() => choose(r)}
                    className="w-full text-left px-4 py-2.5 hover:bg-blue-50 transition-colors flex items-center gap-3"
                  >
                    <StatusDot status={r.status} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-slate-800 truncate">{r.name}</span>
                      <span className="block text-xs text-blue-400 truncate">
                        {deviceTypeLabel(r.device_type)}
                        {r.ip_address && <span className="font-mono"> · {r.ip_address}</span>}
                        {' · '}{r.site?.trim() || SITE_FALLBACK}
                      </span>
                    </span>
                    <span className="text-blue-300 text-sm shrink-0">→</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function StatusDot({ status }: { status: string }) {
  const color = status === 'up' ? 'bg-green-500' : status === 'down' ? 'bg-red-500' : 'bg-blue-200'
  return <span className={`inline-flex h-2.5 w-2.5 rounded-full shrink-0 ${color}`} title={status} />
}
