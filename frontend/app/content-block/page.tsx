'use client'

import React from 'react'
import useSWR from 'swr'
import {
  endpoints,
  fetcher,
  setContentBlock,
  type ContentBlockCategory,
} from '@/lib/api'
import type { Device, Lr } from '@/lib/types'
import IpLink from '@/components/IpLink'

interface SearchResult {
  id: number
  name: string
  ip_address: string | null
  device_type: string
  site: string | null
  status: string
}

export default function ContentBlockPage() {
  // Catalogue of blockable services (Facebook, TikTok, …) from the backend.
  const { data: categories } = useSWR<ContentBlockCategory[]>(
    endpoints.contentBlockCategories,
    fetcher,
  )

  // Search-by-client (LR name carries the client phone number).
  const [search, setSearch] = React.useState('')
  const [debounced, setDebounced] = React.useState('')
  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), 250)
    return () => clearTimeout(t)
  }, [search])

  const { data: results, isLoading: searching } = useSWR<SearchResult[]>(
    debounced.length >= 2 ? endpoints.devicesSearch(debounced) : null,
    fetcher,
    { keepPreviousData: true },
  )
  const lrResults = (results ?? []).filter((r) => r.device_type === 'lr')

  // The selected client's full record (topology_mode, blocked_categories, SSH).
  const [selectedId, setSelectedId] = React.useState<number | null>(null)
  const { data: device, mutate: mutateDevice } = useSWR<Device>(
    selectedId != null ? endpoints.device(selectedId) : null,
    fetcher,
  )
  const lr = device && device.device_type === 'lr' ? (device as Lr) : null

  // Local checkbox state, seeded from the device each time one is loaded.
  // Keyed on the serialised value, NOT the array itself: SWR hands back a new
  // array reference on every revalidation, which would re-run this effect and
  // wipe the operator's in-progress ticks mid-edit.
  const [selected, setSelected] = React.useState<Set<string>>(new Set())
  const blockedKey = (lr?.blocked_categories ?? []).join(',')
  React.useEffect(() => {
    setSelected(new Set(blockedKey ? blockedKey.split(',') : []))
  }, [lr?.id, blockedKey])

  const [applying, setApplying] = React.useState(false)
  const [result, setResult] = React.useState<{ ok: boolean; message: string } | null>(null)

  const isBridge = lr?.topology_mode === 'bridge'
  const noSsh = lr ? !lr.has_ssh_password : false
  const canApply = !!lr && !isBridge && !noSsh && !applying

  const initial = new Set(lr?.blocked_categories ?? [])
  const dirty =
    selected.size !== initial.size ||
    [...selected].some((c) => !initial.has(c))

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
    setResult(null)
  }

  const onApply = async () => {
    if (!lr) return
    setApplying(true)
    setResult(null)
    try {
      const r = await setContentBlock(lr.id, [...selected])
      setResult({ ok: r.ok, message: r.message })
      await mutateDevice()
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : 'Erreur inconnue' })
    } finally {
      setApplying(false)
    }
  }

  const pickClient = (id: number) => {
    setSelectedId(id)
    setResult(null)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Filtre de contenu</h1>
        <p className="text-blue-400 text-sm mt-1 max-w-3xl">
          Bloque l'accès d'un client à des services précis (TikTok, Facebook, Google…) tout en
          le laissant naviguer sur tout le reste. Le filtrage est appliqué directement sur le LR
          du client (résolution DNS des services bloqués vers <code>0.0.0.0</code>) et ré-appliqué
          automatiquement toutes les 120 s (survit au reboot du LR).
        </p>
      </div>

      {/* Search */}
      <div>
        <input
          type="search"
          placeholder="Rechercher un client par numéro ou par IP…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full md:w-96 px-3 py-2 text-sm rounded-lg border border-blue-200 focus:outline-none focus:ring-2 focus:ring-blue-200"
        />

        {debounced.length >= 2 && (
          <div className="mt-2 bg-white border border-blue-100 rounded-xl shadow-sm overflow-hidden max-w-2xl">
            {searching && lrResults.length === 0 ? (
              <div className="px-4 py-6 text-center text-blue-300 text-sm">Recherche…</div>
            ) : lrResults.length === 0 ? (
              <div className="px-4 py-6 text-center text-blue-400 text-sm">Aucun client (LR) trouvé.</div>
            ) : (
              <ul className="divide-y divide-blue-50 max-h-72 overflow-y-auto">
                {lrResults.map((r) => (
                  <li key={r.id}>
                    <button
                      onClick={() => pickClient(r.id)}
                      className={`w-full text-left px-4 py-2.5 hover:bg-blue-50/60 transition-colors ${
                        selectedId === r.id ? 'bg-blue-50' : ''
                      }`}
                    >
                      <div className="text-slate-800 font-medium text-sm">{r.name}</div>
                      <div className="text-blue-300 font-mono text-[11px]">
                        <IpLink ip={r.ip_address} />
                        {r.site && <span className="ml-2 text-blue-400">· {r.site}</span>}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {/* Selected client + category toggles */}
      {lr && (
        <div className="bg-white border border-blue-100 rounded-xl shadow-sm p-5 max-w-2xl space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-lg font-semibold text-slate-800">{lr.name}</div>
              <div className="text-blue-300 font-mono text-xs">
                <IpLink ip={lr.ip_address} />
              </div>
            </div>
            {lr.blocked_categories.length > 0 && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-red-100 text-red-700 text-[11px] font-semibold">
                {lr.blocked_categories.length} service(s) bloqué(s)
              </span>
            )}
          </div>

          {isBridge && (
            <div className="rounded-lg bg-amber-50 border border-amber-300 text-amber-800 text-xs px-3 py-2">
              ⚠ Ce LR est en <strong>mode bridge</strong> — le filtre de contenu ne peut pas fonctionner
              (dnsmasq est contourné). Repasse-le en mode routeur via airOS.
            </div>
          )}
          {noSsh && !isBridge && (
            <div className="rounded-lg bg-amber-50 border border-amber-300 text-amber-800 text-xs px-3 py-2">
              ⚠ Ce LR n'a pas d'identifiants SSH enregistrés — impossible d'appliquer un filtre.
            </div>
          )}

          <fieldset disabled={isBridge || noSsh} className="space-y-2">
            <legend className="text-xs font-semibold uppercase tracking-wider text-blue-500 mb-1">
              Services à bloquer
            </legend>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {(categories ?? []).map((cat) => {
                const checked = selected.has(cat.key)
                return (
                  <label
                    key={cat.key}
                    className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border cursor-pointer transition-colors ${
                      checked
                        ? 'border-red-300 bg-red-50'
                        : 'border-blue-100 hover:bg-blue-50/60'
                    } ${isBridge || noSsh ? 'opacity-60 cursor-not-allowed' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(cat.key)}
                      className="w-4 h-4 accent-red-600"
                    />
                    <span className="text-sm text-slate-800 font-medium">{cat.label}</span>
                  </label>
                )
              })}
            </div>
            {selected.has('google') && (
              <p className="text-[11px] text-amber-700 mt-1">
                ⚠ Bloquer Google coupe aussi YouTube et de nombreux services (reCAPTCHA, cartes…).
              </p>
            )}
          </fieldset>

          <div className="flex items-center gap-3 pt-1">
            <button
              onClick={onApply}
              disabled={!canApply || !dirty}
              className={`px-4 py-2 rounded-lg text-sm font-semibold text-white transition-colors ${
                canApply && dirty ? 'bg-blue-600 hover:bg-blue-700' : 'bg-blue-200 cursor-not-allowed'
              }`}
            >
              {applying ? 'Application…' : 'Appliquer'}
            </button>
            {selected.size === 0 && lr.blocked_categories.length > 0 && (
              <span className="text-xs text-blue-400">Aucun service coché → le filtre sera retiré.</span>
            )}
          </div>

          {result && (
            <div
              className={`rounded-lg text-xs px-3 py-2 ${
                result.ok
                  ? 'bg-green-50 border border-green-200 text-green-700'
                  : 'bg-red-50 border border-red-200 text-red-700'
              }`}
            >
              {result.message}
            </div>
          )}
        </div>
      )}

      {!lr && debounced.length < 2 && (
        <p className="text-sm text-blue-400">
          Tape au moins 2 caractères pour rechercher un client.
        </p>
      )}
    </div>
  )
}
