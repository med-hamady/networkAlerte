'use client'

import React from 'react'
import useSWR from 'swr'
import { endpoints, fetcher, runDiag } from '@/lib/api'
import type { DiagResult } from '@/lib/api'
import type { Device, DeviceMetrics } from '@/lib/types'
import { deviceTypeLabel, formatDate, timeAgo, formatBytes, formatUptime } from '@/lib/types'
import DeviceImage from './DeviceImage'
import DevicePolicyOverridesEditor from './DevicePolicyOverridesEditor'

const RADIO_TYPES = new Set(['ltu_rocket', 'ltu_lr'])
const REFRESH     = 15_000

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
  const isUp     = device.status === 'up'
  const isDown   = device.status === 'down'
  const isRocket = device.device_type === 'ltu_rocket'

  // Children LRs linked to this Rocket
  const linkedLRs = isRocket
    ? devices.filter(d => d.parent_id === device.id)
    : []

  const { data: metrics } = useSWR<DeviceMetrics>(
    (isRadio || isSwitch) ? endpoints.deviceMetrics(device.id) : null,
    fetcher,
    { refreshInterval: REFRESH },
  )

  return (
    <>
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-blue-100 bg-white sticky top-0 z-10">
        <div>
          <p className="font-bold text-slate-800 text-base">{device.name}</p>
          <p className="text-blue-400 text-xs mt-0.5">{deviceTypeLabel(device.device_type)}</p>
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
                  <code className="bg-blue-50 px-1 rounded">PATCH /api/v1/devices/{'<id>'}  {"{"}"parent_id": {device.id}{"}"}</code>
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
          <MetricRow label="Type"          value={deviceTypeLabel(device.device_type)} />
          {device.model    && <MetricRow label="Modèle"       value={device.model} />}
          {device.location && <MetricRow label="Localisation" value={device.location} />}
          <MetricRow
            label="Dernière vue"
            value={<span className={isDown ? 'text-red-500' : 'text-slate-600'}>{timeAgo(device.last_seen)}</span>}
          />
          <MetricRow label="Ajouté le" value={formatDate(device.created_at)} />
          {metrics?.uptime_seconds?.value != null && (
            <MetricRow label="Uptime" value={formatUptime(metrics.uptime_seconds.value)} />
          )}
        </Section>

        {/* Radio metrics */}
        {isRadio && metrics && (
          <Section title="Métriques radio">
            {metrics.eth_if_up?.value != null && device.device_type === 'ltu_rocket' && (
              <MetricRow label="Lien switch (eth0)" value={<LinkStatus up={metrics.eth_if_up.value === 1} />} />
            )}
            {metrics.radio_if_up?.value != null && (
              <MetricRow label="Interface radio" value={<LinkStatus up={metrics.radio_if_up.value === 1} />} />
            )}
            {metrics.distance_m?.value    != null && <MetricRow label="Distance"  value={`${metrics.distance_m.value.toFixed(0)} m`} />}
            {metrics.signal_dbm?.value    != null && <MetricRow label="Signal UL (AP)"  value={<SignalValue dBm={metrics.signal_dbm.value} />} />}
            {metrics.noise_dbm?.value     != null && <MetricRow label="Bruit (AP)"      value={`${metrics.noise_dbm.value} dBm`} />}
            {metrics.cinr_db?.value       != null && <MetricRow label="CINR DL"         value={`${metrics.cinr_db.value} dB`} />}
            {metrics.ul_cinr_db?.value    != null && <MetricRow label="CINR UL"         value={`${metrics.ul_cinr_db.value} dB`} />}
            {metrics.ccq_pct?.value       != null && <MetricRow label="CCQ DL"          value={<CcqValue pct={metrics.ccq_pct.value} />} />}
            {metrics.ul_ccq_pct?.value    != null && <MetricRow label="CCQ UL"          value={<CcqValue pct={metrics.ul_ccq_pct.value} />} />}
            {metrics.tx_rate_mbps?.value  != null && <MetricRow label="Débit DL"        value={`${metrics.tx_rate_mbps.value.toFixed(1)} Mbps`} />}
            {metrics.rx_rate_mbps?.value  != null && <MetricRow label="Débit UL"        value={`${metrics.rx_rate_mbps.value.toFixed(1)} Mbps`} />}
            {metrics.tx_ideal_mbps?.value != null && <MetricRow label="Capacité DL"     value={`${metrics.tx_ideal_mbps.value.toFixed(1)} Mbps`} />}
            {metrics.rx_ideal_mbps?.value != null && <MetricRow label="Capacité UL"     value={`${metrics.rx_ideal_mbps.value.toFixed(1)} Mbps`} />}

            {metrics.remote_signal_dbm?.value != null && (
              <>
                <div className="border-t border-blue-100 my-1" />
                <p className="text-blue-300 text-xs uppercase tracking-wider">CPE distant</p>
                <MetricRow label="Signal DL (CPE)" value={<SignalValue dBm={metrics.remote_signal_dbm.value} />} />
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

        {/* Diagnostics */}
        {device.device_type === 'ltu_lr' && (
          <Section title="Diagnostics">
            <DiagRow label="SSH"          url={endpoints.checkSsh(device.id)} />
            <DiagRow label="Ping 8.8.8.8" url={endpoints.checkPing(device.id)} />
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
        <DeviceImage type="ltu_lr" size="sm" />
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

function Section({ title, children }: { title: string; children: React.ReactNode }) {
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

function SignalValue({ dBm }: { dBm: number }) {
  const color = dBm < -80 ? 'text-red-500' : dBm < -70 ? 'text-yellow-500' : 'text-green-600'
  return <span className={`font-semibold ${color}`}>{dBm} dBm</span>
}

function CcqValue({ pct }: { pct: number }) {
  const color = pct < 50 ? 'text-red-500' : pct < 75 ? 'text-yellow-500' : 'text-green-600'
  return <span className={`font-semibold ${color}`}>{pct.toFixed(0)} %</span>
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
