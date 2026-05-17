'use client'

import Link from 'next/link'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type {
  BadInstallationRow,
  BadInstallationVerdict,
  BadInstallationsResponse,
  SignalEvidence,
} from '@/lib/types'
import { LR_MODEL_VARIANT_LABELS, VERDICT_LABELS } from '@/lib/types'

const VERDICT_GROUPS: BadInstallationVerdict[] = ['critical', 'suspect', 'watch']

const VERDICT_HEADER: Record<BadInstallationVerdict, string> = {
  critical: 'bg-red-50 border-red-200 text-red-700',
  suspect:  'bg-orange-50 border-orange-200 text-orange-700',
  watch:    'bg-amber-50 border-amber-200 text-amber-700',
}

const VERDICT_BADGE: Record<BadInstallationVerdict, string> = {
  critical: 'bg-red-100 text-red-800 border-red-300',
  suspect:  'bg-orange-100 text-orange-800 border-orange-300',
  watch:    'bg-amber-100 text-amber-800 border-amber-300',
}

function fmt(value: number | null, suffix: string, digits = 0): string {
  if (value === null || value === undefined) return '—'
  return `${value.toFixed(digits)}${suffix}`
}

function signalClass(dbm: number | null, warningThreshold: number): string {
  if (dbm === null) return 'text-blue-300'
  if (dbm <= warningThreshold - 10) return 'text-red-600 font-semibold'  // critical band ~10 dB below warn
  if (dbm <= warningThreshold)      return 'text-amber-600 font-medium'
  return 'text-slate-700'
}
function noiseClass(dbm: number | null): string {
  if (dbm === null) return 'text-blue-300'
  if (dbm >= -75) return 'text-red-600 font-semibold'
  if (dbm >= -85) return 'text-amber-600 font-medium'
  return 'text-slate-700'
}
function ccqClass(pct: number | null): string {
  if (pct === null) return 'text-blue-300'
  if (pct < 50) return 'text-red-600 font-semibold'
  if (pct < 75) return 'text-amber-600 font-medium'
  return 'text-slate-700'
}

export default function LrHealthPage() {
  const { data, isLoading } = useSWR<BadInstallationsResponse>(
    endpoints.badInstallations(30),
    fetcher,
    { refreshInterval: 60_000 },
  )

  const items: BadInstallationRow[] = data?.items ?? []
  const groups = VERDICT_GROUPS
    .map(v => ({ verdict: v, items: items.filter(i => i.verdict === v) }))
    .filter(g => g.items.length > 0)

  return (
    <div className="space-y-6">

      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Liaisons clients</h1>
          <p className="text-blue-400 text-sm mt-1">
            Classification du comportement des LR clients sur 30 jours par 10 indicateurs indépendants —
            seuls les LR avec ≥ 3 indicateurs actifs sont surfacés.
          </p>
        </div>
      </div>

      <details className="bg-white border border-blue-100 rounded-xl shadow-sm">
        <summary className="cursor-pointer px-4 py-3 text-sm text-blue-700 font-medium hover:bg-blue-50">
          Comment le verdict est calculé
        </summary>
        <div className="px-4 pb-4 text-xs text-slate-600 space-y-3">
          <p>
            On évalue chaque LR client sur les <strong>30 derniers jours</strong> avec <strong>10 indicateurs indépendants</strong>.
            Chaque indicateur renvoie <em>actif</em> (problème détecté) ou <em>inactif</em> (rien à signaler).
            Le nombre d'indicateurs actifs détermine le verdict.
          </p>

          <p>
            <strong>Composition des 10 indicateurs :</strong>
          </p>
          <ul className="list-disc list-inside space-y-0.5 ml-2">
            <li><strong>4 métriques physiques × 2 angles d'analyse = 8 indicateurs</strong> (détail dans le tableau ci-dessous)</li>
            <li><strong>+ 2 indicateurs de comparaison aux voisins</strong> du même Rocket</li>
          </ul>

          <p className="mt-2">
            Pour chacune des 4 métriques, deux indicateurs distincts sont calculés :
          </p>
          <ul className="list-disc list-inside space-y-0.5 ml-2">
            <li><strong>État</strong> — niveau moyen mauvais sur la fenêtre 30 jours ?</li>
            <li><strong>Tendance</strong> — la métrique se dégrade-t-elle dans le temps (pente de régression linéaire) ?</li>
          </ul>

          <table className="w-full text-[11px] border-collapse mt-2">
            <thead>
              <tr className="bg-blue-50">
                <th className="text-left px-2 py-1 border-b">Métrique</th>
                <th className="text-left px-2 py-1 border-b">Indicateur « État » actif si…</th>
                <th className="text-left px-2 py-1 border-b">Indicateur « Tendance » actif si…</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              <tr>
                <td className="px-2 py-1 align-top">
                  <strong>Signal dBm</strong>
                  <div className="text-slate-400">puissance reçue (plus c'est proche de 0, mieux c'est)</div>
                </td>
                <td className="px-2 py-1 align-top">moyenne 30j <strong>≤ seuil dépendant de la distance</strong> (voir grille ci-dessous)</td>
                <td className="px-2 py-1 align-top">le signal <strong>baisse de ≥ 1 dBm par semaine</strong></td>
              </tr>
              <tr>
                <td className="px-2 py-1 align-top">
                  <strong>Bruit</strong>
                  <div className="text-slate-400">plancher de bruit RF (plus c'est négatif, mieux c'est)</div>
                </td>
                <td className="px-2 py-1 align-top">moyenne 30j <strong>≥ -85 dBm</strong> (bruit trop élevé)</td>
                <td className="px-2 py-1 align-top">le bruit <strong>monte de ≥ 1 dBm par semaine</strong></td>
              </tr>
              <tr>
                <td className="px-2 py-1 align-top">
                  <strong>CCQ</strong>
                  <div className="text-slate-400">qualité de la modulation (100 % = idéal)</div>
                </td>
                <td className="px-2 py-1 align-top">moyenne 30j <strong>&lt; 75 %</strong></td>
                <td className="px-2 py-1 align-top">le CCQ <strong>chute de ≥ 2 points par semaine</strong></td>
              </tr>
              <tr>
                <td className="px-2 py-1 align-top">
                  <strong>Disponibilité</strong>
                  <div className="text-slate-400">temps où le LR a répondu au ping</div>
                </td>
                <td className="px-2 py-1 align-top"><strong>downtime &gt; 1 %</strong> de la fenêtre 30j (≈ 7,2 h cumulées)</td>
                <td className="px-2 py-1 align-top"><strong>≥ 5 pannes distinctes</strong> sur la fenêtre (instabilité récurrente)</td>
              </tr>
            </tbody>
          </table>

          <p className="pt-1">
            <strong>Grille du seuil « Signal dBm » par distance</strong> — plus le client est loin, plus on tolère un signal faible
            (la perte de propagation suit la loi en 20·log d) :
          </p>
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="bg-blue-50">
                <th className="text-left px-2 py-1 border-b">Distance LR ↔ Rocket</th>
                <th className="text-left px-2 py-1 border-b">Seuil « État Signal » actif si moyenne ≤</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              <tr><td className="px-2 py-1">&lt; 1 km</td><td className="px-2 py-1"><strong>-55 dBm</strong></td></tr>
              <tr><td className="px-2 py-1">1 – 3 km</td><td className="px-2 py-1"><strong>-62 dBm</strong></td></tr>
              <tr><td className="px-2 py-1">3 – 7 km</td><td className="px-2 py-1"><strong>-68 dBm</strong></td></tr>
              <tr><td className="px-2 py-1">7 – 12 km</td><td className="px-2 py-1"><strong>-73 dBm</strong></td></tr>
              <tr><td className="px-2 py-1">&gt; 12 km</td><td className="px-2 py-1"><strong>-78 dBm</strong></td></tr>
            </tbody>
          </table>

          <p className="pt-1">
            <strong>Les 2 indicateurs « Comparaison aux voisins »</strong> — on compare le LR aux autres LR connectés au
            <strong> même Rocket</strong> et situés à une distance similaire (± 30 % de la distance du LR évalué, minimum 3 voisins requis).
            But : distinguer une <em>mauvaise installation côté client</em> d'un <em>environnement RF dégradé pour tout le secteur</em>.
          </p>
          <ul className="list-disc list-inside space-y-0.5 ml-2">
            <li><strong>Outlier Signal</strong> — actif si le Signal dBm du LR est nettement pire que celui de ses voisins.</li>
            <li><strong>Outlier Bruit / CCQ</strong> — actif si le bruit ou le CCQ du LR est nettement pire que celui de ses voisins.</li>
          </ul>
          <p className="text-slate-500">
            Si le LR a moins de 3 voisins comparables, ces 2 indicateurs restent inactifs (on ne pénalise pas un LR isolé).
          </p>

          <p className="pt-1">
            <strong>Verdict final</strong> — selon le nombre d'indicateurs actifs (sur 10) :
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
              <tr><td className="px-2 py-1">3 – 4</td><td className="px-2 py-1"><strong>À surveiller</strong></td><td className="px-2 py-1">Bloc jaune</td></tr>
              <tr><td className="px-2 py-1">5 – 7</td><td className="px-2 py-1"><strong>Suspect</strong></td><td className="px-2 py-1">Bloc orange</td></tr>
              <tr><td className="px-2 py-1">8 – 10</td><td className="px-2 py-1"><strong>Critique</strong></td><td className="px-2 py-1">Bloc rouge</td></tr>
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
          <p className="text-green-600 font-semibold text-sm">✓ Toutes les installations sont stables sur 30 jours</p>
          <p className="text-blue-400 text-xs mt-1">Aucun LR n'atteint le seuil de 3 signaux actifs</p>
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
                      {['Client', 'Rocket / distance', 'Verdict', 'Signaux actifs', 'Métriques actuelles', 'Disponibilité 30j', ''].map(h => (
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
                            {row.active_signals_count}/10 signaux
                          </span>
                        </td>

                        <td className="px-4 py-3">
                          <div className="flex flex-col gap-1 max-w-[400px]">
                            {row.signals.map(s => <SignalPill key={s.key} signal={s} />)}
                          </div>
                        </td>

                        <td className="px-4 py-3 text-xs whitespace-nowrap">
                          <div className={signalClass(row.latest_signal_dbm, row.signal_warning_threshold)}
                               title={`Seuil warn pour ce LR : ${row.signal_warning_threshold.toFixed(0)} dBm`}>
                            Signal {fmt(row.latest_signal_dbm, ' dBm')}
                          </div>
                          <div className={noiseClass(row.latest_noise_dbm)}>
                            Bruit {fmt(row.latest_noise_dbm, ' dBm')}
                          </div>
                          <div className={ccqClass(row.latest_ccq_pct)}>
                            CCQ {fmt(row.latest_ccq_pct, ' %')}
                          </div>
                        </td>

                        <td className="px-4 py-3 text-xs">
                          <div className="text-slate-700">
                            {row.downtime_hours.toFixed(1)} h éteint
                          </div>
                          <div className="text-slate-500 text-[11px]">
                            {row.outages_count} panne(s)
                          </div>
                        </td>

                        <td className="px-4 py-3 whitespace-nowrap">
                          <Link
                            href="/devices"
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
