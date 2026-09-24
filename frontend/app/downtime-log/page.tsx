'use client'

import { useEffect, useMemo, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import DeviceDetailModal from '@/components/DeviceDetailModal'
import { deviceTypeLabel } from '@/lib/types'
import type { Device, DeviceDowntime, DowntimeEpisode, DowntimeLogResponse } from '@/lib/types'

/**
 * Journal des coupures — l'historique DATÉ des pannes d'infrastructure.
 *
 * Répond à la seule question que ni /incidents (qui dit ce qui se passe
 * MAINTENANT) ni les graphes « pannes par site » (qui donnent des cumuls) ne
 * traitent : « AT1 est tombé à quelle heure, et il est revenu quand ? ».
 *
 * ⚠️ **Une panne de SITE est une coupure de son SWITCH**, et c'est la règle
 * déjà appliquée par `fn_site_outage_summary` (migration r6b7c8d9e0f1 : « le
 * nombre de pannes d'un site = le nombre d'épisodes de coupure de son SWITCH
 * parent — s'il est down, le site est down »). Cette page n'invente donc aucun
 * barème : elle montre les épisodes que les graphes du tableau de bord
 * comptent déjà. Compter tous les équipements d'infra gonflerait le chiffre et
 * ferait diverger les deux écrans.
 *
 * ⚠️ **Les abonnés (LR) n'y sont PAS**, et ce n'est pas un oubli : un LR qui
 * tombe est une panne CHEZ LE CLIENT (coupure de courant, débranchement), elle
 * n'ouvre aucun incident — il n'existe donc aucune donnée pour la
 * reconstituer. Ce journal couvre l'infra : switches, Rockets, UISP Power, AF60.
 *
 * ⚠️ **L'historique est intégral** : aucun job de rétention ne purge les
 * incidents, et les incidents de DISPONIBILITÉ sont les seuls conservés en
 * `resolved` au lieu d'être supprimés à leur résolution — précisément pour que
 * ce journal puisse exister.
 */

const REFRESH_MS = 60_000

type Preset = '24h' | '7d' | '30d' | 'custom'

const PRESET_DAYS: Record<Exclude<Preset, 'custom'>, number> = {
  '24h': 1,
  '7d': 7,
  '30d': 30,
}

const PRESET_LABEL: Record<Exclude<Preset, 'custom'>, string> = {
  '24h': '24 heures',
  '7d': '7 jours',
  '30d': '30 jours',
}

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10)
}

/** Durée en secondes → « 2h 08min ». Même barème que les graphes du tableau de bord. */
function fmtDuration(secs: number): string {
  if (secs < 60) return `${Math.round(secs)}s`
  if (secs < 3_600) return `${Math.floor(secs / 60)} min`
  const h = Math.floor(secs / 3_600)
  const m = Math.round((secs % 3_600) / 60)
  return m === 0 ? `${h}h` : `${h}h ${m.toString().padStart(2, '0')}min`
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
}

function fmtDay(iso: string): string {
  return new Date(iso).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })
}

/** « A2 PK1 » → « PK1 » : le préfixe commun à tous les sites n'apporte rien. */
function siteLabel(site: string | null): string {
  if (!site) return 'Sans site'
  return site.replace(/^A2\s+/, '').trim() || site
}

/** Une ligne de site : son (ou ses) switch(es) et le cumul de leurs coupures. */
interface SiteOutage {
  site: string | null
  switches: DeviceDowntime[]
  episodes: number
  downtimeSeconds: number
  /**
   * Pire disponibilité parmi les switches du site — un site à deux switches
   * vaut celui qui est le plus tombé, jamais leur moyenne : moyenner
   * masquerait un switch mort derrière un switch sain.
   */
  availability: number
  /** Une coupure est-elle EN COURS à cet instant ? */
  ongoing: boolean
}

function groupBySite(switches: DeviceDowntime[]): SiteOutage[] {
  const map = new Map<string, SiteOutage>()
  for (const sw of switches) {
    const key = sw.site ?? '\u0000'
    const row = map.get(key) ?? {
      site: sw.site,
      switches: [],
      episodes: 0,
      downtimeSeconds: 0,
      availability: 100,
      ongoing: false,
    }
    row.switches.push(sw)
    row.episodes += sw.episodes_count
    row.downtimeSeconds += sw.total_downtime_seconds
    row.availability = Math.min(row.availability, sw.availability_pct)
    row.ongoing = row.ongoing || sw.episodes.some((e) => e.is_ongoing)
    map.set(key, row)
  }
  // Le plus touché d'abord — c'est là que l'opérateur regarde en premier.
  return [...map.values()].sort(
    (a, b) => b.downtimeSeconds - a.downtimeSeconds || b.episodes - a.episodes,
  )
}

export default function DowntimeLogPage() {
  const [preset, setPreset] = useState<Preset>('7d')
  const today = useMemo(() => isoDay(new Date()), [])
  const [from, setFrom] = useState<string>(today)
  const [to, setTo] = useState<string>(today)
  const [applied, setApplied] = useState<{ from: string; to: string } | null>(null)

  // La fenêtre d'un raccourci court avec l'horloge : sans ce battement, une
  // panne survenue après le chargement de la page ne rentrerait jamais dans la
  // borne haute, et l'écran resterait muet sur la coupure en cours.
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  const { startIso, endIso } = useMemo(() => {
    if (preset === 'custom' && applied) {
      return {
        startIso: new Date(`${applied.from}T00:00:00`).toISOString(),
        endIso: new Date(`${applied.to}T23:59:59.999`).toISOString(),
      }
    }
    const days = PRESET_DAYS[preset === 'custom' ? '7d' : preset]
    const now = new Date()
    return {
      startIso: new Date(now.getTime() - days * 86_400_000).toISOString(),
      endIso: now.toISOString(),
    }
    // `tick` fait volontairement partie des dépendances : il fait glisser la fenêtre.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, applied, tick])

  const { data, isLoading, error } = useSWR<DowntimeLogResponse>(
    endpoints.downtimeLog(startIso, endIso), fetcher,
  )

  // Fiche d'équipement ouverte SUR PLACE (sans redirection vers /sites).
  const [selected, setSelected] = useState<Device | null>(null)
  const [deviceLoading, setDeviceLoading] = useState(false)
  const openDevice = async (id: number) => {
    setDeviceLoading(true)
    try { setSelected(await fetcher(endpoints.device(id))) }
    catch { /* équipement introuvable : on n'ouvre pas la fiche */ }
    finally { setDeviceLoading(false) }
  }

  const items = useMemo(() => data?.items ?? [], [data])
  const switches = useMemo(
    () => items.filter((d) => d.device_type === 'uisp_switch'), [items],
  )
  const others = useMemo(
    () => items.filter((d) => d.device_type !== 'uisp_switch'), [items],
  )
  const sites = useMemo(() => groupBySite(switches), [switches])

  const totalEpisodes = sites.reduce((a, s) => a + s.episodes, 0)
  const totalDowntime = sites.reduce((a, s) => a + s.downtimeSeconds, 0)
  const ongoingCount = sites.filter((s) => s.ongoing).length

  return (
    <>
      <div className="space-y-6">
        <header>
          <h1 className="text-2xl font-bold text-blue-950">Journal des coupures</h1>
          <p className="mt-1 text-sm text-slate-600">
            Quand chaque site est tombé, quand il est revenu, et combien de temps il est
            resté coupé. Une panne de site est une coupure de son <strong>switch</strong> :
            s&apos;il tombe, le site tombe.
          </p>
        </header>

        {/* ── Fenêtre d'observation ─────────────────────────────────────── */}
        <div className="bg-white rounded-xl border border-slate-200 p-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {(['24h', '7d', '30d'] as const).map((p) => (
              <button
                key={p}
                onClick={() => setPreset(p)}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${
                  preset === p
                    ? 'bg-blue-600 text-white'
                    : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                }`}
              >
                {PRESET_LABEL[p]}
              </button>
            ))}
            <span className="w-px h-6 bg-slate-200 mx-1" />
            <label className="text-sm text-slate-600">du</label>
            <input
              type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)}
              className="px-2 py-1.5 rounded-lg border border-slate-300 text-sm"
            />
            <label className="text-sm text-slate-600">au</label>
            <input
              type="date" value={to} min={from} max={today} onChange={(e) => setTo(e.target.value)}
              className="px-2 py-1.5 rounded-lg border border-slate-300 text-sm"
            />
            <button
              onClick={() => { setApplied({ from, to }); setPreset('custom') }}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${
                preset === 'custom'
                  ? 'bg-blue-600 text-white'
                  : 'bg-slate-800 text-white hover:bg-slate-900'
              }`}
            >
              Appliquer
            </button>
          </div>
          <p className="text-xs text-slate-500">
            Période analysée : du {fmtDay(startIso)} {fmtTime(startIso)} au{' '}
            {fmtDay(endIso)} {fmtTime(endIso)}
            {data != null && data.merge_gap_seconds > 0 && (
              <>
                {' '}· deux coupures séparées de moins de{' '}
                {Math.round(data.merge_gap_seconds / 60)} min comptent pour une seule
                panne (instabilité)
              </>
            )}
          </p>
        </div>

        <WindowReportCard today={today} />

        {/* ── Chiffres de la période ────────────────────────────────────── */}
        {!isLoading && !error && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Tile label="Sites touchés" value={String(sites.length)} />
            <Tile label="Pannes" value={String(totalEpisodes)} />
            <Tile label="Temps de coupure cumulé" value={fmtDuration(totalDowntime)} />
            <Tile
              label="Coupures en cours"
              value={String(ongoingCount)}
              tone={ongoingCount > 0 ? 'danger' : 'ok'}
            />
          </div>
        )}

        {isLoading && (
          <div className="bg-white rounded-xl border border-slate-200 p-10 text-center text-slate-500 text-sm">
            Chargement du journal…
          </div>
        )}

        {error != null && (
          <div className="bg-red-50 rounded-xl border border-red-200 p-6 text-sm text-red-800">
            Le journal n&apos;a pas pu être chargé.
          </div>
        )}

        {/* ── Le journal ────────────────────────────────────────────────── */}
        {!isLoading && !error && sites.length === 0 && (
          <div className="bg-white rounded-xl border border-slate-200 p-10 text-center">
            <p className="text-slate-700 font-medium">
              Aucun site n&apos;a été coupé sur la période.
            </p>
            <p className="mt-1 text-sm text-slate-500">
              Aucun switch n&apos;a cessé de répondre entre le {fmtDay(startIso)} et le{' '}
              {fmtDay(endIso)}.
            </p>
          </div>
        )}

        {sites.map((site) => (
          <SiteCard key={site.site ?? 'none'} site={site} onOpenDevice={openDevice} />
        ))}

        {/* ⚠️ Un site dont le SWITCH n'est pas tombé n'apparaît pas au-dessus,
            même si un de ses Rockets est resté mort quatre heures. Taire ces
            coupures ferait lire « aucune panne » là où un secteur entier était
            hors service — un négatif faux. On les nomme donc, sans les compter
            dans les chiffres ci-dessus, qui restent ceux des switches. */}
        {others.length > 0 && <OtherDevices devices={others} onOpenDevice={openDevice} />}
      </div>

      {deviceLoading && selected == null && (
        <div className="fixed inset-0 bg-blue-900/30 backdrop-blur-sm z-50 flex items-center justify-center animate-fade-in">
          <div className="bg-white rounded-xl shadow-2xl px-6 py-5 flex items-center gap-3">
            <span className="w-5 h-5 rounded-full border-2 border-blue-200 border-t-blue-600 animate-spin shrink-0" />
            <span className="text-sm font-medium text-blue-900">Chargement de l&apos;équipement…</span>
          </div>
        </div>
      )}

      <DeviceDetailModal device={selected} onClose={() => setSelected(null)} onNavigate={setSelected} />
    </>
  )
}

function Tile({ label, value, tone = 'neutral' }: {
  label: string; value: string; tone?: 'neutral' | 'ok' | 'danger'
}) {
  const color = tone === 'danger' ? 'text-red-600'
    : tone === 'ok' ? 'text-emerald-600'
      : 'text-blue-950'
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <div className={`text-2xl font-bold ${color}`}>{value}</div>
      <div className="mt-0.5 text-xs text-slate-500">{label}</div>
    </div>
  )
}

function SiteCard({ site, onOpenDevice }: {
  site: SiteOutage; onOpenDevice: (id: number) => void
}) {
  const multiSwitch = site.switches.length > 1
  return (
    <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-slate-100 flex flex-wrap items-center gap-x-4 gap-y-1.5">
        <h2 className="text-lg font-bold text-blue-950">{siteLabel(site.site)}</h2>
        {site.ongoing && (
          <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-red-100 text-red-700">
            COUPURE EN COURS
          </span>
        )}
        <span className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          <span className="text-slate-600">
            <strong className="text-blue-950">{site.episodes}</strong>{' '}
            {site.episodes > 1 ? 'pannes' : 'panne'}
          </span>
          <span className="text-slate-600">
            coupé <strong className="text-blue-950">{fmtDuration(site.downtimeSeconds)}</strong>
          </span>
          <span className={site.availability >= 99 ? 'text-emerald-600' : 'text-amber-600'}>
            dispo <strong>{site.availability.toFixed(2)} %</strong>
          </span>
        </span>
      </div>

      {site.switches.map((sw) => (
        <div key={sw.device_id}>
          {multiSwitch && (
            <div className="px-5 pt-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">
              {sw.device_name}
            </div>
          )}
          <ul className="divide-y divide-slate-100">
            {sw.episodes.map((ep) => (
              <EpisodeRow key={ep.incident_id} ep={ep} />
            ))}
          </ul>
          <div className="px-5 py-2.5 bg-slate-50/60 text-xs text-slate-500 flex flex-wrap items-center gap-x-3 gap-y-1">
            <button
              onClick={() => onOpenDevice(sw.device_id)}
              className="font-medium text-blue-600 hover:text-blue-800 hover:underline"
            >
              {sw.device_name}
            </button>
            <span>{sw.device_ip}</span>
            <span>· plus longue coupure {fmtDuration(sw.longest_episode_seconds)}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

function EpisodeRow({ ep }: { ep: DowntimeEpisode }) {
  const start = new Date(ep.started_at)
  const end = ep.ended_at != null ? new Date(ep.ended_at) : null
  // Une coupure peut enjamber minuit : on ne répète la date du retour que
  // lorsqu'elle diffère, sinon la ligne se lit deux fois pour rien.
  const sameDay = end != null && start.toDateString() === end.toDateString()

  return (
    <li className="px-5 py-3 flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
      <span className="text-sm text-slate-500 tabular-nums w-14 shrink-0">
        {fmtDay(ep.started_at)}
      </span>

      <span className="text-sm text-slate-900">
        tombé <strong className="tabular-nums">{fmtTime(ep.started_at)}</strong>
      </span>

      <span className="text-slate-300">→</span>

      {ep.is_ongoing || ep.ended_at == null ? (
        <span className="text-sm font-semibold text-red-600">toujours coupé</span>
      ) : (
        <span className="text-sm text-slate-900">
          revenu <strong className="tabular-nums">{fmtTime(ep.ended_at)}</strong>
          {!sameDay && <span className="text-slate-500"> le {fmtDay(ep.ended_at)}</span>}
        </span>
      )}

      <span
        className={`ml-auto px-2 py-0.5 rounded-md text-sm font-semibold tabular-nums ${
          ep.is_ongoing ? 'bg-red-100 text-red-700' : 'bg-slate-100 text-slate-700'
        }`}
      >
        {fmtDuration(ep.duration_seconds)}
      </span>

      {/* ⚠️ Plusieurs cycles fusionnés : l'équipement n'est pas tombé une fois,
          il a battu. Le dire change le geste (un lien instable ne se répare pas
          comme une panne franche). */}
      {ep.flap_count > 1 && (
        <span
          className="px-2 py-0.5 rounded-md text-xs font-semibold bg-amber-100 text-amber-800"
          title={`${ep.flap_count} coupures rapprochées fusionnées en une seule panne`}
        >
          instable · {ep.flap_count} cycles
        </span>
      )}
    </li>
  )
}

/** Les équipements NON-switch tombés sur la période — repliés, jamais tus. */
function OtherDevices({ devices, onOpenDevice }: {
  devices: DeviceDowntime[]; onOpenDevice: (id: number) => void
}) {
  const [open, setOpen] = useState(false)
  const plural = devices.length > 1 ? 's' : ''
  return (
    <div className="bg-white rounded-xl border border-slate-200">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full px-5 py-3.5 flex flex-wrap items-center gap-2 text-left hover:bg-slate-50 transition rounded-xl"
      >
        <span className={`text-slate-400 transition-transform ${open ? 'rotate-90' : ''}`}>›</span>
        <span className="text-sm font-semibold text-blue-950">
          {devices.length} autre{plural} équipement{plural} coupé{plural} sur la période
        </span>
        <span className="text-xs text-slate-500">
          (Rockets, UISP Power, AF60 — hors du décompte des sites ci-dessus)
        </span>
      </button>

      {open && (
        <div className="border-t border-slate-100 divide-y divide-slate-100">
          {devices.map((d) => (
            <div key={d.device_id} className="px-5 py-3">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <button
                  onClick={() => onOpenDevice(d.device_id)}
                  className="text-sm font-medium text-blue-600 hover:text-blue-800 hover:underline"
                >
                  {d.device_name}
                </button>
                <span className="text-xs text-slate-500">{deviceTypeLabel(d.device_type)}</span>
                <span className="text-xs text-slate-500">{siteLabel(d.site)}</span>
                <span className="ml-auto text-sm text-slate-600">
                  {d.episodes_count} {d.episodes_count > 1 ? 'pannes' : 'panne'} ·{' '}
                  {fmtDuration(d.total_downtime_seconds)}
                </span>
              </div>
              <ul className="mt-1.5 divide-y divide-slate-100">
                {d.episodes.map((ep) => <EpisodeRow key={ep.incident_id} ep={ep} />)}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Toutes les heures de la journée, pour les deux sélecteurs de la tranche. */
const HOURS = Array.from({ length: 24 }, (_, h) => h)

function hh(h: number): string {
  return `${h.toString().padStart(2, '0')}:00`
}

function filenameFromResponse(res: Response, fallback: string): string {
  const header = res.headers.get('content-disposition') ?? ''
  const match = /filename="?([^";]+)"?/i.exec(header)
  return match?.[1] ?? fallback
}

/**
 * Rapport PDF des coupures de TOUS les sites sur une tranche horaire répétée
 * chaque jour de la période (00 h → 08 h par défaut, réglable).
 *
 * ⚠️ Règle de comptage (côté serveur, `night_outage_report_service`) : seules
 * les coupures qui COMMENCENT dans la tranche sont comptées, jusqu'à la fin de
 * la tranche. Elle est rappelée ici pour qu'on ne s'étonne pas qu'une coupure
 * de 23 h 30 manque au rapport 00 h → 08 h.
 *
 * `fetch` + blob plutôt qu'un lien nu : un échec (période trop longue…) doit
 * s'afficher ici, pas s'ouvrir en JSON brut dans un onglet.
 */
function WindowReportCard({ today }: { today: string }) {
  const weekAgo = useMemo(() => isoDay(new Date(Date.now() - 6 * 86_400_000)), [])
  const [from, setFrom] = useState(weekAgo)
  const [to, setTo] = useState(today)
  const [fromHour, setFromHour] = useState(0)
  const [toHour, setToHour] = useState(8)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const crossesMidnight = toHour < fromHour
  const invalid = fromHour === toHour || from > to

  const download = async () => {
    setBusy(true)
    setErr(null)
    try {
      const res = await fetch(endpoints.windowOutageReportPdf(from, to, fromHour, toHour))
      if (!res.ok) {
        let detail = `HTTP ${res.status}`
        try {
          const body = await res.json()
          if (typeof body?.detail === 'string') detail = body.detail
        } catch {
          /* réponse non JSON : on garde le code HTTP */
        }
        throw new Error(detail)
      }
      const url = URL.createObjectURL(await res.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = filenameFromResponse(res, 'coupures-par-tranche.pdf')
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 space-y-3">
      <div>
        <h2 className="text-sm font-semibold text-blue-950">Rapport PDF par tranche horaire</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          Les coupures de tous les sites dans une même tranche horaire, chaque jour de la
          période. Seules les coupures qui <strong>commencent</strong> dans la tranche sont
          comptées, jusqu&apos;à la fin de la tranche. Heures UTC (= heure de Mauritanie).
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-sm text-slate-600">du</label>
        <input
          type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)}
          className="px-2 py-1.5 rounded-lg border border-slate-300 text-sm"
        />
        <label className="text-sm text-slate-600">au</label>
        <input
          type="date" value={to} min={from} max={today} onChange={(e) => setTo(e.target.value)}
          className="px-2 py-1.5 rounded-lg border border-slate-300 text-sm"
        />
        <span className="w-px h-6 bg-slate-200 mx-1" />
        <label className="text-sm text-slate-600">de</label>
        <select
          value={fromHour} onChange={(e) => setFromHour(Number(e.target.value))}
          className="px-2 py-1.5 rounded-lg border border-slate-300 text-sm"
        >
          {HOURS.map((h) => <option key={h} value={h}>{hh(h)}</option>)}
        </select>
        <label className="text-sm text-slate-600">à</label>
        <select
          value={toHour} onChange={(e) => setToHour(Number(e.target.value))}
          className="px-2 py-1.5 rounded-lg border border-slate-300 text-sm"
        >
          {HOURS.map((h) => <option key={h} value={h}>{hh(h)}</option>)}
        </select>
        <button
          onClick={download}
          disabled={busy || invalid}
          className="px-3 py-1.5 rounded-lg text-sm font-medium bg-slate-800 text-white hover:bg-slate-900 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {busy ? 'Génération…' : 'Télécharger le PDF'}
        </button>
      </div>
      {fromHour === toHour && (
        <p className="text-xs text-amber-700">La tranche est vide : choisissez deux heures différentes.</p>
      )}
      {crossesMidnight && (
        <p className="text-xs text-slate-500">
          La tranche enjambe minuit : chaque nuit va de {hh(fromHour)} le jour J à{' '}
          {hh(toHour)} le lendemain.
        </p>
      )}
      {err != null && <p className="text-xs text-red-700">Le rapport n&apos;a pas pu être généré : {err}</p>}
    </div>
  )
}
