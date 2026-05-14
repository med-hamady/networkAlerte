'use client'

import { useMemo, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { DeviceDowntime, DowntimeEpisode, DowntimeLogResponse } from '@/lib/types'
import { alertTypeLabel, deviceTypeLabel, severityLabel } from '@/lib/types'

// ─── Date helpers — convert between browser-local <input> and UTC ISO ─────
function nowMinusHoursLocalInput(hours: number): string {
  const d = new Date(Date.now() - hours * 3_600_000)
  const offsetMs = d.getTimezoneOffset() * 60_000
  return new Date(d.getTime() - offsetMs).toISOString().slice(0, 16)
}
function nowLocalInput(): string {
  const d = new Date()
  const offsetMs = d.getTimezoneOffset() * 60_000
  return new Date(d.getTime() - offsetMs).toISOString().slice(0, 16)
}
function localInputToIso(local: string): string {
  return new Date(local).toISOString()
}

// ─── Format helpers ───────────────────────────────────────────────────────
function fmtDuration(secs: number): string {
  if (secs < 0) return '0s'
  if (secs < 60) return `${Math.round(secs)}s`
  if (secs < 3_600) {
    const m = Math.floor(secs / 60)
    const s = Math.round(secs % 60)
    return s === 0 ? `${m} min` : `${m} min ${s}s`
  }
  const h = Math.floor(secs / 3_600)
  const m = Math.round((secs % 3_600) / 60)
  return m === 0 ? `${h}h` : `${h}h ${m.toString().padStart(2, '0')}min`
}

function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtTimeOnly(iso: string): string {
  return new Date(iso).toLocaleTimeString('fr-FR', {
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtDayMonth(iso: string): string {
  return new Date(iso).toLocaleDateString('fr-FR', {
    day: '2-digit', month: '2-digit',
  })
}

function isSameDay(a: string, b: string): boolean {
  const da = new Date(a)
  const db = new Date(b)
  return da.getFullYear() === db.getFullYear()
    && da.getMonth() === db.getMonth()
    && da.getDate() === db.getDate()
}

// ─── Page ─────────────────────────────────────────────────────────────────
export default function NetworkUptimePage() {
  const [startInput, setStartInput] = useState<string>(() => nowMinusHoursLocalInput(24))
  const [endInput, setEndInput] = useState<string>(() => nowLocalInput())
  const [appliedStart, setAppliedStart] = useState<string>(startInput)
  const [appliedEnd, setAppliedEnd] = useState<string>(endInput)
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [nameFilter, setNameFilter] = useState<string>('')

  const startIso = useMemo(() => localInputToIso(appliedStart), [appliedStart])
  const endIso   = useMemo(() => localInputToIso(appliedEnd),   [appliedEnd])

  const { data, isLoading, error } = useSWR<DowntimeLogResponse>(
    endpoints.downtimeLog(startIso, endIso),
    fetcher,
    { refreshInterval: 60_000 },
  )

  const allItems = data?.items ?? []
  const filteredItems = useMemo(() => {
    const q = nameFilter.trim().toLowerCase()
    return allItems.filter(d => {
      if (typeFilter !== 'all' && d.device_type !== typeFilter) return false
      if (q && !d.device_name.toLowerCase().includes(q) && !d.device_ip.toLowerCase().includes(q)) return false
      return true
    })
  }, [allItems, typeFilter, nameFilter])

  const totalEpisodes = filteredItems.reduce((s, d) => s + d.episodes_count, 0)
  const totalSeconds = filteredItems.reduce((s, d) => s + d.total_downtime_seconds, 0)
  const stillDownCount = filteredItems.filter(d => d.current_status === 'down').length

  const onApply = () => {
    setAppliedStart(startInput)
    setAppliedEnd(endInput)
  }

  const onPreset = (hours: number) => {
    const s = nowMinusHoursLocalInput(hours)
    const e = nowLocalInput()
    setStartInput(s); setEndInput(e)
    setAppliedStart(s); setAppliedEnd(e)
  }

  const onExportCsv = () => {
    if (!filteredItems.length) return
    const lines: string[] = [
      'device_id,device_name,device_ip,device_type,current_status,availability_pct,episodes_count,raw_episodes_count,total_downtime_seconds,episode_start,episode_end,episode_duration_seconds,severity,alert_type,flap_count',
    ]
    for (const d of filteredItems) {
      for (const ep of d.episodes) {
        const row = [
          d.device_id,
          csvEscape(d.device_name),
          d.device_ip,
          d.device_type,
          d.current_status,
          d.availability_pct.toFixed(3),
          d.episodes_count,
          d.raw_episodes_count,
          Math.round(d.total_downtime_seconds),
          ep.started_at,
          ep.ended_at ?? '',
          Math.round(ep.duration_seconds),
          ep.severity,
          ep.alert_type,
          ep.flap_count,
        ].join(',')
        lines.push(row)
      }
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `downtime-log_${new Date(startIso).toISOString().slice(0, 10)}_${new Date(endIso).toISOString().slice(0, 10)}.csv`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-6">

      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Journal des coupures</h1>
        <p className="text-blue-400 text-sm mt-1">
          Historique des coupures des équipements réseau (Rocket, UISP Switch, UISP Power)
          sur la période choisie. Cliquer sur une ligne pour voir la chronologie de chaque épisode.
        </p>
      </div>

      {/* Filtres */}
      <div className="bg-white border border-blue-100 rounded-xl shadow-sm p-4">
        <div className="flex flex-wrap items-end gap-4">

          <div className="flex flex-col">
            <label className="text-xs font-medium text-blue-500 uppercase tracking-wider mb-1">Du</label>
            <input
              type="datetime-local"
              value={startInput}
              onChange={e => setStartInput(e.target.value)}
              className="border border-blue-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
            />
          </div>

          <div className="flex flex-col">
            <label className="text-xs font-medium text-blue-500 uppercase tracking-wider mb-1">Au</label>
            <input
              type="datetime-local"
              value={endInput}
              onChange={e => setEndInput(e.target.value)}
              className="border border-blue-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
            />
          </div>

          <button
            onClick={onApply}
            className="text-sm text-white bg-blue-700 hover:bg-blue-800 font-medium px-4 py-1.5 rounded-lg transition-colors shadow-sm"
          >
            Appliquer
          </button>

          <div className="flex items-center gap-2 ml-auto">
            <span className="text-xs text-blue-400">Raccourcis :</span>
            <button onClick={() => onPreset(24)}      className="text-xs text-blue-600 border border-blue-200 rounded px-2 py-1 hover:bg-blue-50">24 h</button>
            <button onClick={() => onPreset(24 * 7)}  className="text-xs text-blue-600 border border-blue-200 rounded px-2 py-1 hover:bg-blue-50">7 j</button>
            <button onClick={() => onPreset(24 * 30)} className="text-xs text-blue-600 border border-blue-200 rounded px-2 py-1 hover:bg-blue-50">30 j</button>
          </div>
        </div>

        {/* Filtres secondaires */}
        <div className="flex flex-wrap items-end gap-4 mt-4 pt-4 border-t border-blue-50">
          <div className="flex flex-col">
            <label className="text-xs font-medium text-blue-500 uppercase tracking-wider mb-1">Type</label>
            <select
              value={typeFilter}
              onChange={e => setTypeFilter(e.target.value)}
              className="border border-blue-200 rounded-lg px-3 py-1.5 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
            >
              <option value="all">Tous</option>
              <option value="rocket">Rocket</option>
              <option value="uisp_switch">UISP Switch</option>
              <option value="uisp_power">UISP Power</option>
            </select>
          </div>

          <div className="flex flex-col flex-1 min-w-[200px]">
            <label className="text-xs font-medium text-blue-500 uppercase tracking-wider mb-1">Recherche</label>
            <input
              type="text"
              value={nameFilter}
              onChange={e => setNameFilter(e.target.value)}
              placeholder="Nom ou IP…"
              className="border border-blue-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
            />
          </div>

          <button
            onClick={onExportCsv}
            disabled={!filteredItems.length}
            className="text-sm text-blue-700 border border-blue-200 hover:bg-blue-50 disabled:opacity-50 disabled:cursor-not-allowed font-medium px-4 py-1.5 rounded-lg transition-colors flex items-center gap-1.5"
          >
            <DownloadIcon className="w-4 h-4" />
            Exporter CSV
          </button>
        </div>

        {/* Résumé global */}
        <div className="grid grid-cols-3 gap-4 mt-4 pt-4 border-t border-blue-50">
          <Stat label="Équipements touchés" value={`${filteredItems.length}`} />
          <Stat label="Épisodes de coupure" value={`${totalEpisodes}`} />
          <Stat label="Downtime cumulé"     value={fmtDuration(totalSeconds)} extra={stillDownCount > 0 ? `${stillDownCount} encore DOWN` : undefined} />
        </div>
      </div>

      {/* Contenu */}
      {error ? (
        <div className="bg-red-50 border border-red-200 rounded-xl px-6 py-8 text-center text-red-700">
          Erreur : période invalide ou serveur indisponible.
        </div>
      ) : isLoading ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center text-blue-300 shadow-sm">
          Chargement…
        </div>
      ) : filteredItems.length === 0 ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm">
          <p className="text-green-600 font-semibold text-sm">
            {allItems.length === 0
              ? '✓ Aucune coupure sur la période'
              : 'Aucun équipement ne correspond aux filtres'}
          </p>
          <p className="text-blue-400 text-xs mt-1">
            Du {fmtDateTime(startIso)} au {fmtDateTime(endIso)}
          </p>
        </div>
      ) : (
        <div className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
          <table className="w-full text-sm">
            <thead className="bg-blue-50 border-b border-blue-100">
              <tr>
                {['', 'Équipement', 'Type', 'Statut actuel', 'Épisodes', 'Disponibilité', 'Downtime cumulé'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              {filteredItems.map(d => (
                <DeviceRow key={d.device_id} device={d} windowStartIso={startIso} windowEndIso={endIso} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, extra }: { label: string; value: string; extra?: string }) {
  return (
    <div>
      <div className="text-xs text-blue-400 uppercase tracking-wider">{label}</div>
      <div className="text-xl font-bold text-blue-900 mt-1">{value}</div>
      {extra && <div className="text-[11px] text-red-600 font-medium mt-0.5">{extra}</div>}
    </div>
  )
}

function availabilityClass(pct: number): string {
  if (pct >= 99.9) return 'text-green-700 font-semibold'
  if (pct >= 99)   return 'text-amber-600 font-semibold'
  return 'text-red-600 font-semibold'
}

function DeviceRow({
  device, windowStartIso, windowEndIso,
}: {
  device: DeviceDowntime
  windowStartIso: string
  windowEndIso: string
}) {
  const [open, setOpen] = useState(false)
  const isDown = device.current_status === 'down'
  const flapping = device.raw_episodes_count > device.episodes_count

  return (
    <>
      <tr
        onClick={() => setOpen(o => !o)}
        className="hover:bg-blue-50/60 transition-colors cursor-pointer align-top"
      >
        <td className="px-4 py-3 text-blue-400 w-8">
          <svg
            className={`w-4 h-4 transition-transform ${open ? 'rotate-90' : ''}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
        </td>
        <td className="px-4 py-3">
          <div className="text-slate-800 font-medium">{device.device_name}</div>
          <div className="text-blue-300 font-mono text-[11px]">{device.device_ip}</div>
        </td>
        <td className="px-4 py-3 text-xs text-slate-600">{deviceTypeLabel(device.device_type)}</td>
        <td className="px-4 py-3">
          <StatusPill status={device.current_status} />
          {isDown && <div className="text-[11px] text-red-600 font-medium mt-0.5">Encore DOWN</div>}
        </td>
        <td className="px-4 py-3 text-sm">
          <div className="text-slate-700">{device.episodes_count} épisode{device.episodes_count > 1 ? 's' : ''}</div>
          {flapping && (
            <div
              title={`${device.raw_episodes_count} coupures brutes fusionnées en ${device.episodes_count} épisode(s) affiché(s). Indique un lien physiquement instable — câble, sertissage, étanchéité, alimentation limite, ou mât qui bouge.`}
              className="mt-1 inline-flex items-center gap-1.5 text-xs font-bold px-2 py-1 rounded-md bg-orange-100 text-orange-800 border border-orange-300 shadow-sm"
            >
              <span className="text-base leading-none">⚡</span>
              <span>
                INSTABLE {device.raw_episodes_count}
                <span className="font-normal opacity-75"> coupures → </span>
                {device.episodes_count} épisode{device.episodes_count > 1 ? 's' : ''}
              </span>
            </div>
          )}
        </td>
        <td className={`px-4 py-3 text-sm whitespace-nowrap ${availabilityClass(device.availability_pct)}`}>
          {device.availability_pct.toFixed(3)} %
        </td>
        <td className="px-4 py-3 text-sm font-semibold text-slate-800">{fmtDuration(device.total_downtime_seconds)}</td>
      </tr>

      {open && (
        <tr className="bg-blue-50/30">
          <td></td>
          <td colSpan={6} className="px-4 py-3 space-y-4">

            <Gantt
              windowStartIso={windowStartIso}
              windowEndIso={windowEndIso}
              episodes={device.episodes}
            />

            <div>
              <div className="text-xs font-medium text-blue-600 mb-2">
                Détail des {device.episodes_count} épisode{device.episodes_count > 1 ? 's' : ''} :
              </div>
              <ul className="space-y-1.5">
                {device.episodes.map(ep => <EpisodeLine key={ep.incident_id} episode={ep} />)}
              </ul>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ─── Gantt chart — horizontal timeline with each episode + each raw flap ─
//
// For non-flapping episodes (flap_count == 1) we draw a single solid bar.
// For flapping episodes we draw a hatched "envelope" covering the merged
// span PLUS each raw sub-flap as its own solid bar inside — so unstable
// links look visually distinct (a comb of ticks) from a clean long outage
// of the same duration (one continuous bar).
function Gantt({
  windowStartIso, windowEndIso, episodes,
}: {
  windowStartIso: string
  windowEndIso: string
  episodes: DowntimeEpisode[]
}) {
  const windowStart = new Date(windowStartIso).getTime()
  const windowEnd = new Date(windowEndIso).getTime()
  const windowMs = Math.max(1, windowEnd - windowStart)
  const now = Date.now()

  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => ({
    fraction: f,
    iso: new Date(windowStart + f * windowMs).toISOString(),
  }))

  // Hatched diagonal pattern for the flapping envelope (Tailwind doesn't
  // ship a hatched utility — inline CSS is the simplest way).
  const flapHatchStyle: React.CSSProperties = {
    backgroundImage:
      'repeating-linear-gradient(45deg, rgba(251,146,60,0.35) 0 4px, rgba(251,146,60,0.10) 4px 8px)',
    border: '1px solid rgba(251,146,60,0.6)',
  }

  return (
    <div>
      <div className="text-xs font-medium text-blue-600 mb-2">Chronologie sur la fenêtre :</div>

      <div className="relative h-10 bg-slate-100 border border-slate-200 rounded overflow-hidden">
        {episodes.map(ep => {
          const startMs = new Date(ep.started_at).getTime()
          const endMs = ep.ended_at ? new Date(ep.ended_at).getTime() : now
          const left = Math.max(0, ((startMs - windowStart) / windowMs) * 100)
          const right = Math.min(100, ((endMs - windowStart) / windowMs) * 100)
          const width = Math.max(0.4, right - left)
          const solidCls = ep.severity === 'critical' ? 'bg-red-500' : 'bg-amber-400'
          const baseTitle = (
            `${fmtDateTime(ep.started_at)} → `
            + (ep.is_ongoing ? 'en cours' : fmtDateTime(ep.ended_at!))
            + ` · ${fmtDuration(ep.duration_seconds)} · ${ep.severity}`
          )

          // Non-flapping: single solid block. Done.
          if (ep.flap_count <= 1 || ep.flaps.length === 0) {
            return (
              <div
                key={ep.incident_id}
                style={{ left: `${left}%`, width: `${width}%` }}
                className={`absolute top-1 bottom-1 ${solidCls} hover:opacity-80 rounded-sm cursor-help`}
                title={baseTitle}
              />
            )
          }

          // Flapping: hatched envelope + each sub-flap as a thin solid bar inside.
          return (
            <div key={ep.incident_id}>
              {/* Hatched envelope covering the merged span */}
              <div
                style={{ left: `${left}%`, width: `${width}%`, ...flapHatchStyle }}
                className="absolute top-1 bottom-1 rounded-sm cursor-help"
                title={`${baseTitle} · FLAPPING ×${ep.flap_count} (zone hachurée = enveloppe, traits oranges = chaque coupure individuelle)`}
              />
              {/* Each individual raw flap as a solid tick on top */}
              {ep.flaps.map((f, i) => {
                const fStartMs = new Date(f.started_at).getTime()
                const fEndMs = f.ended_at ? new Date(f.ended_at).getTime() : now
                const fLeft = Math.max(0, ((fStartMs - windowStart) / windowMs) * 100)
                const fRight = Math.min(100, ((fEndMs - windowStart) / windowMs) * 100)
                const fWidth = Math.max(0.15, fRight - fLeft)
                return (
                  <div
                    key={`${ep.incident_id}-flap-${i}`}
                    style={{ left: `${fLeft}%`, width: `${fWidth}%` }}
                    className="absolute top-2 bottom-2 bg-orange-600 hover:bg-orange-700 cursor-help"
                    title={`Coupure ${i + 1}/${ep.flap_count} · ${fmtDateTime(f.started_at)} → ${f.ended_at ? fmtDateTime(f.ended_at) : 'en cours'} · ${fmtDuration(f.duration_seconds)}`}
                  />
                )
              })}
            </div>
          )
        })}
      </div>

      {/* Axe horaire — 5 ticks */}
      <div className="relative h-5 mt-1">
        {ticks.map(t => (
          <div
            key={t.fraction}
            className="absolute -translate-x-1/2 text-[10px] text-blue-400 whitespace-nowrap"
            style={{ left: `${t.fraction * 100}%` }}
          >
            {fmtDateTime(t.iso)}
          </div>
        ))}
      </div>

      {/* Légende couleurs */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-[10px] text-blue-500">
        <span className="flex items-center gap-1">
          <span className="w-3 h-2 bg-red-500 rounded-sm" /> Panne critique
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-2 bg-amber-400 rounded-sm" /> Panne warning
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-2 rounded-sm" style={flapHatchStyle} /> Plage d'instabilité
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-2 bg-orange-600 rounded-sm" /> Chaque coupure individuelle (dans la plage d'instabilité)
        </span>
        <span className="text-blue-300 ml-auto">Survol → détail</span>
      </div>
    </div>
  )
}

function EpisodeLine({ episode: ep }: { episode: DowntimeEpisode }) {
  const startDay = fmtDayMonth(ep.started_at)
  const startTime = fmtTimeOnly(ep.started_at)
  const sameDay = ep.ended_at && isSameDay(ep.started_at, ep.ended_at)
  const dotCls = ep.severity === 'critical' ? 'bg-red-500' : 'bg-amber-400'

  return (
    <li className="flex items-start gap-3 text-sm">
      <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${dotCls}`} />
      <div className="flex-1">
        <div className="text-slate-700">
          <strong>{startDay} {startTime}</strong>
          {' → '}
          {ep.is_ongoing ? (
            <span className="text-red-600 font-semibold">en cours</span>
          ) : sameDay ? (
            <strong>{fmtTimeOnly(ep.ended_at!)}</strong>
          ) : (
            <strong>{fmtDayMonth(ep.ended_at!)} {fmtTimeOnly(ep.ended_at!)}</strong>
          )}
          {ep.flap_count > 1 && (
            <span
              title={`${ep.flap_count} pannes brutes fusionnées sur cette plage`}
              className="ml-2 inline-flex items-center text-[10px] font-semibold px-1.5 py-0.5 rounded bg-orange-50 text-orange-700 border border-orange-200"
            >
              ⚡ instable ×{ep.flap_count}
            </span>
          )}
        </div>
        <div className="text-[11px] text-slate-500">
          Durée : <span className="font-medium">{fmtDuration(ep.duration_seconds)}</span>
          {' · '}
          Sévérité : <span className="font-medium">{severityLabel(ep.severity)}</span>
          {' · '}
          Type : <span className="font-medium">{alertTypeLabel(ep.alert_type)}</span>
          {' · '}
          Incident #{ep.incident_id}
        </div>
      </div>
    </li>
  )
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    up:      { label: 'UP',      cls: 'bg-green-100 text-green-800 border-green-200' },
    down:    { label: 'DOWN',    cls: 'bg-red-100 text-red-800 border-red-200'       },
    unknown: { label: 'INCONNU', cls: 'bg-blue-100 text-blue-700 border-blue-200'    },
  }
  const m = map[status] ?? map.unknown
  return (
    <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full border ${m.cls}`}>
      {m.label}
    </span>
  )
}

function DownloadIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16" />
    </svg>
  )
}

function csvEscape(s: string): string {
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return `"${s.replace(/"/g, '""')}"`
  }
  return s
}
