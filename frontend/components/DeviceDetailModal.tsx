'use client'

import React from 'react'
import useSWR from 'swr'
import { endpoints, fetcher, runDiag } from '@/lib/api'
import type { DiagResult } from '@/lib/api'
import type { Device, DeviceMetrics } from '@/lib/types'
import { deviceLabel, formatDate, timeAgo, formatBytes, formatUptime, parentRocketId } from '@/lib/types'
import { useThresholds } from '@/lib/useThresholds'
import DeviceImage from './DeviceImage'
import DevicePolicyOverridesEditor from './DevicePolicyOverridesEditor'

const RADIO_TYPES = new Set(['rocket', 'lr'])
const REFRESH      = 15_000
const LIVE_REFRESH = 10_000

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
  const isRadio  = RADIO_TYPES.has(device.device_type)
  const isSwitch = device.device_type === 'uisp_switch'
  const isPower  = device.device_type === 'uisp_power'
  const isUp     = device.status === 'up'
  const isDown   = device.status === 'down'
  const isRocket = device.device_type === 'rocket'

  const thresholds = useThresholds()

  // Children LRs linked to this Rocket
  const linkedLRs = isRocket
    ? devices.filter(d => parentRocketId(d) === device.id)
    : []

  const { data: dbMetrics } = useSWR<DeviceMetrics>(
    (isRadio || isSwitch || isPower) ? endpoints.deviceMetrics(device.id) : null,
    fetcher,
    { refreshInterval: REFRESH },
  )
  // Radio values fluctuate per-second, so the 60 s poll snapshot lags the
  // device dashboard. For LR/Rocket, also pull a live reading from the
  // (parent) Rocket API and let it override the DB values key-by-key: the
  // DB shows instantly, live replaces as soon as it lands.
  const { data: liveMetrics, isValidating: liveValidating } = useSWR<DeviceMetrics>(
    isRadio ? endpoints.deviceMetricsLive(device.id) : null,
    fetcher,
    { refreshInterval: LIVE_REFRESH, shouldRetryOnError: false },
  )
  const metrics: DeviceMetrics | undefined =
    dbMetrics || liveMetrics ? { ...(dbMetrics ?? {}), ...(liveMetrics ?? {}) } : undefined
  const liveState: 'live' | 'loading' | 'deferred' =
    liveMetrics ? 'live' : liveValidating ? 'loading' : 'deferred'

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
        <DeviceImage type={device.device_type} size="lg" />

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
        {!isUp && !isDown && (
          <div className="flex items-center gap-2 bg-white border border-blue-200 px-4 py-1.5 rounded-full shadow-sm">
            <span className="inline-flex h-2.5 w-2.5 rounded-full bg-blue-300" />
            <span className="text-blue-400 text-sm font-bold">INCONNU</span>
          </div>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 px-6 py-5 space-y-6">

        {/* LR associés (Rocket only) */}
        {isRocket && (
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
          <MetricRow label="Adresse IP"    value={<span className="font-mono text-blue-700">{device.ip_address}</span>} />
          <MetricRow label="Type"          value={deviceLabel(device)} />
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
        </Section>

        {/* Radio metrics */}
        {isRadio && metrics && (
          <Section title={<>Métriques radio<LiveBadge state={liveState} /></>}>
            {metrics.eth_if_up?.value != null && device.device_type === 'rocket' && (
              <MetricRow label="Lien switch (eth0)" value={<LinkStatus up={metrics.eth_if_up.value === 1} />} />
            )}
            {metrics.radio_if_up?.value != null && (
              <MetricRow label="Interface radio" value={<LinkStatus up={metrics.radio_if_up.value === 1} />} />
            )}
            {metrics.distance_m?.value    != null && <MetricRow label="Distance"  value={`${metrics.distance_m.value.toFixed(0)} m`} />}
            {metrics.signal_dbm?.value    != null && <MetricRow label="Signal UL (AP)"  value={<SignalValue dBm={metrics.signal_dbm.value} warn={thresholds.signal_warning_dbm} crit={thresholds.signal_critical_dbm} />} />}
            {metrics.noise_dbm?.value     != null && <MetricRow label="Bruit (AP)"      value={`${metrics.noise_dbm.value} dBm`} />}
            {metrics.cinr_db?.value       != null && <MetricRow label="CINR DL"         value={`${metrics.cinr_db.value} dB`} />}
            {metrics.ul_cinr_db?.value    != null && <MetricRow label="CINR UL"         value={`${metrics.ul_cinr_db.value} dB`} />}
            {metrics.ccq_pct?.value       != null && <MetricRow label="CCQ DL"          value={<CcqValue pct={metrics.ccq_pct.value} warn={thresholds.ccq_warning_pct} crit={thresholds.ccq_critical_pct} />} />}
            {metrics.ul_ccq_pct?.value    != null && <MetricRow label="CCQ UL"          value={<CcqValue pct={metrics.ul_ccq_pct.value} warn={thresholds.ccq_warning_pct} crit={thresholds.ccq_critical_pct} />} />}
            {metrics.tx_rate_mbps?.value  != null && <MetricRow label="Débit DL"        value={`${metrics.tx_rate_mbps.value.toFixed(1)} Mbps`} />}
            {metrics.rx_rate_mbps?.value  != null && <MetricRow label="Débit UL"        value={`${metrics.rx_rate_mbps.value.toFixed(1)} Mbps`} />}
            {metrics.tx_ideal_mbps?.value != null && <MetricRow label="Capacité DL"     value={`${metrics.tx_ideal_mbps.value.toFixed(1)} Mbps`} />}
            {metrics.rx_ideal_mbps?.value != null && <MetricRow label="Capacité UL"     value={`${metrics.rx_ideal_mbps.value.toFixed(1)} Mbps`} />}

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

        {isPower && !metrics && isUp && (
          <p className="text-blue-300 text-sm italic">Métriques UISP Power en attente de collecte…</p>
        )}

        {/* Diagnostics */}
        {device.device_type === 'lr' && (
          <Section title="Diagnostics">
            <DiagRow label="SSH"          url={endpoints.checkSsh(device.id)} />
            <DiagRow label="Ping 8.8.8.8" url={endpoints.checkPing(device.id)} />
          </Section>
        )}

        {device.device_type === 'client_modem' && (
          <Section title="Diagnostics">
            <DiagRow label="Ping depuis le LR" url={endpoints.pingFromLr(device.id)} />
          </Section>
        )}

        {device.notes && (
          <Section title="Notes">
            <p className="text-slate-600 text-sm">{device.notes}</p>
          </Section>
        )}

        <DevicePolicyOverridesEditor device={device} />
      </div>
    </>
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
        <p className="text-blue-400 text-xs font-mono mt-0.5">{lr.ip_address}</p>
        {lr.location && <p className="text-slate-400 text-xs truncate">{lr.location}</p>}
      </div>

      {/* Arrow */}
      <svg className="w-4 h-4 text-blue-300 group-hover:text-blue-500 transition-colors shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
      </svg>
    </button>
  )
}

/* ─── Sub-components ─── */

function LiveBadge({ state }: { state: 'live' | 'loading' | 'deferred' }) {
  if (state === 'live') {
    return (
      <span className="ml-2 inline-flex items-center gap-1 text-[10px] font-semibold text-green-600 normal-case tracking-normal align-middle">
        <span className="relative flex h-1.5 w-1.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-70" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-green-500" />
        </span>
        temps réel
      </span>
    )
  }
  if (state === 'loading') {
    return (
      <span className="ml-2 text-[10px] font-medium text-blue-400 normal-case tracking-normal align-middle animate-pulse">
        actualisation…
      </span>
    )
  }
  return (
    <span
      className="ml-2 text-[10px] text-blue-300 normal-case tracking-normal align-middle"
      title="API du Rocket injoignable — affichage du dernier relevé (≤ 60 s)"
    >
      différé ≤ 60 s
    </span>
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
              ? (label.includes('Ping') ? '● Joignable' : '● OK')
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
