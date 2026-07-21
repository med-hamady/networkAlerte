'use client'

import React from 'react'
import useSWR from 'swr'
import {
  endpoints,
  fetcher,
  setContentBlock,
  type ContentBlockCategory,
  type ContentBlockMode,
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

  // Filter direction, seeded from the device the same way.
  const [mode, setMode] = React.useState<ContentBlockMode>('denylist')
  const deviceMode = lr?.content_block_mode ?? 'denylist'
  React.useEffect(() => {
    setMode(deviceMode === 'allowlist' ? 'allowlist' : 'denylist')
  }, [lr?.id, deviceMode])

  const [applying, setApplying] = React.useState(false)
  const [result, setResult] = React.useState<{ ok: boolean; message: string } | null>(null)

  const isBridge = lr?.topology_mode === 'bridge'
  const noSsh = lr ? !lr.has_ssh_password : false
  const canApply = !!lr && !isBridge && !noSsh && !applying
  const isAllow = mode === 'allowlist'

  const initial = new Set(lr?.blocked_categories ?? [])
  const dirty =
    mode !== deviceMode ||
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

  // Single write path for both "Appliquer" and "Tout retirer" — the backend
  // takes the complete desired set, so removal is just applying an empty one.
  const push = async (categories: string[], pushMode: ContentBlockMode = mode) => {
    if (!lr) return
    setApplying(true)
    setResult(null)
    try {
      const r = await setContentBlock(lr.id, categories, pushMode)
      setResult({ ok: r.ok, message: r.message })
      await mutateDevice()
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : 'Erreur inconnue' })
    } finally {
      setApplying(false)
    }
  }

  const onApply = () => push([...selected])
  const onRemoveAll = () => push([])

  const pickClient = (id: number) => {
    setSelectedId(id)
    setResult(null)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Filtre de contenu</h1>
        <p className="text-blue-400 text-sm mt-1 max-w-3xl">
          Filtre les services accessibles à un client, dans les deux sens : <strong>autoriser tout
          sauf</strong> certains services, ou <strong>tout bloquer sauf</strong> certains services.
          Le filtrage est appliqué directement sur le LR du client (au niveau DNS) et ré-appliqué
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
              <span
                className={`inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-semibold ${
                  deviceMode === 'allowlist'
                    ? 'bg-green-100 text-green-800'
                    : 'bg-red-100 text-red-700'
                }`}
              >
                {deviceMode === 'allowlist'
                  ? `${lr.blocked_categories.length} service(s) autorisé(s) uniquement`
                  : `${lr.blocked_categories.length} service(s) bloqué(s)`}
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

          <fieldset disabled={isBridge || noSsh} className="space-y-4">
            {/* Direction of the filter — everything below reads from it. */}
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-blue-500 mb-1.5">
                Type de filtrage
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <ModeCard
                  active={!isAllow}
                  disabled={isBridge || noSsh}
                  onClick={() => { setMode('denylist'); setResult(null) }}
                  title="Autoriser tout, sauf…"
                  desc="Le client navigue partout sauf sur les services cochés."
                />
                <ModeCard
                  active={isAllow}
                  disabled={isBridge || noSsh}
                  onClick={() => { setMode('allowlist'); setResult(null) }}
                  title="Tout bloquer, sauf…"
                  desc="Le client n'a accès qu'aux services cochés."
                />
              </div>
            </div>

            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-blue-500 mb-1.5">
                {isAllow ? 'Services autorisés' : 'Services à bloquer'}
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {(categories ?? []).map((cat) => {
                  const checked = selected.has(cat.key)
                  // Ticked means "allowed" in allowlist mode → green, not red.
                  const tone = isAllow ? 'border-green-300 bg-green-50' : 'border-red-300 bg-red-50'
                  return (
                    <label
                      key={cat.key}
                      // Exact domain list on hover: the label is a summary, this
                      // is the ground truth of what the LR will actually block.
                      title={`${cat.domain_count} domaine(s) : ${cat.domains.join(', ')}`}
                      className={`flex items-start gap-3 px-3 py-2.5 rounded-lg border cursor-pointer transition-colors ${
                        checked ? tone : 'border-blue-100 hover:bg-blue-50/60'
                      } ${isBridge || noSsh ? 'opacity-60 cursor-not-allowed' : ''}`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(cat.key)}
                        className={`w-4 h-4 mt-0.5 shrink-0 ${
                          isAllow ? 'accent-green-600' : 'accent-red-600'
                        }`}
                      />
                      <span className="min-w-0">
                        <span className="block text-sm text-slate-800 font-medium leading-tight">
                          {cat.label}
                        </span>
                        {cat.description && (
                          <span className="block text-[11px] text-blue-400 mt-0.5 leading-snug">
                            {cat.description}
                          </span>
                        )}
                      </span>
                    </label>
                  )
                })}
              </div>

              {!isAllow && selected.has('google') && (
                <p className="text-[11px] text-amber-700 mt-2 leading-relaxed">
                  ⚠ « Google » ne couvre <strong>pas</strong> YouTube (case séparée). En revanche
                  il coupe la recherche, Drive, Maps — et de nombreux sites tiers qui s'appuient
                  sur Google (reCAPTCHA, cartes intégrées, polices). À n'utiliser qu'en connaissance
                  de cause.
                </p>
              )}
              {isAllow && (
                <p className="text-[11px] text-amber-700 mt-2 leading-relaxed">
                  ⚠ « Tout bloquer sauf » est moins étanche que l'inverse : le blocage se fait au
                  niveau DNS, donc un client qui utilise une IP directe ou du DNS chiffré (DoH)
                  peut passer outre. Pour une coupure stricte liée à un impayé, utilise plutôt la
                  page <strong>FAI</strong>.
                </p>
              )}
              {isAllow && selected.size === 0 && (
                <p className="text-[11px] text-red-700 mt-2">
                  ⚠ Aucun service coché en mode « tout bloquer » = le filtre sera simplement retiré
                  (on ne coupe pas un client sans rien autoriser depuis cette page).
                </p>
              )}
            </div>
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

            {/* One-click removal of every rule on this LR. Only shown when
                something is actually applied — it acts on the LR's real state,
                not on the checkboxes, so it works even mid-edit. */}
            {lr.blocked_categories.length > 0 && (
              <button
                onClick={onRemoveAll}
                disabled={!canApply}
                className={`px-4 py-2 rounded-lg text-sm font-semibold border transition-colors ${
                  canApply
                    ? 'border-red-300 text-red-700 hover:bg-red-50'
                    : 'border-blue-100 text-blue-300 cursor-not-allowed'
                }`}
              >
                Tout retirer
              </button>
            )}

            {selected.size === 0 && lr.blocked_categories.length > 0 && (
              <span className="text-xs text-blue-400">Aucun service coché → le filtre sera retiré.</span>
            )}
          </div>

          <div>
            {/* Plain-language recap of what will actually happen. */}
            {selected.size > 0 && (
              <p className="text-xs text-slate-600">
                {isAllow ? (
                  <>Ce client n'aura accès <strong>qu'à</strong> : {labelsOf(selected, categories)}.</>
                ) : (
                  <>Ce client aura accès à tout <strong>sauf</strong> : {labelsOf(selected, categories)}.</>
                )}
              </p>
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

/** Human labels for the ticked category keys, in catalogue order. */
function labelsOf(keys: Set<string>, categories?: ContentBlockCategory[]): string {
  return (categories ?? [])
    .filter((c) => keys.has(c.key))
    .map((c) => c.label)
    .join(', ')
}

function ModeCard({ active, disabled, onClick, title, desc }: {
  active: boolean
  disabled: boolean
  onClick: () => void
  title: string
  desc: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`text-left px-3 py-2.5 rounded-lg border transition-colors ${
        active
          ? 'border-blue-500 bg-blue-50 ring-1 ring-blue-300'
          : 'border-blue-100 hover:bg-blue-50/60'
      } ${disabled ? 'opacity-60 cursor-not-allowed' : ''}`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`w-3.5 h-3.5 rounded-full border shrink-0 ${
            active ? 'border-blue-600 bg-blue-600 ring-2 ring-inset ring-white' : 'border-blue-300'
          }`}
        />
        <span className="text-sm font-semibold text-slate-800">{title}</span>
      </div>
      <p className="text-[11px] text-blue-400 mt-1 ml-5">{desc}</p>
    </button>
  )
}
