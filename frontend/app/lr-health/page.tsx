'use client'

import Link from 'next/link'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type {
  BadInstallationRow,
  BadInstallationVerdict,
  LiveLinkHealthResponse,
  SignalEvidence,
} from '@/lib/types'
import { LR_MODEL_VARIANT_LABELS, VERDICT_LABELS } from '@/lib/types'

const VERDICT_GROUPS: BadInstallationVerdict[] = ['critical', 'suspect']

const VERDICT_HEADER: Record<BadInstallationVerdict, string> = {
  critical: 'bg-red-50 border-red-200 text-red-700',
  suspect:  'bg-orange-50 border-orange-200 text-orange-700',
}

const VERDICT_BADGE: Record<BadInstallationVerdict, string> = {
  critical: 'bg-red-100 text-red-800 border-red-300',
  suspect:  'bg-orange-100 text-orange-800 border-orange-300',
}

function fmt(value: number | null | undefined, suffix: string, digits = 0): string {
  if (value === null || value === undefined) return '—'
  return `${value.toFixed(digits)}${suffix}`
}

// Signal — seuil plat (warning ≤ -75 dBm par défaut). Critique ~10 dB plus bas.
function signalClass(dbm: number | null, warningThreshold: number): string {
  if (dbm === null) return 'text-blue-300'
  if (dbm <= warningThreshold - 10) return 'text-red-600 font-semibold'
  if (dbm <= warningThreshold)      return 'text-amber-600 font-medium'
  return 'text-slate-700'
}
// Potentiel du lien — plancher par famille (LTU 50 % / airMAX 40 %).
function potentialClass(v: number | null, floor: number): string {
  if (v === null) return 'text-blue-300'
  if (v < floor) return 'text-red-600 font-semibold'
  if (v < floor + 10) return 'text-amber-600 font-medium'
  return 'text-slate-700'
}
// Capacité totale — plancher fixe (Mbps).
function capacityClass(v: number | null, floor: number): string {
  if (v === null) return 'text-blue-300'
  if (v < floor) return 'text-red-600 font-semibold'
  if (v < floor + 15) return 'text-amber-600 font-medium'
  return 'text-slate-700'
}
// Débit RX (local/distant) — plancher par famille (LTU ×6 / airMAX ×6 warn, ×4 crit).
function rateClass(v: number | null, floor: number): string {
  if (v === null) return 'text-blue-300'
  if (v < floor) return 'text-red-600 font-semibold'
  if (v < floor + 2) return 'text-amber-600 font-medium'
  return 'text-slate-700'
}
// Latence LR → Internet (ms). Le seuil critique miroite LR_LATENCY_CRITICAL_MS
// (défaut 100 ms) ; warning au-delà de la moitié. Affichage seul, hors verdict.
function latencyClass(ms: number | null): string {
  if (ms === null) return 'text-blue-300'
  if (ms >= 100) return 'text-red-600 font-semibold'
  if (ms >= 50) return 'text-amber-600 font-medium'
  return 'text-slate-700'
}

export default function LrHealthPage() {
  const { data, isLoading } = useSWR<LiveLinkHealthResponse>(
    endpoints.badInstallations,
    fetcher,
    { refreshInterval: 60_000 },
  )

  const items: BadInstallationRow[] = data?.items ?? []
  const unreachable = data?.unreachable_count ?? 0
  const groups = VERDICT_GROUPS
    .map(v => ({ verdict: v, items: items.filter(i => i.verdict === v) }))
    .filter(g => g.items.length > 0)

  return (
    <div className="space-y-6">

      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Liaisons clients</h1>
          <p className="text-blue-400 text-sm mt-1">
            État <strong>actuel</strong> des LR clients — interrogés en direct à l'ouverture —
            classés par 5 indicateurs de niveau indépendants. Seuls les LR avec ≥ 3 indicateurs
            actifs sont surfacés.
          </p>
          {unreachable > 0 && (
            <p className="text-amber-500 text-xs mt-1">
              {unreachable} LR injoignable{unreachable > 1 ? 's' : ''} au moment de la lecture —
              exclu{unreachable > 1 ? 's' : ''} de la liste.
            </p>
          )}
        </div>
      </div>

      <details className="bg-white border border-blue-100 rounded-xl shadow-sm">
        <summary className="cursor-pointer px-4 py-3 text-sm text-blue-700 font-medium hover:bg-blue-50">
          Comment le verdict est calculé
        </summary>
        <div className="px-4 pb-4 text-xs text-slate-600 space-y-3">
          <p>
            On interroge chaque LR client <strong>en direct</strong> à l'ouverture de la page
            (LTU via le Rocket parent, airMAX via airOS) et on l'évalue sur son
            <strong> état actuel</strong> avec <strong>5 indicateurs de niveau indépendants</strong>.
            Chaque indicateur compare la <strong>valeur actuelle</strong> de la métrique à un
            plancher : sous le plancher = <em>actif</em>. Un LR <strong>injoignable</strong> au
            moment de la lecture est <em>exclu</em> de la liste (aucun repli sur d'anciennes mesures).
          </p>

          <table className="w-full text-[11px] border-collapse mt-2">
            <thead>
              <tr className="bg-blue-50">
                <th className="text-left px-2 py-1 border-b">Indicateur</th>
                <th className="text-left px-2 py-1 border-b">LTU — actif si valeur actuelle…</th>
                <th className="text-left px-2 py-1 border-b">airMAX — actif si valeur actuelle…</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              <tr>
                <td className="px-2 py-1 align-top">
                  <strong>Signal dBm</strong>
                  <div className="text-slate-400">puissance reçue (seuil plat)</div>
                </td>
                <td className="px-2 py-1 align-top" colSpan={2}>
                  est <strong>≤ -75 dBm</strong> (configurable via SIGNAL_WARNING_DBM)
                </td>
              </tr>
              <tr>
                <td className="px-2 py-1 align-top">
                  <strong>Potentiel du lien</strong>
                  <div className="text-slate-400">moyenne des linkScore DL/UL (100 % = idéal)</div>
                </td>
                <td className="px-2 py-1 align-top">est <strong>&lt; 50 %</strong></td>
                <td className="px-2 py-1 align-top">est <strong>&lt; 40 %</strong></td>
              </tr>
              <tr>
                <td className="px-2 py-1 align-top">
                  <strong>Capacité totale</strong>
                  <div className="text-slate-400">débit total réel du lien (DL + UL)</div>
                </td>
                <td className="px-2 py-1 align-top" colSpan={2}>est <strong>&lt; 60 Mbps</strong></td>
              </tr>
              <tr>
                <td className="px-2 py-1 align-top">
                  <strong>Débit RX local</strong>
                  <div className="text-slate-400">multiplicateur de modulation RX local (1×…12×)</div>
                </td>
                <td className="px-2 py-1 align-top">est <strong>&lt; ×6</strong></td>
                <td className="px-2 py-1 align-top">est <strong>&lt; ×6</strong> (critique &lt; ×4)</td>
              </tr>
              <tr>
                <td className="px-2 py-1 align-top">
                  <strong>Débit RX distant</strong>
                  <div className="text-slate-400">multiplicateur de modulation RX distant (1×…12×)</div>
                </td>
                <td className="px-2 py-1 align-top">est <strong>&lt; ×6</strong></td>
                <td className="px-2 py-1 align-top">est <strong>&lt; ×6</strong> (critique &lt; ×4)</td>
              </tr>
            </tbody>
          </table>

          <p className="pt-1">
            <strong>Verdict final</strong> — selon le nombre d'indicateurs actifs (sur 5) :
          </p>
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="bg-blue-50">
                <th className="text-left px-2 py-1 border-b">Indicateurs actifs</th>
                <th className="text-left px-2 py-1 border-b">Verdict</th>
                <th className="text-left px-2 py-1 border-b">Affichage</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              <tr><td className="px-2 py-1">0 – 2</td><td className="px-2 py-1">Stable</td><td className="px-2 py-1 text-slate-500">N'apparaît pas dans la liste</td></tr>
              <tr><td className="px-2 py-1">3</td><td className="px-2 py-1"><strong>Suspect</strong></td><td className="px-2 py-1">Bloc orange</td></tr>
              <tr><td className="px-2 py-1">4 – 5</td><td className="px-2 py-1"><strong>Critique</strong></td><td className="px-2 py-1">Bloc rouge</td></tr>
            </tbody>
          </table>
        </div>
      </details>

      {isLoading ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center text-blue-300 shadow-sm">
          Chargement…
        </div>
      ) : items.length === 0 ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm">
          <p className="text-green-600 font-semibold text-sm">✓ Toutes les liaisons clients sont actuellement stables</p>
          <p className="text-blue-400 text-xs mt-1">Aucun LR joignable n'a ≥ 3 indicateurs actifs en ce moment</p>
        </div>
      ) : (
        <div className="space-y-4">
          {groups.map(({ verdict, items: rows }) => (
            <div key={verdict} className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">

              <div className={`flex items-center gap-2 px-4 py-2.5 border-b ${VERDICT_HEADER[verdict]}`}>
                <span className="text-xs font-bold uppercase tracking-widest">{VERDICT_LABELS[verdict]}</span>
                <span className="ml-auto text-xs font-semibold opacity-70">
                  {rows.length} LR{rows.length > 1 ? 's' : ''}
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-blue-50 border-b border-blue-100">
                    <tr>
                      {['Client', 'Rocket / distance', 'Verdict', 'Indicateurs actifs', 'Métriques actuelles', ''].map(h => (
                        <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-blue-50">
                    {rows.map(row => (
                      <tr key={row.lr_id} className="hover:bg-blue-50/60 transition-colors align-top">

                        <td className="px-4 py-3">
                          <div className="text-slate-800 font-medium">{row.lr_name}</div>
                          <div className="text-blue-300 font-mono text-[11px]">{row.lr_ip}</div>
                          <div className="text-blue-400 text-[11px]">
                            {LR_MODEL_VARIANT_LABELS[row.model_variant] ?? row.model_variant}
                          </div>
                        </td>

                        <td className="px-4 py-3 text-xs whitespace-nowrap">
                          <div className="text-slate-700">{row.rocket_name ?? <span className="text-blue-300">— sans parent —</span>}</div>
                          <div className="text-blue-400">
                            {row.distance_m !== null ? `${Math.round(row.distance_m)} m` : ''}
                          </div>
                        </td>

                        <td className="px-4 py-3 whitespace-nowrap">
                          <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${VERDICT_BADGE[verdict]}`}>
                            {row.active_signals_count}/5 indicateurs
                          </span>
                        </td>

                        <td className="px-4 py-3">
                          <div className="flex flex-col gap-1 max-w-[400px]">
                            {row.signals.map(s => <SignalPill key={s.key} signal={s} />)}
                          </div>
                        </td>

                        <td className="px-4 py-3 text-xs whitespace-nowrap">
                          <div className={signalClass(row.latest_signal_dbm, row.signal_warning_threshold)}
                               title={`Seuil signal : ${row.signal_warning_threshold.toFixed(0)} dBm`}>
                            Signal {fmt(row.latest_signal_dbm, ' dBm')}
                          </div>
                          <div className={potentialClass(row.latest_link_potential_pct, row.link_potential_floor_pct)}
                               title={`Plancher (${LR_MODEL_VARIANT_LABELS[row.model_variant] ?? row.model_variant}) : ${row.link_potential_floor_pct.toFixed(0)} %`}>
                            Potentiel {fmt(row.latest_link_potential_pct, ' %')}
                          </div>
                          <div className={capacityClass(row.latest_total_capacity_mbps, row.total_capacity_floor_mbps)}
                               title={`Plancher : ${row.total_capacity_floor_mbps.toFixed(0)} Mbps`}>
                            Capacité {fmt(row.latest_total_capacity_mbps, ' Mbps', 1)}
                          </div>
                          <div className={rateClass(row.latest_local_rx_rate_idx, row.rx_rate_floor_idx)}
                               title={`Plancher : ×${row.rx_rate_floor_idx.toFixed(0)}`}>
                            RX local {row.latest_local_rx_rate_idx !== null ? `×${row.latest_local_rx_rate_idx.toFixed(0)}` : '—'}
                          </div>
                          <div className={rateClass(row.latest_remote_rx_rate_idx, row.rx_rate_floor_idx)}
                               title={`Plancher : ×${row.rx_rate_floor_idx.toFixed(0)}`}>
                            RX distant {row.latest_remote_rx_rate_idx !== null ? `×${row.latest_remote_rx_rate_idx.toFixed(0)}` : '—'}
                          </div>
                          <div className={latencyClass(row.latency_ms)}
                               title="RTT LR → Internet (8.8.8.8), dernier relevé de la sonde SSH (≤ 60 s)">
                            Latence {row.latency_ms !== null ? `${row.latency_ms.toFixed(0)} ms` : '—'}
                          </div>
                        </td>

                        <td className="px-4 py-3 whitespace-nowrap">
                          <Link
                            href="/sites"
                            className="text-xs font-medium text-blue-600 hover:text-blue-800 hover:underline"
                          >
                            Voir l'équipement →
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SignalPill({ signal }: { signal: SignalEvidence }) {
  const cls = signal.active
    ? 'bg-red-50 text-red-700 border-red-200'
    : 'bg-slate-50 text-slate-400 border-slate-200'
  return (
    <span
      title={signal.detail}
      className={`inline-flex items-center gap-1.5 text-[11px] px-2 py-0.5 rounded border ${cls}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${signal.active ? 'bg-red-500' : 'bg-slate-300'}`} />
      <strong className="font-semibold">{signal.label}</strong>
      <span className="opacity-80">— {signal.value}</span>
    </span>
  )
}
