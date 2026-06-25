'use client'

import { useMemo, useState } from 'react'
import useSWR, { type KeyedMutator } from 'swr'
import { endpoints, fetcher, updateDevice } from '@/lib/api'
import type { CapacityBucket, NetworkCapacity, RocketCapacity, SiteCapacity, SiteInfra } from '@/lib/types'
import CapacityDonut from '@/components/CapacityDonut'

// Un Rocket saturé = la même ligne que dans le drill-down, + le site auquel il
// appartient (perdu dans le nesting par site, ré-attaché ici pour la liste plate).
type SaturatedRocket = RocketCapacity & { site: string }

// Couleurs par famille (utilisé / disponible) — mêmes teintes que les donuts.
const FAMILY = {
  ltu:    { label: 'LTU',    used: '#22c55e', free: '#fcd34d' },
  airmax: { label: 'airMAX', used: '#3b82f6', free: '#fdba74' },
} as const

type Family = keyof typeof FAMILY

export default function CapacityPage() {
  const { data, error, isLoading, mutate } = useSWR<NetworkCapacity>(
    endpoints.networkCapacity, fetcher, { refreshInterval: 30_000 },
  )
  const [selectedSite, setSelectedSite] = useState<string | null>(null)

  const sites = data?.sites ?? []
  const siteObj = selectedSite != null ? sites.find(s => s.site === selectedSite) ?? null : null

  // Échelle commune des barres par site = plus grande capacité (famille × site).
  const globalMax = useMemo(
    () => sites.reduce((m, s) => Math.max(m, s.ltu.capacity, s.airmax.capacity), 0),
    [sites],
  )

  // Rockets saturés : clients installés ≥ max. Capacité indéterminée
  // (max_clients null) = jamais saturé. Trié du plus surchargé au moins
  // (ratio installés/max décroissant).
  const saturatedRockets = useMemo<SaturatedRocket[]>(() => {
    const out: SaturatedRocket[] = []
    for (const s of sites) {
      for (const r of s.rockets) {
        if (r.max_clients != null && r.max_clients > 0 && r.current_clients >= r.max_clients) {
          out.push({ ...r, site: s.site })
        }
      }
    }
    return out.sort(
      (a, b) => b.current_clients / b.max_clients! - a.current_clients / a.max_clients!,
    )
  }, [sites])

  if (error) {
    return <p className="text-red-600 text-sm">Erreur de chargement de la capacité réseau.</p>
  }

  return (
    <div className="space-y-6">
      {/* Breadcrumb / header */}
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-sm mb-1">
          <button
            onClick={() => setSelectedSite(null)}
            className={selectedSite == null
              ? 'font-bold text-blue-900 text-2xl tracking-tight'
              : 'text-blue-500 hover:text-blue-700 transition-colors'}
          >
            {selectedSite == null ? 'Capacité du réseau' : 'Sites'}
          </button>
          {selectedSite != null && (
            <>
              <span className="text-blue-200">/</span>
              <span className="font-bold text-blue-900 text-2xl tracking-tight truncate">
                {selectedSite}
              </span>
            </>
          )}
        </div>
        <p className="text-blue-400 text-sm">
          {siteObj != null
            ? <>Rockets de ce site — clients installés vs maximum avant saturation.</>
            : <>Clients installés vs disponibles par famille radio et par site — clique un site pour voir ses Rockets.</>}
        </p>
      </div>

      {isLoading && <p className="text-slate-400 text-sm">Chargement…</p>}

      {data != null && siteObj == null && (
        <>
          {/* Cercles globaux */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 max-w-3xl">
            <CapacityDonut
              title="LTU" used={FAMILY.ltu.used} free={FAMILY.ltu.free}
              consumed={data.families.ltu.consumed}
              available={data.families.ltu.available}
              rockets={data.families.ltu.rockets}
              unknown={data.families.ltu.unknown}
            />
            <CapacityDonut
              title="airMAX" used={FAMILY.airmax.used} free={FAMILY.airmax.free}
              consumed={data.families.airmax.consumed}
              available={data.families.airmax.available}
              rockets={data.families.airmax.rockets}
              unknown={data.families.airmax.unknown}
            />
          </div>

          {/* Rockets saturés */}
          <SaturatedRocketsSection
            rockets={saturatedRockets}
            onSelectSite={setSelectedSite}
          />

          {/* Capacité infra par site (Rockets + AF60 + PTP vs max) */}
          {data.infra != null && (
            <SiteInfraSection
              infra={data.infra}
              navigable={new Set(sites.map(s => s.site))}
              onSelectSite={setSelectedSite}
            />
          )}

          {/* Barres par site */}
          <div className="bg-white border border-blue-100 rounded-xl shadow-sm p-5">
            <div className="mb-4">
              <h3 className="font-semibold text-blue-900">Capacité par site</h3>
              <p className="text-xs text-blue-400 mt-0.5">
                Longueur = capacité totale du site ; partie pleine = clients installés.
                Clique un site pour le détail.
              </p>
            </div>
            {sites.length === 0 ? (
              <p className="py-8 text-center text-slate-400 text-sm">Aucun site.</p>
            ) : (
              <div className="space-y-3 max-h-[28rem] overflow-y-auto pr-1">
                {sites.map(s => (
                  <button
                    key={s.site}
                    onClick={() => setSelectedSite(s.site)}
                    className="w-full text-left rounded-lg px-2 py-2 hover:bg-blue-50 transition-colors"
                  >
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-sm font-semibold text-slate-800 truncate">{s.site}</span>
                      {s.unknown > 0 && (
                        <span
                          className="shrink-0 text-[10px] font-medium text-amber-700 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5"
                          title="Rockets à capacité indéterminée (largeur de canal inconnue, exclus des totaux)"
                        >
                          {s.unknown} indéterminé{s.unknown > 1 ? 's' : ''}
                        </span>
                      )}
                    </div>
                    <div className="space-y-1">
                      <SiteFamilyBar family="ltu" bucket={s.ltu} globalMax={globalMax} />
                      <SiteFamilyBar family="airmax" bucket={s.airmax} globalMax={globalMax} />
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {/* Drill-down : Rockets du site */}
      {siteObj != null && <SiteRocketsTable site={siteObj} onSaved={mutate} />}
    </div>
  )
}

function SiteInfraSection({
  infra, navigable, onSelectSite,
}: {
  infra: NetworkCapacity['infra']
  navigable: Set<string>
  onSelectSite: (site: string) => void
}) {
  const overCount = infra.sites.filter(s => s.over).length

  // Échelle de l'axe : 0 → max(plafond, plus gros site) avec ~15 % de marge à
  // droite pour que la plus longue barre ne touche pas le bord. Au moins le
  // plafond + 1 pour que la ligne de plafond reste lisible même si tout est bas.
  const axisMax = useMemo(() => {
    const maxCount = infra.sites.reduce((m, s) => Math.max(m, s.count), 0)
    return Math.max(Math.ceil(Math.max(infra.threshold, maxCount) * 1.15), infra.threshold + 1)
  }, [infra])
  const thresholdPct = (infra.threshold / axisMax) * 100

  return (
    <div className="bg-white border border-blue-100 rounded-xl shadow-sm p-5">
      <div className="mb-4 flex items-center gap-2 flex-wrap">
        <h3 className="font-semibold text-blue-900">Capacité infra par site</h3>
        <span className="text-xs font-semibold text-slate-600 bg-slate-50 border border-slate-200 rounded-full px-2 py-0.5 tabular-nums">
          max {infra.threshold}/site
        </span>
        {overCount > 0 && (
          <span className="text-xs font-semibold text-red-600 bg-red-50 border border-red-200 rounded-full px-2 py-0.5 tabular-nums">
            {overCount} en dépassement
          </span>
        )}
        <p className="text-xs text-blue-400 ml-1 w-full sm:w-auto">
          Rockets + AF60 + PTP (hors switch et UISP Power). +N = places libres, −N = dépassement.
        </p>
      </div>
      {infra.sites.length === 0 ? (
        <p className="py-6 text-center text-slate-400 text-sm">Aucun équipement infra.</p>
      ) : (
        <div className="space-y-2.5 max-h-[28rem] overflow-y-auto pr-1">
          {infra.sites.map((s: SiteInfra) => {
            const canNavigate = navigable.has(s.site)
            const barPct = Math.min((s.count / axisMax) * 100, 100)
            return (
              <button
                key={s.site}
                onClick={canNavigate ? () => onSelectSite(s.site) : undefined}
                disabled={!canNavigate}
                className={`w-full text-left flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors ${
                  canNavigate ? 'hover:bg-blue-50 cursor-pointer' : 'cursor-default'
                }`}
              >
                <span
                  className="w-32 shrink-0 text-xs font-semibold text-slate-800 truncate text-right"
                  title={s.site}
                >
                  {s.site}
                </span>
                {/* Piste = repère ; ligne de plafond ; barre = nb d'équipements */}
                <div className="relative flex-1 bg-slate-50 rounded h-5 overflow-hidden">
                  <div
                    className="absolute inset-y-0 left-0 rounded"
                    style={{
                      width: `${Math.max(barPct, 1.5)}%`,
                      background: s.over ? '#ef4444' : '#10b981',
                    }}
                  />
                  {/* Plafond : trait vertical en pointillés rouges */}
                  <div
                    className="absolute inset-y-0 border-l-2 border-dashed border-red-400"
                    style={{ left: `${thresholdPct}%` }}
                    title={`Plafond ${infra.threshold}`}
                  />
                </div>
                <span className="w-10 shrink-0 text-right text-xs font-bold text-slate-700 tabular-nums">
                  {s.count}
                </span>
                <span
                  className={`w-10 shrink-0 text-center text-xs font-semibold rounded-full px-1.5 py-0.5 tabular-nums ${
                    s.over
                      ? 'text-red-600 bg-red-50 border border-red-200'
                      : 'text-emerald-600 bg-emerald-50 border border-emerald-200'
                  }`}
                >
                  {s.remaining >= 0 ? `+${s.remaining}` : `−${Math.abs(s.remaining)}`}
                </span>
              </button>
            )
          })}
          {/* Légende de l'axe : 0 … plafond … max */}
          <div className="flex items-center gap-3 pt-1.5 mt-1 border-t border-blue-50 text-[10px] text-slate-400 tabular-nums">
            <span className="w-32 shrink-0 text-right">0</span>
            <div className="relative flex-1 h-3">
              <span
                className="absolute -translate-x-1/2 text-red-400 font-medium"
                style={{ left: `${thresholdPct}%` }}
              >
                plafond {infra.threshold}
              </span>
              <span className="absolute right-0">{axisMax}</span>
            </div>
            <span className="w-10 shrink-0" />
            <span className="w-10 shrink-0" />
          </div>
        </div>
      )}
    </div>
  )
}

function SaturatedRocketsSection({
  rockets, onSelectSite,
}: { rockets: SaturatedRocket[]; onSelectSite: (site: string) => void }) {
  return (
    <div className="bg-white border border-red-100 rounded-xl shadow-sm p-5">
      <div className="mb-4 flex items-center gap-2">
        <span className="inline-block w-2.5 h-2.5 rounded-full bg-red-500" />
        <h3 className="font-semibold text-blue-900">Rockets saturés</h3>
        <span className="text-xs font-semibold text-red-600 bg-red-50 border border-red-200 rounded-full px-2 py-0.5 tabular-nums">
          {rockets.length}
        </span>
        <p className="text-xs text-blue-400 ml-1">
          Clients installés ≥ maximum — capacité atteinte ou dépassée.
        </p>
      </div>
      {rockets.length === 0 ? (
        <p className="py-6 text-center text-slate-400 text-sm">Aucun Rocket saturé. 🎉</p>
      ) : (
        <div className="space-y-2 max-h-[24rem] overflow-y-auto pr-1">
          {rockets.map(r => {
            const fam = FAMILY[r.family]
            const pct = Math.round((r.current_clients / r.max_clients!) * 100)
            return (
              <button
                key={r.id}
                onClick={() => onSelectSite(r.site)}
                className="w-full text-left flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-red-50 transition-colors"
              >
                <span className="inline-block w-2.5 h-2.5 shrink-0 rounded-sm" style={{ background: fam.used }} title={fam.label} />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold text-slate-800 truncate" title={r.name}>{r.name}</div>
                  <div className="text-[11px] text-blue-400 truncate">
                    {r.site} · {fam.label}
                    {r.channel_width_mhz != null ? ` · ${Math.round(r.channel_width_mhz)} MHz` : ''}
                  </div>
                </div>
                <span className="shrink-0 text-sm font-bold text-red-600 tabular-nums">
                  {r.current_clients} / {r.max_clients}
                </span>
                <span className="w-12 shrink-0 text-right text-xs font-semibold text-red-500 tabular-nums">
                  {pct}%
                </span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

function SiteFamilyBar({
  family, bucket, globalMax,
}: { family: Family; bucket: CapacityBucket; globalMax: number }) {
  const { label, used, free } = FAMILY[family]

  if (bucket.capacity <= 0) {
    if (bucket.unknown <= 0) return null
    return (
      <div className="flex items-center gap-2">
        <span className="w-14 shrink-0 text-[11px] text-slate-500 text-right">{label}</span>
        <span className="text-[11px] text-amber-600">{bucket.unknown} Rocket(s) — capacité indéterminée</span>
      </div>
    )
  }

  const trackPct = globalMax > 0 ? (bucket.capacity / globalMax) * 100 : 0
  const usedPct = bucket.capacity > 0 ? (bucket.consumed / bucket.capacity) * 100 : 0

  return (
    <div className="flex items-center gap-2">
      <span className="w-14 shrink-0 text-[11px] text-slate-500 text-right">{label}</span>
      <div className="flex-1 bg-slate-50 rounded h-4 overflow-hidden relative">
        <div className="absolute inset-y-0 left-0 flex" style={{ width: `${Math.max(trackPct, 1)}%` }}>
          <div className="h-full" style={{ width: `${usedPct}%`, background: used }} />
          <div className="h-full flex-1" style={{ background: free }} />
        </div>
      </div>
      <span className="w-16 shrink-0 text-[11px] font-semibold text-slate-800 text-right tabular-nums">
        {bucket.consumed}/{bucket.capacity}
      </span>
      <span
        className="w-8 shrink-0 text-[10px] text-amber-600 text-right tabular-nums"
        title={bucket.unknown > 0
          ? `${bucket.unknown} Rocket(s) à capacité indéterminée (largeur inconnue, exclus du total)`
          : undefined}
      >
        {bucket.unknown > 0 ? `+${bucket.unknown}` : ''}
      </span>
    </div>
  )
}

function SiteRocketsTable({
  site, onSaved,
}: { site: SiteCapacity; onSaved: KeyedMutator<NetworkCapacity> }) {
  return (
    <div className="bg-white border border-blue-100 rounded-xl shadow-sm overflow-hidden">
      <div className="px-4 pt-4 pb-2">
        <p className="text-xs text-blue-400">
          La <strong className="text-slate-600">capacité max</strong> est calculée automatiquement
          (famille radio + largeur de canal). Clique « modifier » pour la fixer manuellement sur une
          Rocket — la valeur saisie remplace alors le calcul auto.
        </p>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-blue-50 text-blue-700 text-xs uppercase tracking-wide">
            <th className="text-left font-semibold px-4 py-2.5">Rocket</th>
            <th className="text-left font-semibold px-4 py-2.5">Famille</th>
            <th className="text-right font-semibold px-4 py-2.5">Largeur</th>
            <th className="text-right font-semibold px-4 py-2.5">Installés</th>
            <th className="text-right font-semibold px-4 py-2.5 w-56">Capacité max</th>
            <th className="text-left font-semibold px-4 py-2.5 w-40">Charge</th>
          </tr>
        </thead>
        <tbody>
          {site.rockets.map(r => {
            const fam = FAMILY[r.family]
            const pct = r.max_clients && r.max_clients > 0
              ? Math.min(Math.round((r.current_clients / r.max_clients) * 100), 100)
              : null
            const over = r.max_clients != null && r.current_clients >= r.max_clients
            return (
              <tr key={r.id} className="border-t border-blue-50">
                <td className="px-4 py-2.5 text-slate-800 truncate max-w-[16rem]" title={r.name}>{r.name}</td>
                <td className="px-4 py-2.5">
                  <span className="inline-flex items-center gap-1.5 text-xs text-slate-600">
                    <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: fam.used }} />
                    {fam.label}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-right text-slate-600 tabular-nums">
                  {r.channel_width_mhz != null ? `${Math.round(r.channel_width_mhz)} MHz` : '—'}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums">
                  <span className={over ? 'font-bold text-red-600' : 'text-slate-800'}>
                    {r.current_clients}
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  <MaxClientsCell rocket={r} onSaved={onSaved} />
                </td>
                <td className="px-4 py-2.5">
                  {pct != null ? (
                    <div className="flex items-center gap-2">
                      <div className="flex-1 bg-slate-100 rounded h-3 overflow-hidden">
                        <div
                          className="h-full rounded"
                          style={{ width: `${Math.max(pct, 2)}%`, background: over ? '#dc2626' : fam.used }}
                        />
                      </div>
                      <span className="w-9 text-right text-[11px] text-slate-500 tabular-nums">{pct}%</span>
                    </div>
                  ) : (
                    <span className="text-[11px] text-slate-400">—</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// Cellule « capacité max » éditable. Affiche la valeur effective (override si
// posé, sinon formule) + un badge « manuel » ; en mode édition, saisie d'un
// nombre qui remplace la formule, bouton « Auto » pour revenir au calcul.
function MaxClientsCell({
  rocket, onSaved,
}: { rocket: RocketCapacity; onSaved: KeyedMutator<NetworkCapacity> }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const isManual = rocket.max_clients_override != null
  const autoLabel = rocket.max_clients_auto != null ? `${rocket.max_clients_auto}` : 'indéterminé'

  function startEdit() {
    setDraft(rocket.max_clients_override != null ? String(rocket.max_clients_override) : '')
    setErr(null)
    setEditing(true)
  }

  async function commit(value: number | null) {
    setSaving(true)
    setErr(null)
    try {
      await updateDevice(rocket.id, {
        device_type: 'rocket',
        radio_tech: rocket.family,
        max_clients_override: value,
      })
      await onSaved()
      setEditing(false)
    } catch {
      setErr('Échec de l’enregistrement')
    } finally {
      setSaving(false)
    }
  }

  function save() {
    const trimmed = draft.trim()
    if (trimmed === '') {
      void commit(null) // vide = repasse en automatique
      return
    }
    const n = Number(trimmed)
    if (!Number.isInteger(n) || n <= 0) {
      setErr('Entier > 0 attendu')
      return
    }
    void commit(n)
  }

  if (editing) {
    return (
      <div className="flex items-center justify-end gap-1.5">
        <input
          type="number"
          min={1}
          autoFocus
          value={draft}
          disabled={saving}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') save()
            if (e.key === 'Escape') setEditing(false)
          }}
          placeholder={autoLabel}
          className="w-20 rounded border border-blue-300 px-2 py-1 text-right text-sm tabular-nums focus:outline-none focus:ring-1 focus:ring-blue-400"
        />
        <button
          onClick={save}
          disabled={saving}
          className="rounded bg-blue-600 px-2 py-1 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
          title="Enregistrer"
        >
          ✓
        </button>
        <button
          onClick={() => setEditing(false)}
          disabled={saving}
          className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-500 hover:bg-slate-50 disabled:opacity-50"
          title="Annuler"
        >
          ✗
        </button>
        {err != null && <span className="text-[10px] text-red-600">{err}</span>}
      </div>
    )
  }

  return (
    <div className="flex items-center justify-end gap-2">
      <span className="tabular-nums text-slate-800">
        {rocket.max_clients != null ? rocket.max_clients : <span className="text-amber-600">indéterminé</span>}
      </span>
      {isManual ? (
        <span
          className="shrink-0 text-[10px] font-medium text-blue-700 bg-blue-50 border border-blue-200 rounded px-1.5 py-0.5"
          title={`Valeur manuelle — la formule automatique donnerait ${autoLabel}`}
        >
          manuel
        </span>
      ) : (
        <span className="shrink-0 text-[10px] text-slate-400">auto</span>
      )}
      <button
        onClick={startEdit}
        className="shrink-0 text-[11px] font-medium text-blue-500 hover:text-blue-700 hover:underline"
      >
        modifier
      </button>
    </div>
  )
}
