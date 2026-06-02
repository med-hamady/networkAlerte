'use client'

import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { AlertPolicy } from '@/lib/types'
import { alertTypeLabel } from '@/lib/types'
import SeverityBadge from '@/components/SeverityBadge'

const CHANNEL_STYLES: Record<string, string> = {
  email: 'bg-amber-50 text-amber-700 border-amber-200',
}

type EquipmentGroup = {
  key: string
  label: string
  description: string
  alertTypes: string[]
}

const EQUIPMENT_GROUPS: EquipmentGroup[] = [
  {
    key: 'rocket',
    label: 'LTU Rocket — station de base',
    description: 'Ping, SNMP IF-MIB, API HTTP LTU et auto-découverte des CPE peers',
    alertTypes: [
      'rocket_down',
      'radio_interface_down',
      'eth0_down',
      'cpe_disconnected',
      'signal_low',
      'cinr_low',
      'ccq_low',
      'cinr_ul_low',
      'ccq_ul_low',
      'radio_link_degraded',
      'capacity_low',
      'capacity_ul_low',
      'high_rx_tx_errors',
      'throughput_anomaly',
      'lr_discovered',
      'lr_ip_changed',
      'lr_reassigned',
    ],
  },
  {
    key: 'airmax_base',
    label: 'airMAX Rocket — station de base',
    description: 'Ping, SNMP UBNT Enterprise MIB + IF-MIB et auto-découverte des peers (Rocket M / NanoStation M)',
    alertTypes: [
      'airmax_down',
      'radio_interface_down',
      'eth0_down',
      'signal_low',
      'cinr_low',
      'ccq_low',
      'radio_link_degraded',
      'high_rx_tx_errors',
      'throughput_anomaly',
      'lr_discovered',
      'lr_ip_changed',
      'lr_reassigned',
    ],
  },
  {
    key: 'lr_ltu',
    label: 'Client LR — famille LTU',
    description: 'LR LTU : SNMP + API LTU + sonde transit SSH. Seuils de lien propres au LTU (potentiel ≥ 50 %, débit RX plus strict) — incident lr_link_substandard critique.',
    alertTypes: [
      'lr_link_substandard',
      'signal_low',
      'cinr_low',
      'ccq_low',
      'throughput_anomaly',
      'lr_no_transit',
      'lr_latency_high',
      'lr_bridge_mode_misconfig',
    ],
  },
  {
    key: 'lr_airmax',
    label: 'Client LR — famille airMAX (LiteBeam)',
    description: 'LR airMAX/LiteBeam : poll HTTP airOS (login.cgi → status.cgi) + sonde transit SSH. Seuils de lien airMAX (potentiel ≥ 40 %, débit RX moins strict, palier warning) — incident lr_link_substandard.',
    alertTypes: [
      'lr_link_substandard',
      'signal_low',
      'cinr_low',
      'throughput_anomaly',
      'lr_no_transit',
      'lr_latency_high',
      'lr_bridge_mode_misconfig',
    ],
  },
  {
    key: 'uisp_switch',
    label: 'UISP Switch',
    description: 'Ping, SNMP MIB-II : disponibilité, état et vitesse des ports, erreurs',
    alertTypes: [
      'switch_down',
      'switch_port_down',
      'switch_port_speed_low',
      'high_rx_tx_errors',
    ],
  },
  {
    key: 'uisp_power',
    label: 'UISP Power',
    description: 'Ping et API REST UISP Power : voltage, batterie, joignabilité',
    alertTypes: [
      'uisp_power_unreachable',
      'battery_low_warning',
      'battery_low_critical',
      'voltage_anomaly',
    ],
  },
  {
    key: 'generic',
    label: 'Générique / Réseau',
    description: 'Alertes transverses appliquées à tout équipement pingé',
    alertTypes: [
      'device_unreachable',
      'ping_instability',
      'transit_unavailable',
    ],
  },
  {
    key: 'system',
    label: 'Système / Sécurité',
    description: 'Événements non rattachés à un équipement — détectés côté superviseur',
    alertTypes: [
      'security_anomaly',
    ],
  },
]

function ChannelChip({ channel }: { channel: string }) {
  const style = CHANNEL_STYLES[channel] ?? 'bg-slate-50 text-slate-600 border-slate-200'
  return (
    <span className={`inline-flex px-2 py-0.5 rounded text-[11px] font-mono border ${style}`}>
      {channel}
    </span>
  )
}

function YesNo({ value }: { value: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-semibold ${
      value ? 'text-green-600' : 'text-blue-300'
    }`}>
      <span className={`w-1.5 h-1.5 rounded-full ${value ? 'bg-green-500' : 'bg-blue-200'}`} />
      {value ? 'Oui' : 'Non'}
    </span>
  )
}

function PolicyTable({ policies }: { policies: AlertPolicy[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-blue-50 border-b border-blue-100">
          <tr>
            {["Type d'alerte", 'Sévérité', 'Notification immédiate', 'Canaux', 'Regroupable', 'Rétablissement'].map(h => (
              <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-blue-50">
          {policies.map(p => (
            <tr key={p.alert_type} className="hover:bg-blue-50/50 transition-colors align-top">
              <td className="px-4 py-3">
                <div className="text-sm font-medium text-blue-900" title={p.alert_type}>
                  {alertTypeLabel(p.alert_type)}
                </div>
              </td>
              <td className="px-4 py-3"><SeverityBadge severity={p.severity} /></td>
              <td className="px-4 py-3 whitespace-nowrap"><YesNo value={p.notify_immediately} /></td>
              <td className="px-4 py-3">
                <div className="flex gap-1 flex-wrap">
                  {p.channels.map(c => <ChannelChip key={c} channel={c} />)}
                </div>
              </td>
              <td className="px-4 py-3 whitespace-nowrap"><YesNo value={p.groupable} /></td>
              <td className="px-4 py-3 whitespace-nowrap"><YesNo value={p.recovery_notification} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function AlertPoliciesPage() {
  const { data: policies, isLoading, error } = useSWR<AlertPolicy[]>(
    endpoints.alertPolicies, fetcher, { refreshInterval: 60_000 },
  )

  const policyByType = new Map<string, AlertPolicy>()
  for (const p of policies ?? []) policyByType.set(p.alert_type, p)

  const grouped = EQUIPMENT_GROUPS.map(group => ({
    group,
    items: group.alertTypes
      .map(t => policyByType.get(t))
      .filter((p): p is AlertPolicy => Boolean(p)),
  }))

  const knownTypes = new Set(EQUIPMENT_GROUPS.flatMap(g => g.alertTypes))
  const unclassified = (policies ?? []).filter(p => !knownTypes.has(p.alert_type))

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Politiques d&apos;alerte</h1>
        <p className="text-blue-400 text-sm mt-1">
          Référence des {policies?.length ?? '…'} alert_types regroupés par équipement :
          sévérité, canaux et politique de notification.
        </p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-600 px-4 py-3 rounded-xl text-sm">
          Erreur : {error instanceof Error ? error.message : 'Chargement impossible'}
        </div>
      )}

      {isLoading ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center text-blue-300 shadow-sm">
          Chargement…
        </div>
      ) : !policies?.length ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center text-blue-400 shadow-sm">
          Aucune policy retournée
        </div>
      ) : (
        <div className="space-y-6">
          {grouped.map(({ group, items }) => (
            items.length === 0 ? null : (
              <section key={group.key} className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
                <header className="px-5 py-3 bg-gradient-to-r from-blue-50 to-white border-b border-blue-100 flex items-baseline justify-between gap-4">
                  <div>
                    <h2 className="text-base font-semibold text-blue-900">{group.label}</h2>
                    <p className="text-[11px] text-blue-400 mt-0.5">{group.description}</p>
                  </div>
                  <span className="text-xs text-blue-500 font-mono whitespace-nowrap">
                    {items.length} alert_type{items.length > 1 ? 's' : ''}
                  </span>
                </header>
                <PolicyTable policies={items} />
              </section>
            )
          ))}

          {unclassified.length > 0 && (
            <section className="bg-white border border-amber-200 rounded-xl overflow-hidden shadow-sm">
              <header className="px-5 py-3 bg-amber-50 border-b border-amber-200">
                <h2 className="text-base font-semibold text-amber-800">Non classés</h2>
                <p className="text-[11px] text-amber-600 mt-0.5">
                  Alert types renvoyés par l&apos;API mais absents du mapping équipement.
                </p>
              </header>
              <PolicyTable policies={unclassified} />
            </section>
          )}
        </div>
      )}

      <div className="text-xs text-blue-400">
        <strong>Note :</strong> sévérité <code className="font-mono">dynamic</code> = la règle décide
        warning ou critical selon la valeur ; <em>notify immédiat</em> est alors forcé à
        true uniquement quand l&apos;incident est critical.
      </div>
    </div>
  )
}
