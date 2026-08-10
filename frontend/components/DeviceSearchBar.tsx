'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import { deviceTypeLabel, type DeviceSearchResult } from '@/lib/types'

interface Props {
  // Appelé quand un résultat est choisi → l'appelant décide de la destination
  // (ouvrir la fiche en place sur /sites, ou naviguer depuis la barre globale).
  onSelect: (deviceId: number, site: string | null) => void
  placeholder?: string
  // Largeur du champ. Défaut = la mise en page d'origine (colonne /sites).
  className?: string
  // Ctrl/⌘+K met le focus dans le champ. Réservé à l'instance GLOBALE : deux
  // champs réclamant le même raccourci se voleraient le focus.
  shortcut?: boolean
}

const SITE_FALLBACK = 'Sans site'

export default function DeviceSearchBar({
  onSelect,
  placeholder = "Rechercher par nom, IP, ou téléphone d'un client…",
  className = 'w-full max-w-md',
  shortcut = false,
}: Props) {
  const [query, setQuery]       = useState('')
  const [debounced, setDebounced] = useState('')
  const [open, setOpen]         = useState(false)
  // Résultat surligné au clavier. -1 = aucun (la frappe ne présélectionne rien :
  // un Entrée réflexe ne doit pas ouvrir la fiche d'un homonyme).
  const [active, setActive]     = useState(-1)
  const boxRef   = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Anti-rebond : on n'interroge l'API qu'après 250 ms sans frappe.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 250)
    return () => clearTimeout(t)
  }, [query])

  // Recherche serveur uniquement à partir de 2 caractères (cf. min_length API).
  const { data, isLoading } = useSWR<DeviceSearchResult[]>(
    debounced.length >= 2 ? endpoints.devicesSearch(debounced) : null,
    fetcher,
    { keepPreviousData: true },
  )
  const results = useMemo(() => data ?? [], [data])

  // La liste a changé sous le curseur → on retire le surlignage plutôt que de
  // le laisser désigner une ligne qui n'est plus la même.
  useEffect(() => { setActive(-1) }, [results])

  // Fermer le menu sur clic extérieur.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  // Ctrl/⌘+K depuis n'importe où dans l'application.
  useEffect(() => {
    if (!shortcut) return
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
        inputRef.current?.select()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [shortcut])

  const choose = (r: DeviceSearchResult) => {
    setOpen(false)
    setActive(-1)
    setQuery('')
    setDebounced('')
    inputRef.current?.blur()
    onSelect(r.id, r.site)
  }

  const showDropdown = open && debounced.length >= 2

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') { setOpen(false); setActive(-1); return }
    if (!showDropdown || results.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive(i => (i + 1) % results.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive(i => (i <= 0 ? results.length - 1 : i - 1))
    } else if (e.key === 'Enter' && active >= 0) {
      e.preventDefault()
      choose(results[active])
    }
  }

  return (
    <div ref={boxRef} className={`relative ${className}`}>
      <div className="relative">
        <svg
          className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-blue-300 pointer-events-none"
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z" />
        </svg>
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={e => { setQuery(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          aria-label="Rechercher un équipement ou un client"
          className={`w-full pl-9 py-2 text-sm rounded-lg border border-blue-200 bg-white shadow-sm
                      focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-blue-300
                      placeholder:text-blue-300 ${shortcut ? 'pr-16' : 'pr-3'}`}
        />
        {shortcut && (
          <kbd
            className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none select-none
                       rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5
                       text-[10px] font-sans font-medium text-blue-300"
          >
            Ctrl K
          </kbd>
        )}
      </div>

      {showDropdown && (
        <div className="absolute z-30 mt-1 w-full bg-white border border-blue-100 rounded-lg shadow-lg max-h-80 overflow-auto">
          {results.length === 0 ? (
            <p className="px-4 py-3 text-sm text-blue-300">
              {isLoading ? 'Recherche…' : 'Aucun résultat'}
            </p>
          ) : (
            <ul className="divide-y divide-blue-50">
              {results.map((r, i) => (
                <li key={r.id}>
                  <button
                    onClick={() => choose(r)}
                    onMouseEnter={() => setActive(i)}
                    className={`w-full text-left px-4 py-2.5 transition-colors flex items-center gap-3 ${
                      i === active ? 'bg-blue-50' : 'hover:bg-blue-50'
                    }`}
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
