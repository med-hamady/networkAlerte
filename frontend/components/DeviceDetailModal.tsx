'use client'

import React from 'react'
import useSWR, { useSWRConfig } from 'swr'
import { controlPowerOutput, deleteDevice, endpoints, fetcher, runDiag } from '@/lib/api'
import { PERM, usePermissions } from '@/lib/permissions'
import type { DiagResult, PowerOutputAction, PowerOutputState } from '@/lib/api'
import type { Device, DeviceMetrics, Lr, NetworkCapacity, RocketCapacity } from '@/lib/types'
import { deviceLabel, formatDate, timeAgo, formatBytes, formatUptime, parentRocketId } from '@/lib/types'
import { useThresholds } from '@/lib/useThresholds'
import DeviceImage, { devicePhotoVariant } from './DeviceImage'
import IpLink from './IpLink'
import MetricHistoryModal from './MetricHistoryModal'

const RADIO_TYPES = new Set(['rocket', 'lr', 'airfiber', 'ptp_litebeam'])
const REFRESH      = 15_000

// Friendly labels + display order for the per-battery readings a UISP Power
// reports (metric slugs from uisp_power_service.battery_type_slug).
const BATTERY_LABELS: Record<string, string> = {
  lead_acid: 'Banc plomb (externe)',
  li_ion:    'Li-Ion (UPS interne)',
}
const BATTERY_ORDER: Record<string, number> = {
  lead_acid: 0,  // main backup bank first — it drives site survival
  li_ion:    1,
}

interface Props {
  device: Device | null
  devices?: Device[]
  onClose: () => void
  onNavigate?: (device: Device) => void
}

export default function DeviceDetailModal({ device, devices = [], onClose, onNavigate }: Props) {
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  if (!device) return null

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-blue-900/30 backdrop-blur-sm z-40 animate-fade-in"
        onClick={onClose}
      />
      {/* Panel */}
      <div className="fixed right-0 top-0 h-full w-full max-w-lg bg-white border-l border-blue-100 z-50 overflow-y-auto flex flex-col animate-slide-in shadow-2xl">
        <ModalContent device={device} devices={devices} onClose={onClose} onNavigate={onNavigate} />
      </div>
    </>
  )
}

function ModalContent({ device, devices, onClose, onNavigate }: {
  device: Device
  devices: Device[]
  onClose: () => void
  onNavigate?: (device: Device) => void
}) {
  // Droits de l'opérateur sur CETTE fiche. Trois gestes, trois droits : les
  // diagnostics ouvrent une session SSH chez le client, la sortie DC coupe une
  // alimentation, la suppression est irréversible. Les fondre en un seul
  // « peut modifier » donnerait les trois à qui n'en demandait qu'un.
  const { can } = usePermissions()
  const canDiagnostics = can(PERM.deviceDiagnostics)
  const canPowerOutput = can(PERM.devicePowerOutput)
  const canDelete = can(PERM.deviceDelete)

  const isRadio  = RADIO_TYPES.has(device.device_type)
  const isSwitch = device.device_type === 'uisp_switch'
  const isPower  = device.device_type === 'uisp_power'
  const isUp     = device.status === 'up'
  const isDown   = device.status === 'down'
  const isRocket = device.device_type === 'rocket'
  // airMAX P2P backhaul: surveillé sur sa capacité de lien (comme un AF60),
  // pas sur des clients — d'où une section dédiée et pas de "LR associés".
  const isBackhaul = device.device_type === 'ptp_litebeam'
  const isLr = device.device_type === 'lr'

  const thresholds = useThresholds()
  const [showHistory, setShowHistory] = React.useState(false)

  // Children LRs linked to this Rocket
  const linkedLRs = isRocket
    ? devices.filter(d => parentRocketId(d) === device.id)
    : []

  // Rocket parente d'un LR. La fiche s'ouvre aussi depuis des pages qui ne
  // passent pas la liste des devices (/lr-health, /topo-preview), donc on la
  // résout par fetch quand elle n'est pas dans `devices`.
  const rocketId = parentRocketId(device)
  const rocketFromList = rocketId != null ? devices.find(d => d.id === rocketId) : undefined
  const { data: rocketFetched } = useSWR<Device>(
    rocketId != null && !rocketFromList ? endpoints.device(rocketId) : null,
    fetcher,
  )
  const parentRocket = rocketFromList ?? rocketFetched

  // Métriques affichées depuis le dernier snapshot en base (poll ≤ 60 s).
  // L'ancien appel live (SNMP/API direct sur l'équipement) a été retiré.
  const { data: metrics } = useSWR<DeviceMetrics>(
    (isRadio || isSwitch || isPower) ? endpoints.deviceMetrics(device.id) : null,
    fetcher,
    { refreshInterval: REFRESH },
  )

  // Client capacity for a base-station Rocket: installed supervised clients
  // (current) vs its client ceiling (max). Read from /network-capacity so the
  // per-family/width formula stays a single backend source of truth.
  const { data: capacity } = useSWR<NetworkCapacity>(
    isRocket ? endpoints.networkCapacity : null,
    fetcher,
    { refreshInterval: REFRESH },
  )
  const rocketCap: RocketCapacity | undefined =
    isRocket && capacity
      ? capacity.sites.flatMap(s => s.rockets).find(r => r.id === device.id)
      : undefined

  return (
    <>
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-blue-100 bg-white sticky top-0 z-10">
        <div>
          <p className="font-bold text-slate-800 text-base">{device.name}</p>
          <p className="text-blue-400 text-xs mt-0.5">{deviceLabel(device)}</p>
        </div>
        <button
          onClick={onClose}
          className="w-8 h-8 flex items-center justify-center rounded-lg bg-blue-50 text-blue-400 hover:bg-blue-100 hover:text-blue-600 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Device image + status */}
      <div className={`px-6 py-8 flex flex-col items-center gap-4 border-b border-blue-100 ${
        isDown ? 'bg-red-50' : 'bg-blue-50'
      }`}>
        <DeviceImage type={device.device_type} variant={devicePhotoVariant(device)} size="lg" />

        {isUp && (
          <div className="flex items-center gap-2 bg-white border border-green-200 px-4 py-1.5 rounded-full shadow-sm">
            <span className="relative flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-60" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-green-500" />
            </span>
            <span className="text-green-600 text-sm font-bold">EN LIGNE</span>
          </div>
        )}
        {isDown && (
          <div className="flex items-center gap-2 bg-white border border-red-200 px-4 py-1.5 rounded-full shadow-sm">
            <span className="inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
            <span className="text-red-500 text-sm font-bold">HORS LIGNE</span>
          </div>
        )}
        {/* Aucune source ne parle de cet abonné : pas d'IP (donc hors du sweep
            de ping) ET UISP ne l'a pas vu depuis des jours. Ce n'est pas une
            panne constatée, donc pas de rouge — on affiche l'incertitude. */}
        {!isUp && !isDown && device.device_type === 'lr' && device.out_of_supervision && (
          <div
            className="flex items-center gap-2 bg-white border border-amber-200 px-4 py-1.5 rounded-full shadow-sm"
            title="Sans IP et non vu par UISP — aucune mesure possible. Récupéré automatiquement dès qu'un AP le rapporte."
          >
            <span className="inline-flex h-2.5 w-2.5 rounded-full bg-amber-400" />
            <span className="text-amber-600 text-sm font-bold">HORS SUPERVISION</span>
          </div>
        )}
        {/* Statut non mesurable mais RÉCENT — rouge comme un down : le device
            vient de sortir du sweep de ping, rien ne confirme plus qu'il est là. */}
        {!isUp && !isDown && !(device.device_type === 'lr' && device.out_of_supervision) && (
          <div className="flex items-center gap-2 bg-white border border-red-200 px-4 py-1.5 rounded-full shadow-sm">
            <span className="inline-flex h-2.5 w-2.5 rounded-full bg-red-400" />
            <span className="text-red-500 text-sm font-bold">INCONNU</span>
          </div>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 px-6 py-5 space-y-6">

        {/* Capacité du lien (backhaul P2P airMAX) — la métrique clé d'un lien
            inter-sites, remontée en tête comme pour un AF60. */}
        {isBackhaul && (
          <Section title="Capacité du lien">
            <BackhaulLinkContent
              current={metrics?.total_capacity_mbps?.value ?? null}
              floor={thresholds.airmax_backhaul_capacity_min_mbps}
              signal={metrics?.signal_dbm?.value ?? null}
            />
          </Section>
        )}

        {/* Capacité clients (Rocket AP only — jamais pour un backhaul) */}
        {isRocket && !isBackhaul && rocketCap && (
          <Section title="Capacité clients">
            <RocketCapacityContent cap={rocketCap} />
          </Section>
        )}

        {/* LR associés (Rocket AP only — un backhaul n'a pas de clients) */}
        {isRocket && !isBackhaul && (
          <div className="space-y-2.5">
            <p className="text-blue-400 text-xs uppercase tracking-widest font-semibold">
              LR associés
              <span className="ml-2 bg-blue-100 text-blue-600 px-1.5 py-0.5 rounded-full text-xs font-bold">
                {linkedLRs.length}
              </span>
            </p>

            {linkedLRs.length === 0 ? (
              <div className="bg-white border border-blue-100 rounded-xl p-4 text-center shadow-sm">
                <p className="text-blue-300 text-sm">Aucun LR lié à cette Rocket</p>
                <p className="text-blue-200 text-xs mt-1">
                  Assignez un LR via{' '}
                  <code className="bg-blue-50 px-1 rounded">PUT /api/v1/devices/{'<id>'}  {"{"}"rocket_id": {device.id}{"}"}</code>
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {linkedLRs.map(lr => (
                  <LRMiniCard
                    key={lr.id}
                    lr={lr}
                    onClick={() => onNavigate?.(lr)}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Base info */}
        <Section title="Informations générales">
          <MetricRow label="Adresse IP"    value={<IpLink ip={device.ip_address} className="font-mono text-blue-700" />} />
          <MetricRow label="Type"          value={deviceLabel(device)} />
          {device.mac_address && (
            <MetricRow
              label="Adresse MAC"
              value={<span className="font-mono uppercase text-slate-600">{device.mac_address}</span>}
            />
          )}
          {/* Rattachement radio : sur quelle Rocket cet abonné est connecté.
              C'est la donnée d'identité qui manquait pour aller du client à
              son AP sans repasser par la page du site. */}
          {isLr && (
            <MetricRow
              label="Rocket associée"
              value={
                parentRocket
                  ? (onNavigate
                      ? <button
                          onClick={() => onNavigate(parentRocket)}
                          className="text-blue-600 hover:text-blue-800 hover:underline font-semibold"
                        >
                          {parentRocket.name}
                        </button>
                      : <span className="font-semibold text-slate-700">{parentRocket.name}</span>)
                  : rocketId != null
                    ? <span className="text-blue-300">chargement…</span>
                    : <span className="text-blue-300">non rattachée</span>
              }
            />
          )}
          {device.device_type === 'lr' && (
            <MetricRow label="Client UISP" value={<CrmAttachment lr={device} />} />
          )}
          {device.location && <MetricRow label="Localisation" value={device.location} />}
          <MetricRow
            label="Dernière vue"
            value={<span className={isDown ? 'text-red-500' : 'text-slate-600'}>{timeAgo(device.last_seen)}</span>}
          />
          <MetricRow label="Ajouté le" value={formatDate(device.created_at)} />
          {metrics?.uptime_seconds?.value != null && (
            <MetricRow label="Uptime" value={formatUptime(metrics.uptime_seconds.value)} />
          )}
          {metrics?.lr_latency_ms?.value != null && (
            <MetricRow
              label="Latence Internet (moy.)"
              value={
                <LatencyValue
                  ms={metrics.lr_latency_ms.value}
                  warn={thresholds.lr_latency_critical_ms}
                  crit={thresholds.lr_latency_critical_ms}
                />
              }
            />
          )}
          {/* LR : latence + qualité du lien. AF60 : débits et capacités du
              backhaul (le RTT n'y est pas sondé, mais les onglets suivent
              `available_metrics`, donc seule la latence manque à l'appel). */}
          {/* Les SWITCHES sont inclus depuis 2026-08-11 : ceux qui portent une
              liaison fibre inter-sites ont une courbe de débit (dérivée des
              compteurs de leur port SFP). Le bouton reste inoffensif sur un
              switch sans fibre — les onglets suivent `available_metrics`, donc
              la modale annonce simplement qu'il n'y a pas d'historique. */}
          {(isLr || device.device_type === 'airfiber'
            || device.device_type === 'uisp_switch') && (
            <button
              onClick={() => setShowHistory(true)}
              className="w-full flex items-center justify-center gap-2 mt-1 px-3 py-2 rounded-lg bg-blue-50 text-blue-600 hover:bg-blue-100 text-xs font-semibold transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 17l6-6 4 4 8-8" />
              </svg>
              Plus d&apos;infos — graphes d&apos;historique
            </button>
          )}
        </Section>

        {/* Radio metrics */}
        {isRadio && metrics && (
          <Section title="Métriques radio">
            {metrics.eth_if_up?.value != null && device.device_type === 'rocket' && (
              <MetricRow label="Lien switch (eth0)" value={<LinkStatus up={metrics.eth_if_up.value === 1} />} />
            )}
            {metrics.radio_if_up?.value != null && (
              <MetricRow label="Interface radio" value={<LinkStatus up={metrics.radio_if_up.value === 1} />} />
            )}
            {/* AF60 — état du lien 60 GHz (radios[0].linkState) */}
            {metrics.af60_link_up?.value != null && (
              <MetricRow label="Lien radio (60 GHz)" value={<LinkStatus up={metrics.af60_link_up.value === 1} />} />
            )}
            {metrics.distance_m?.value    != null && <MetricRow label="Distance"  value={`${metrics.distance_m.value.toFixed(0)} m`} />}
            {metrics.signal_dbm?.value    != null && <MetricRow label="Signal UL (AP)"  value={<SignalValue dBm={metrics.signal_dbm.value} warn={thresholds.signal_warning_dbm} crit={thresholds.signal_critical_dbm} />} />}
            {/* AF60 — SNR (le 60 GHz expose un SNR, pas de CINR) */}
            {metrics.snr_db?.value        != null && <MetricRow label="SNR (local)"     value={`${metrics.snr_db.value.toFixed(0)} dB`} />}
            {metrics.remote_snr_db?.value != null && <MetricRow label="SNR (distant)"   value={`${metrics.remote_snr_db.value.toFixed(0)} dB`} />}
            {metrics.noise_dbm?.value     != null && <MetricRow label="Bruit (AP)"      value={`${metrics.noise_dbm.value} dBm`} />}
            {metrics.cinr_db?.value       != null && <MetricRow label="CINR DL"         value={`${metrics.cinr_db.value} dB`} />}
            {metrics.ul_cinr_db?.value    != null && <MetricRow label="CINR UL"         value={`${metrics.ul_cinr_db.value} dB`} />}
            {metrics.ccq_pct?.value       != null && <MetricRow label="CCQ DL"          value={<CcqValue pct={metrics.ccq_pct.value} warn={thresholds.ccq_warning_pct} crit={thresholds.ccq_critical_pct} />} />}
            {metrics.ul_ccq_pct?.value    != null && <MetricRow label="CCQ UL"          value={<CcqValue pct={metrics.ul_ccq_pct.value} warn={thresholds.ccq_warning_pct} crit={thresholds.ccq_critical_pct} />} />}
            {/* DÉBIT = trafic réellement écoulé ; CAPACITÉ = ce que le lien
                pourrait écouler. Deux mesures distinctes : les afficher avec
                la même valeur revenait à mentir sur l'une des deux. */}
            {metrics.dl_throughput_mbps?.value != null && <MetricRow label="Débit DL"    value={formatRate(metrics.dl_throughput_mbps.value)} />}
            {metrics.ul_throughput_mbps?.value != null && <MetricRow label="Débit UL"    value={formatRate(metrics.ul_throughput_mbps.value)} />}
            {metrics.dl_capacity_mbps?.value   != null && <MetricRow label="Capacité DL" value={`${metrics.dl_capacity_mbps.value.toFixed(1)} Mbps`} />}
            {metrics.ul_capacity_mbps?.value   != null && <MetricRow label="Capacité UL" value={`${metrics.ul_capacity_mbps.value.toFixed(1)} Mbps`} />}
            {metrics.dl_phy_rate_mbps?.value   != null && <MetricRow label="Modulation DL" value={`${metrics.dl_phy_rate_mbps.value.toFixed(1)} Mbps`} />}
            {metrics.ul_phy_rate_mbps?.value   != null && <MetricRow label="Modulation UL" value={`${metrics.ul_phy_rate_mbps.value.toFixed(1)} Mbps`} />}

            {(metrics.total_capacity_mbps?.value != null ||
              metrics.link_potential_pct?.value != null ||
              metrics.local_rx_rate_idx?.value != null ||
              metrics.remote_rx_rate_idx?.value != null) && (
              <>
                <div className="border-t border-blue-100 my-1" />
                <p className="text-blue-300 text-xs uppercase tracking-wider">Résumé du lien</p>
                {metrics.total_capacity_mbps?.value != null && (
                  <MetricRow label="Capacité totale" value={`${metrics.total_capacity_mbps.value.toFixed(2)} Mbps`} />
                )}
                {metrics.link_potential_pct?.value != null && (
                  <MetricRow label="Potentiel du lien" value={<LinkPotentialValue pct={metrics.link_potential_pct.value} />} />
                )}
                {metrics.local_rx_rate_idx?.value != null && (
                  <MetricRow label="Débit RX local" value={<RateIdxValue idx={metrics.local_rx_rate_idx.value} />} />
                )}
                {metrics.remote_rx_rate_idx?.value != null && (
                  <MetricRow label="Débit RX distant" value={<RateIdxValue idx={metrics.remote_rx_rate_idx.value} />} />
                )}
              </>
            )}

            {metrics.remote_signal_dbm?.value != null && (
              <>
                <div className="border-t border-blue-100 my-1" />
                <p className="text-blue-300 text-xs uppercase tracking-wider">CPE distant</p>
                <MetricRow label="Signal DL (CPE)" value={<SignalValue dBm={metrics.remote_signal_dbm.value} warn={thresholds.signal_warning_dbm} crit={thresholds.signal_critical_dbm} />} />
              </>
            )}
            {metrics.remote_noise_dbm?.value != null && <MetricRow label="Bruit (CPE)"   value={`${metrics.remote_noise_dbm.value} dBm`} />}
            {metrics.remote_eirp_dbm?.value  != null && <MetricRow label="Puissance TX"  value={`${metrics.remote_eirp_dbm.value} dBm`} />}
            {metrics.peer_uptime_s?.value    != null && <MetricRow label="Uptime CPE"    value={formatUptime(metrics.peer_uptime_s.value)} />}
            {metrics.peer_cpu_pct?.value     != null && <MetricRow label="CPU CPE"       value={`${metrics.peer_cpu_pct.value.toFixed(0)} %`} />}
            {metrics.peer_ram_pct?.value     != null && <MetricRow label="RAM CPE"       value={`${metrics.peer_ram_pct.value.toFixed(0)} %`} />}

            {metrics.radio_rx_bytes?.value != null && (
              <>
                <div className="border-t border-blue-100 my-1" />
                <p className="text-blue-300 text-xs uppercase tracking-wider">Compteurs</p>
                <MetricRow label="RX total"    value={formatBytes(metrics.radio_rx_bytes.value)} />
              </>
            )}
            {metrics.radio_tx_bytes?.value != null && <MetricRow label="TX total"     value={formatBytes(metrics.radio_tx_bytes.value)} />}
            {(metrics.radio_in_errors?.value != null || metrics.radio_out_errors?.value != null) && (
              <MetricRow label="Erreurs RX/TX" value={`${metrics.radio_in_errors?.value ?? '—'} / ${metrics.radio_out_errors?.value ?? '—'}`} />
            )}
          </Section>
        )}

        {isRadio && !metrics && isUp && (
          <p className="text-blue-300 text-sm italic">Métriques SNMP en attente de collecte…</p>
        )}

        {/* Switch ports */}
        {isSwitch && metrics && (() => {
          const portNums = [...new Set(
            Object.keys(metrics)
              .filter(k => /^port_\d+_up$/.test(k))
              .map(k => parseInt(k.split('_')[1]))
          )].sort((a, b) => a - b)

          if (portNums.length === 0) return null

          return (
            <Section title="Ports réseau">
              {portNums.map(n => {
                const up      = metrics[`port_${n}_up`]?.value
                const speed   = metrics[`port_${n}_speed_mbps`]?.value
                const rxBytes = metrics[`port_${n}_rx_bytes`]?.value
                const txBytes = metrics[`port_${n}_tx_bytes`]?.value
                const inErr   = metrics[`port_${n}_in_errors`]?.value
                const outErr  = metrics[`port_${n}_out_errors`]?.value
                const inDis   = metrics[`port_${n}_in_discards`]?.value
                const outDis  = metrics[`port_${n}_out_discards`]?.value
                const portUp  = up === 1.0

                return (
                  <div key={n} className="rounded-lg border border-blue-100 p-3 space-y-1.5 bg-blue-50/50">
                    <div className="flex items-center justify-between font-medium text-sm">
                      <span className="text-slate-700">GigabitEthernet{n}</span>
                      <span className={portUp ? 'text-green-600' : 'text-blue-300'}>
                        {portUp ? '● UP' : '○ DOWN'}
                        {speed != null && portUp && (
                          <span className="text-blue-300 font-normal ml-1 text-xs">{speed} Mbps</span>
                        )}
                      </span>
                    </div>
                    {portUp && (rxBytes != null || txBytes != null) && (
                      <div className="grid grid-cols-2 gap-x-3 text-xs text-blue-400">
                        {rxBytes != null && <span>RX : {formatBytes(rxBytes)}</span>}
                        {txBytes != null && <span>TX : {formatBytes(txBytes)}</span>}
                        {(inErr  != null || outErr  != null) && <span>Erreurs : {inErr ?? 0}/{outErr ?? 0}</span>}
                        {(inDis  != null || outDis  != null) && <span>Discards : {inDis ?? 0}/{outDis ?? 0}</span>}
                      </div>
                    )}
                  </div>
                )
              })}
            </Section>
          )
        })()}

        {/* UISP Power metrics */}
        {isPower && metrics && (
          (() => {
            const v       = metrics.voltage_v?.value
            const a       = metrics.current_a?.value
            const w       = metrics.power_w?.value
            const maxW    = metrics.output_max_power_w?.value
            const energy  = metrics.output_energy_wh?.value
            const ac      = metrics.ac_connected?.value   // 1 = secteur présent, 0 = sur batterie

            // Per-battery readings: every metric_name like `battery_<slug>_pct`,
            // plus its voltage/capacity/runtime counterparts. Falls back to the
            // legacy single battery_pct/battery_voltage_v keys for devices polled
            // before the per-battery split shipped.
            const batteries = Object.keys(metrics)
              .map(k => /^battery_(.+)_pct$/.exec(k))
              .filter((m): m is RegExpExecArray => m != null)
              .map(m => ({
                slug:        m[1],
                pct:         metrics[m[0]]?.value,
                volt:        metrics[`battery_${m[1]}_voltage_v`]?.value,
                capAh:       metrics[`battery_${m[1]}_capacity_ah`]?.value,
                runtime:     metrics[`battery_${m[1]}_runtime_s`]?.value,
                discharging: metrics[`battery_${m[1]}_discharging`]?.value,
              }))
              .sort((x, y) => (BATTERY_ORDER[x.slug] ?? 99) - (BATTERY_ORDER[y.slug] ?? 99))

            // Battery currently in use (discharging) — named on the source row.
            const activeBattery = batteries.find(b => b.discharging != null && b.discharging >= 1)

            // DC output ports: every `dc_output_<id>_power_w`.
            const dcOutputs = Object.keys(metrics)
              .map(k => /^dc_output_(.+)_power_w$/.exec(k))
              .filter((m): m is RegExpExecArray => m != null)
              .map(m => ({
                id:        m[1],
                w:         metrics[m[0]]?.value,
                volt:      metrics[`dc_output_${m[1]}_voltage_v`]?.value,
                amp:       metrics[`dc_output_${m[1]}_current_a`]?.value,
                connected: metrics[`dc_output_${m[1]}_connected`]?.value,
              }))
              .sort((x, y) => x.id.localeCompare(y.id))

            const legacyPct  = metrics.battery_pct?.value
            const legacyVolt = metrics.battery_voltage_v?.value
            const showLegacy = batteries.length === 0 && (legacyPct != null || legacyVolt != null)

            const hasAny =
              [v, a, w, maxW, energy, ac].some(x => x != null) ||
              batteries.length > 0 || dcOutputs.length > 0 || showLegacy
            if (!hasAny) return null

            const renderBattery = (
              label: string,
              b: { pct?: number | null; volt?: number | null; capAh?: number | null; runtime?: number | null; discharging?: number | null },
            ) => (
              <div key={label} className="mt-1">
                <p className="text-blue-300 text-xs uppercase tracking-wider">
                  {label}
                  {b.discharging != null && b.discharging >= 1 && (
                    <span className="ml-2 text-orange-500 font-semibold normal-case">● en service</span>
                  )}
                </p>
                {b.pct != null && (
                  <MetricRow
                    label="Charge"
                    value={
                      <BatteryValue
                        pct={b.pct}
                        warn={thresholds.battery_warning_pct}
                        crit={thresholds.battery_critical_pct}
                      />
                    }
                  />
                )}
                {b.volt != null && (
                  <MetricRow label="Tension" value={`${b.volt.toFixed(1)} V`} />
                )}
                {b.capAh != null && (
                  <MetricRow label="Capacité" value={`${b.capAh.toFixed(1)} Ah`} />
                )}
                {b.runtime != null && b.runtime > 0 && (
                  <MetricRow label="Autonomie estimée" value={formatUptime(b.runtime)} />
                )}
              </div>
            )

            return (
              <Section title="Alimentation">
                {ac != null && (
                  <MetricRow
                    label="Source d'alimentation"
                    value={
                      ac >= 1 ? (
                        <span className="text-green-600 font-semibold">⚡ Secteur (SOMELEC)</span>
                      ) : (
                        <span className="text-orange-500 font-semibold">
                          🔋 Batterie{activeBattery ? ` — ${BATTERY_LABELS[activeBattery.slug] ?? activeBattery.slug}` : ' (secteur absent)'}
                        </span>
                      )
                    }
                  />
                )}
                {v != null && (
                  <MetricRow label="Tension" value={<VoltageValue volts={v} />} />
                )}
                {a != null && (
                  <MetricRow label="Courant" value={`${a.toFixed(2)} A`} />
                )}
                {w != null && (
                  <MetricRow
                    label="Puissance"
                    value={`${w.toFixed(1)} W${maxW != null ? ` / ${maxW.toFixed(0)} W` : ''}`}
                  />
                )}
                {energy != null && (
                  <MetricRow label="Énergie cumulée" value={`${(energy / 1000).toFixed(1)} kWh`} />
                )}

                {(batteries.length > 0 || showLegacy) && (
                  <div className="border-t border-blue-100 my-1" />
                )}
                {batteries.map(b =>
                  renderBattery(BATTERY_LABELS[b.slug] ?? `Batterie ${b.slug}`, b),
                )}
                {showLegacy && renderBattery('Batterie', { pct: legacyPct, volt: legacyVolt })}

                {dcOutputs.length > 0 && (
                  <>
                    <div className="border-t border-blue-100 my-1" />
                    <p className="text-blue-300 text-xs uppercase tracking-wider">Sorties DC</p>
                    {dcOutputs.map(o => (
                      <MetricRow
                        key={o.id}
                        label={`Sortie ${o.id}`}
                        value={
                          <span className="text-slate-600">
                            <span className={o.connected ? 'text-green-600 font-semibold' : 'text-blue-300'}>
                              {o.connected ? 'connectée' : 'déconnectée'}
                            </span>
                            {o.w != null && ` · ${o.w.toFixed(1)} W`}
                            {o.volt != null && ` · ${o.volt.toFixed(1)} V`}
                          </span>
                        }
                      />
                    ))}
                  </>
                )}
              </Section>
            )
          })()
        )}

        {/* ⚠️ Couper une sortie DC coupe PHYSIQUEMENT l'alimentation du
            matériel branché dessus : son droit est distinct de la simple
            consultation de la fiche, et distinct des diagnostics. */}
        {isPower && canPowerOutput && <PowerOutputControl device={device} />}

        {isPower && !metrics && isUp && (
          <p className="text-blue-300 text-sm italic">Métriques UISP Power en attente de collecte…</p>
        )}

        {/* Diagnostics — ouvrent une session SSH sur l'équipement du client. */}
        {canDiagnostics && device.device_type === 'lr' && (
          <Section title="Diagnostics">
            <DiagRow label="SSH"          url={endpoints.checkSsh(device.id)} />
            <DiagRow label="Ping 8.8.8.8" url={endpoints.checkPing(device.id)} />
          </Section>
        )}

        {canDiagnostics && device.device_type === 'client_modem' && (
          <Section title="Diagnostics">
            <DiagRow label="Ping depuis le LR" url={endpoints.pingFromLr(device.id)} />
          </Section>
        )}

        {device.notes && (
          <Section title="Notes">
            <p className="text-slate-600 text-sm">{device.notes}</p>
          </Section>
        )}

        {canDelete && <DeleteDeviceControl device={device} onDeleted={onClose} />}
      </div>

      {showHistory && (
        <MetricHistoryModal device={device} onClose={() => setShowHistory(false)} />
      )}
    </>
  )
}

/* ─── Suppression définitive ─── */

// Confirmation en deux temps : le clic sur « Supprimer » ne fait qu'armer.
// La cascade DB emporte métriques, incidents (journal des coupures compris) et
// historique des courbes ; les LR d'un Rocket sont seulement détachés (SET NULL).
function DeleteDeviceControl({ device, onDeleted }: { device: Device; onDeleted: () => void }) {
  const { mutate } = useSWRConfig()
  const [confirming, setConfirming] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  // Changer d'équipement (onNavigate) ne doit pas garder la confirmation armée.
  React.useEffect(() => {
    setConfirming(false)
    setError(null)
  }, [device.id])

  const handleDelete = async () => {
    setBusy(true)
    setError(null)
    try {
      await deleteDevice(device.id)
      // Fermer d'abord : les requêtes de la fiche (métriques de l'équipement
      // supprimé) sont démontées et ne partent pas en 404 à la revalidation.
      onDeleted()
      // Toutes les listes montées (sites, santé des liens…) contiennent
      // potentiellement l'équipement : on les revalide toutes.
      mutate(() => true)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur inattendue')
      setBusy(false)
    }
  }

  return (
    <Section title="Zone de danger">
      {!confirming ? (
        <button
          onClick={() => setConfirming(true)}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 text-xs font-semibold transition-colors"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3M4 7h16" />
          </svg>
          Supprimer l&apos;équipement
        </button>
      ) : (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 space-y-2">
          <p className="text-xs text-red-800">
            Supprimer <span className="font-semibold">{device.name}</span> définitivement ?
            Ses métriques, incidents et historiques seront effacés. Action irréversible.
          </p>
          <p className="text-xs text-red-700/80">
            S&apos;il est toujours présent dans UISP ou rapporté par un AP, il sera
            réimporté automatiquement (sans son historique).
          </p>
          <div className="flex gap-2 justify-end">
            <button
              onClick={() => setConfirming(false)}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded-lg bg-white border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
            >
              Annuler
            </button>
            <button
              onClick={handleDelete}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded-lg bg-red-600 text-white font-semibold hover:bg-red-700 disabled:opacity-50"
            >
              {busy ? 'Suppression…' : 'Supprimer définitivement'}
            </button>
          </div>
        </div>
      )}
      {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
    </Section>
  )
}

/* ─── LR Mini Card ─── */

function LRMiniCard({ lr, onClick }: { lr: Device; onClick: () => void }) {
  const isUp   = lr.status === 'up'
  const isDown = lr.status === 'down'

  return (
    <button
      onClick={onClick}
      className={`w-full text-left flex items-center gap-3 p-3 rounded-xl border transition-all hover:shadow-sm group ${
        isDown
          ? 'bg-red-50 border-red-200 hover:border-red-300'
          : 'bg-white border-blue-100 hover:border-blue-300'
      }`}
    >
      {/* Mini photo */}
      <div className={`w-12 h-12 rounded-lg flex items-center justify-center shrink-0 ${
        isDown ? 'bg-red-100' : 'bg-blue-50'
      }`}>
        <DeviceImage type="lr" size="sm" />
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <p className="font-semibold text-slate-800 text-sm truncate">{lr.name}</p>
          {isUp && (
            <span className="flex items-center gap-1 shrink-0">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-green-500" />
              </span>
              <span className="text-green-600 text-xs font-bold">UP</span>
            </span>
          )}
          {isDown && (
            <span className="flex items-center gap-1 shrink-0">
              <span className="inline-flex h-2 w-2 rounded-full bg-red-500" />
              <span className="text-red-500 text-xs font-bold">DOWN</span>
            </span>
          )}
          {!isUp && !isDown && (
            <span className="text-blue-300 text-xs font-bold shrink-0">—</span>
          )}
        </div>
        <p className="text-blue-400 text-xs font-mono mt-0.5"><IpLink ip={lr.ip_address} /></p>
        {lr.location && <p className="text-slate-400 text-xs truncate">{lr.location}</p>}
      </div>

      {/* Arrow */}
      <svg className="w-4 h-4 text-blue-300 group-hover:text-blue-500 transition-colors shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
      </svg>
    </button>
  )
}

/* ─── Backhaul P2P link capacity ─── */

function BackhaulLinkContent({ current, floor, signal }: {
  current: number | null
  floor: number
  signal: number | null
}) {
  const below = current != null && current < floor
  const pct = current != null && floor > 0 ? Math.min(100, (current / floor) * 100) : null
  return (
    <>
      <MetricRow
        label="Capacité actuelle"
        value={
          current != null
            ? <span className={`font-semibold ${below ? 'text-red-500' : 'text-green-600'}`}>{current.toFixed(0)} Mbps</span>
            : <span className="text-blue-300">en attente de collecte…</span>
        }
      />
      <MetricRow label="Plancher" value={<span className="text-slate-600">{floor.toFixed(0)} Mbps</span>} />
      {signal != null && (
        <MetricRow label="Signal" value={<span className="text-slate-600">{signal.toFixed(0)} dBm</span>} />
      )}
      {pct != null && (
        <div className="bg-slate-100 rounded h-2.5 overflow-hidden">
          <div
            className={`h-full rounded transition-all ${below ? 'bg-red-500' : 'bg-green-500'}`}
            style={{ width: `${Math.max(2, pct)}%` }}
          />
        </div>
      )}
      {below && (
        <p className="text-red-500 text-xs">Lien sous le plancher — capacité dégradée.</p>
      )}
    </>
  )
}

/* ─── Rocket client capacity ─── */

function RocketCapacityContent({ cap }: { cap: RocketCapacity }) {
  const { current_clients: current, max_clients: max, channel_width_mhz: width } = cap
  const rawPct = max && max > 0 ? (current / max) * 100 : null
  const barPct = rawPct == null ? 0 : Math.min(100, rawPct)
  // Occupancy bands: <70 % OK, 70–90 % chargé, ≥90 % (ou saturé) critique.
  const band = rawPct == null ? 'unknown' : rawPct >= 90 ? 'crit' : rawPct >= 70 ? 'warn' : 'ok'
  const barCls = band === 'crit' ? 'bg-red-500' : band === 'warn' ? 'bg-yellow-400' : 'bg-green-500'
  const txtCls = band === 'crit' ? 'text-red-500' : band === 'warn' ? 'text-yellow-600' : 'text-green-600'

  return (
    <>
      <MetricRow
        label="Capacité actuelle"
        value={<span className="font-semibold text-slate-700">{current} client{current > 1 ? 's' : ''}</span>}
      />
      <MetricRow
        label="Capacité maximale"
        value={max != null
          ? <span className="font-semibold text-slate-700">{max} clients</span>
          : <span className="text-blue-300">indéterminée</span>}
      />
      {width != null && <MetricRow label="Largeur de canal" value={`${width} MHz`} />}

      {max != null && rawPct != null ? (
        <>
          <MetricRow
            label="Taux d'occupation"
            value={<span className={`font-semibold ${txtCls}`}>{current}/{max} · {rawPct.toFixed(0)} %</span>}
          />
          <div className="bg-slate-100 rounded h-2.5 overflow-hidden">
            <div className={`h-full ${barCls} rounded transition-all`} style={{ width: `${Math.max(2, barPct)}%` }} />
          </div>
        </>
      ) : (
        <p className="text-blue-300 text-xs">
          Capacité maximale indéterminée — largeur de canal inconnue (créds airOS manquants sur la fiche).
        </p>
      )}
    </>
  )
}

/* ─── Sub-components ─── */

/* ─── Alimentation d'un UISP Power ─── */

// Le seul endroit du dashboard qui éteint physiquement du matériel. Deux
// gestes que tout sépare, et c'est ce que l'écran doit rendre évident :
//   - « temporaire » : le firmware du boîtier rallume seul en ~5 s ;
//   - « durable »    : personne ne la relève, il faut revenir cliquer.
//
// ⚠️ Sur un UISP-P il n'y a QU'UNE sortie DC : couper, c'est couper tout ce
// que le boîtier alimente — pas un port isolé. D'où la charge en watts
// affichée avant d'agir : c'est la mesure de ce qu'on s'apprête à éteindre.
function PowerOutputControl({ device }: { device: Device }) {
  const { data, error, isLoading, mutate } = useSWR<PowerOutputState>(
    endpoints.powerOutput(device.id),
    fetcher,
    // Pas de refreshInterval : chaque lecture ouvre une session HTTPS sur le
    // boîtier, et cet état ne change que lorsqu'on le change soi-même.
    { revalidateOnFocus: false },
  )

  const [expanded, setExpanded] = React.useState(false)
  // La coupure durable demande un second clic : voir le bloc d'avertissement.
  const [confirmDurable, setConfirmDurable] = React.useState(false)
  const [busy, setBusy] = React.useState<PowerOutputAction | null>(null)
  const [outcome, setOutcome] = React.useState<{ ok: boolean; message: string } | null>(null)

  const run = async (action: PowerOutputAction) => {
    setBusy(action)
    setOutcome(null)
    try {
      const result = await controlPowerOutput(device.id, action)
      setOutcome({ ok: result.ok, message: result.message })
      setExpanded(false)
      setConfirmDurable(false)
      // ⚠️ Relecture tardive, et pas seulement parce que la sortie est coupée
      // 5 s : le boîtier alimente le switch qui porte son propre lien de
      // management (constaté sur AT1 le 2026-09-07), donc il disparaît du
      // réseau et n'en revient qu'une fois ce switch redémarré — une bonne
      // minute. Relire à 8 s ne ramenait qu'une erreur, affichée comme une
      // panne alors que tout se déroule normalement.
      if (action === 'cycle') setTimeout(() => mutate(), 90_000)
      else mutate()
    } catch (e) {
      setOutcome({ ok: false, message: e instanceof Error ? e.message : 'Erreur réseau' })
    } finally {
      setBusy(null)
    }
  }

  if (isLoading) {
    return (
      <Section title="Alimentation">
        <p className="text-blue-300 text-sm italic animate-pulse">
          Lecture de l'état de la sortie DC…
        </p>
      </Section>
    )
  }

  // Boîtier injoignable ou identifiants absents : on ne propose AUCUN bouton.
  // Un bouton qui échouera à coup sûr laisse croire que la coupure a peut-être
  // eu lieu — sur ce geste-là, c'est le doute le plus coûteux.
  if (error || !data) {
    return (
      <Section title="Alimentation">
        <p className="text-sm text-amber-600">
          État de l'alimentation indisponible — le boîtier ne répond pas, ou ses
          identifiants API manquent sur sa fiche.
        </p>
      </Section>
    )
  }

  const isOff = data.any_enabled === false
  const load = data.power_w

  return (
    <Section title="Alimentation">
      {outcome && (
        <p className={`text-sm font-medium ${outcome.ok ? 'text-green-600' : 'text-red-500'}`}>
          {outcome.ok ? '● ' : '✗ '}{outcome.message}
        </p>
      )}

      {isOff ? (
        <>
          <div className="rounded-lg bg-red-50 border border-red-200 p-3">
            <p className="text-sm font-semibold text-red-700">Sortie DC coupée</p>
            <p className="text-xs text-red-600 mt-1">
              Ce boîtier ne délivre plus de courant. Les équipements qu'il alimente
              resteront hors service tant que l'alimentation n'est pas rétablie.
            </p>
          </div>
          <button
            onClick={() => run('on')}
            disabled={busy != null}
            className="w-full rounded-lg bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white text-sm font-semibold py-2.5 transition-colors"
          >
            {busy === 'on' ? 'Rétablissement…' : 'Rallumer l\'alimentation'}
          </button>
        </>
      ) : !expanded ? (
        <>
          <MetricRow
            label="Sortie DC"
            value={
              <span className="text-green-600 font-semibold">
                Active{load != null ? ` — ${load.toFixed(1)} W` : ''}
              </span>
            }
          />
          <button
            onClick={() => { setExpanded(true); setConfirmDurable(false); setOutcome(null) }}
            className="w-full rounded-lg border border-red-300 text-red-600 hover:bg-red-50 text-sm font-semibold py-2.5 transition-colors"
          >
            Couper l'alimentation
          </button>
        </>
      ) : (
        <div className="space-y-3">
          {/* Ce qu'on s'apprête à éteindre, nommé — pas un « Êtes-vous sûr ? ». */}
          <div className="rounded-lg bg-red-50 border border-red-200 p-3 space-y-1.5">
            <p className="text-sm font-semibold text-red-700">
              {device.name}
              {device.site ? ` — site ${device.site}` : ''}
            </p>
            <p className="text-xs text-red-600">
              {load != null
                ? `${load.toFixed(1)} W actuellement délivrés, soit tout ce que ce boîtier alimente : `
                : 'La coupure porte sur tout ce que ce boîtier alimente : '}
              il n'y a qu'une sortie DC, ce n'est pas un port isolé.
            </p>
            <p className="text-xs text-red-600">
              Les équipements alimentés vont tomber : les incidents
              <span className="font-semibold"> device_unreachable </span>
              partiront sur WhatsApp comme pour une panne réelle.
            </p>
          </div>

          <button
            onClick={() => run('cycle')}
            disabled={busy != null}
            className="w-full text-left rounded-lg border border-amber-300 hover:bg-amber-50 disabled:opacity-50 p-3 transition-colors"
          >
            <span className="block text-sm font-semibold text-amber-700">
              {busy === 'cycle' ? 'Coupure en cours…' : 'Coupure temporaire'}
            </span>
            <span className="block text-xs text-amber-600 mt-0.5">
              5 secondes de coupure, puis le boîtier rallume tout seul — mais
              les équipements alimentés redémarrent : compter 1 à 2 min avant
              le retour du service. Sert à débloquer du matériel figé.
            </span>
          </button>

          {!confirmDurable ? (
            <button
              onClick={() => setConfirmDurable(true)}
              disabled={busy != null}
              className="w-full text-left rounded-lg bg-red-600 hover:bg-red-700 disabled:opacity-50 p-3 transition-colors"
            >
              <span className="block text-sm font-semibold text-white">Coupure durable</span>
              <span className="block text-xs text-red-100 mt-0.5">
                Reste coupé jusqu'à ce que quelqu'un revienne cliquer
                « Rallumer ». Rien ne le rétablit tout seul.
              </span>
            </button>
          ) : (
            /* Second clic exigé — et ce n'est pas de la friction décorative :
               le boîtier alimente le switch qui porte son propre lien de
               management, donc une fois coupé il n'est plus joignable et le
               bouton « Rallumer » ne peut plus l'atteindre. Le geste est
               réversible sur place, pas depuis cet écran. */
            <div className="rounded-lg bg-red-600 p-3 space-y-2.5">
              <p className="text-sm font-semibold text-white">
                Confirmer la coupure durable ?
              </p>
              <p className="text-xs text-red-100">
                Ce boîtier alimente le switch par lequel on le joint. Une fois
                coupé, il sort du réseau et le bouton « Rallumer » ne pourra
                plus l'atteindre :
                <span className="font-semibold"> le rétablissement exigera un
                déplacement sur site.</span>
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => run('off')}
                  disabled={busy != null}
                  className="flex-1 rounded-lg bg-white text-red-700 hover:bg-red-50 disabled:opacity-50 text-sm font-semibold py-2 transition-colors"
                >
                  {busy === 'off' ? 'Coupure en cours…' : 'Oui, couper durablement'}
                </button>
                <button
                  onClick={() => setConfirmDurable(false)}
                  disabled={busy != null}
                  className="flex-1 rounded-lg border border-red-200 text-white hover:bg-red-700 disabled:opacity-50 text-sm py-2 transition-colors"
                >
                  Revenir
                </button>
              </div>
            </div>
          )}

          <button
            onClick={() => { setExpanded(false); setConfirmDurable(false) }}
            disabled={busy != null}
            className="w-full text-xs text-blue-500 hover:text-blue-700 disabled:opacity-40 underline"
          >
            Annuler
          </button>
        </div>
      )}
    </Section>
  )
}

function Section({ title, children }: { title: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="space-y-2.5">
      <p className="text-blue-400 text-xs uppercase tracking-widest font-semibold">{title}</p>
      <div className="bg-white border border-blue-100 rounded-xl p-4 space-y-2.5 shadow-sm">
        {children}
      </div>
    </div>
  )
}

// Un débit réel descend souvent sous le Mb/s (94 kb/s sur un lien au repos).
// L'afficher en Mbps donnerait « 0.1 Mbps » pour toute la plage utile, donc on
// bascule en kbps sous 1 Mbps.
function formatRate(mbps: number): string {
  if (mbps < 1) return `${Math.round(mbps * 1000)} kbps`
  return `${mbps.toFixed(1)} Mbps`
}

/**
 * Rattachement de l'abonné à un client CRM dans UISP.
 *
 * ⚠️ « Absent de UISP » et « non rattaché » sont deux états distincts : dans le
 * premier on ne sait rien (le contrôleur ne connaît pas l'équipement), dans le
 * second UISP le connaît mais ne l'a rattaché à aucun client — donc
 * potentiellement non facturé.
 */
function CrmAttachment({ lr }: { lr: Lr }) {
  if (!lr.uisp_synced_at) {
    return <span className="text-blue-300">— Absent de UISP</span>
  }
  if (lr.uisp_crm_client_id) {
    return (
      <span className="text-green-700 font-semibold">
        ✓ {lr.uisp_crm_client_name ?? 'Client'}
        <span className="ml-1.5 font-mono font-normal text-[11px] text-blue-400">
          id {lr.uisp_crm_client_id}
        </span>
      </span>
    )
  }
  return (
    <span
      className="text-amber-700 font-semibold"
      title={
        lr.uisp_site_name
          ? `Rattaché au site UISP « ${lr.uisp_site_name} », qui n'a aucun client CRM`
          : "Présent dans UISP mais rattaché à aucun client (« unknown »)"
      }
    >
      ⚠ Non rattaché
      {lr.uisp_site_name && (
        <span className="block text-[11px] font-normal text-blue-400">
          site « {lr.uisp_site_name} » sans client CRM
        </span>
      )}
    </span>
  )
}

function MetricRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 text-sm">
      <span className="text-blue-400 shrink-0">{label}</span>
      <span className="text-slate-700 text-right">{value}</span>
    </div>
  )
}

function LinkStatus({ up }: { up: boolean }) {
  return up
    ? <span className="text-green-600 font-semibold">UP</span>
    : <span className="text-red-500 font-semibold">DOWN</span>
}

function SignalValue({ dBm, warn, crit }: { dBm: number; warn: number; crit: number }) {
  const color = dBm < crit ? 'text-red-500' : dBm < warn ? 'text-yellow-500' : 'text-green-600'
  return <span className={`font-semibold ${color}`}>{dBm} dBm</span>
}

function CcqValue({ pct, warn, crit }: { pct: number; warn: number; crit: number }) {
  const color = pct < crit ? 'text-red-500' : pct < warn ? 'text-yellow-500' : 'text-green-600'
  return <span className={`font-semibold ${color}`}>{pct.toFixed(0)} %</span>
}

function LatencyValue({ ms, warn, crit }: { ms: number; warn: number; crit: number }) {
  const color = ms >= crit ? 'text-red-500' : ms >= warn ? 'text-yellow-500' : 'text-green-600'
  return <span className={`font-semibold ${color}`}>{ms.toFixed(1)} ms</span>
}

function VoltageValue({ volts }: { volts: number }) {
  // UISP Power: hardcoded safe range matches voltage_anomaly rule (20–56 V)
  const out = volts < 20 || volts > 56
  return <span className={`font-semibold ${out ? 'text-red-500' : 'text-green-600'}`}>{volts.toFixed(1)} V</span>
}

function BatteryValue({ pct, warn, crit }: { pct: number; warn: number; crit: number }) {
  const color = pct < crit ? 'text-red-500' : pct < warn ? 'text-yellow-500' : 'text-green-600'
  return <span className={`font-semibold ${color}`}>{pct.toFixed(0)} %</span>
}

function LinkPotentialValue({ pct }: { pct: number }) {
  // Hardcoded bands (no env threshold for this informational metric): mirrors
  // the UISP dashboard "Excellent Link" wording — ≥90 % excellent, 70–90 %
  // correct, <70 % weak link.
  const color = pct < 70 ? 'text-red-500' : pct < 90 ? 'text-yellow-500' : 'text-green-600'
  const label = pct < 70 ? 'faible' : pct < 90 ? 'correct' : 'excellent'
  return (
    <span className={`font-semibold ${color}`}>
      {pct.toFixed(0)} % <span className="font-normal opacity-70">({label})</span>
    </span>
  )
}

function RateIdxValue({ idx }: { idx: number }) {
  // Modulation multiplier ("Nx", 1..12) — higher is better. No "expected"
  // reference is collected, so left neutral to avoid implying a false threshold.
  return <span className="font-semibold text-slate-700">{idx.toFixed(0)}×</span>
}




type DiagState = { status: 'idle' | 'loading' | 'done'; result?: DiagResult }

function DiagRow({ label, url }: { label: string; url: string }) {
  const [state, setState] = React.useState<DiagState>({ status: 'idle' })

  const run = async () => {
    setState({ status: 'loading' })
    try {
      const result = await runDiag(url)
      setState({ status: 'done', result })
    } catch {
      setState({ status: 'done', result: { ok: false, message: 'Erreur réseau' } })
    }
  }

  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-blue-400">{label}</span>
      <div className="flex items-center gap-3">
        {state.status === 'idle'    && <span className="text-blue-200 text-xs">—</span>}
        {state.status === 'loading' && <span className="text-blue-400 text-xs animate-pulse">Test en cours…</span>}
        {state.status === 'done' && state.result && (
          <span className={`text-xs font-semibold ${state.result.ok ? 'text-green-600' : 'text-red-500'}`}>
            {state.result.ok
              ? (label.includes('Ping') ? `● ${state.result.message}` : '● OK')
              : (label.includes('Ping') ? '✗ Non joignable' : '✗ Non accessible')}
          </span>
        )}
        <button
          onClick={run}
          disabled={state.status === 'loading'}
          className="text-xs text-blue-500 hover:text-blue-700 disabled:opacity-40 underline transition-colors"
        >
          {state.status === 'idle' ? 'Tester' : '↻ Retester'}
        </button>
      </div>
    </div>
  )
}
