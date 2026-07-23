'use client'

import React from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type {
  AccessDiagnosticsResponse,
  RadioNotInUispRow,
  SshRefusalStatus,
  SshRefusedRow,
} from '@/lib/types'
import IpLink from '@/components/IpLink'

// Chaque cause de refus SSH, avec son libellé et sa couleur. Toutes en teinte
// « alerte » (le LR est ingérable), nuancées par gravité de l'action requise.
const SSH_STATUS: Record<SshRefusalStatus, { label: string; hint: string; cls: string }> = {
  auth_failed: {
    label: 'Mot de passe invalide',
    hint: "Le LR répond mais rejette l'authentification. Corriger le mot de passe sur la fiche.",
    cls: 'bg-red-50 text-red-700 border-red-200',
  },
  ssh_disabled: {
    label: 'SSH désactivé',
    hint: "Le port SSH est fermé (connexion refusée). Réactiver SSH sur l'équipement.",
    cls: 'bg-amber-50 text-amber-800 border-amber-300',
  },
  host_key_mismatch: {
    label: "Clé d'hôte incompatible",
    hint: "La clé d'hôte a changé sans que la MAC ne concorde. Vérifier l'équipement (re-flash ?).",
    cls: 'bg-purple-50 text-purple-700 border-purple-200',
  },
}

function formatTs(ts: string | null): string {
  if (!ts) return '—'
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  return d.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

export default function AccessDiagnosticsPage() {
  const { data, isLoading } = useSWR<AccessDiagnosticsResponse>(
    endpoints.accessDiagnostics,
    fetcher,
    { refreshInterval: 60_000, keepPreviousData: true },
  )

  const sshRefused = data?.ssh_refused ?? []
  const radioNotInUisp = data?.radio_not_in_uisp ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Diagnostics d'accès</h1>
        <p className="text-blue-400 text-sm mt-1">
          Deux anomalies de gestion du parc abonné que rien d'autre ne signale : les LR qu'on ne
          peut plus piloter en <strong>SSH</strong> (mot de passe, SSH coupé, clé d'hôte), et les
          clients <strong>vus par le radio mais absents de UISP</strong> (non provisionnés).
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 max-w-md">
        <StatCard label="Refusent le SSH" value={sshRefused.length} tone="red" />
        <StatCard label="Hors UISP" value={radioNotInUisp.length} tone="amber" />
      </div>

      {/* ── Section 1 : LR qui refusent le SSH ─────────────────────────────── */}
      <section className="bg-white rounded-xl border border-blue-100 overflow-hidden">
        <header className="px-5 py-3 bg-blue-50 border-b border-blue-100">
          <h2 className="text-sm font-bold text-blue-900">
            LR qui refusent la connexion SSH — {sshRefused.length}
          </h2>
          <p className="text-xs text-blue-500 mt-0.5">
            Le LR est en ligne (il répond au ping) mais on ne peut pas ouvrir de session SSH :
            impossible de le sonder, le bloquer ou le corriger à distance. Seuls les LR encore
            actifs sont listés — un LR éteint n'est pas un refus. Contrôlé à chaque sonde.
          </p>
        </header>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-white text-blue-900">
              <tr>
                <Th>Client</Th>
                <Th>MAC</Th>
                <Th>IP</Th>
                <Th>Site / AP</Th>
                <Th>Cause</Th>
                <Th>Dernier contrôle</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              {sshRefused.map((r) => (
                <SshRow key={r.id} row={r} />
              ))}
            </tbody>
          </table>
        </div>
        {sshRefused.length === 0 && (
          <EmptyRow loading={isLoading} ok="Tous les LR actifs acceptent le SSH." />
        )}
      </section>

      {/* ── Section 2 : découverts par radio, absents de UISP ──────────────── */}
      <section className="bg-white rounded-xl border border-amber-200 overflow-hidden">
        <header className="px-5 py-3 bg-amber-50 border-b border-amber-200">
          <h2 className="text-sm font-bold text-amber-900">
            Découverts par radio mais absents de UISP — {radioNotInUisp.length}
          </h2>
          <p className="text-xs text-amber-700 mt-0.5">
            Ces clients sont physiquement connectés à une antenne (vus par la découverte radio),
            mais leur MAC n'apparaît dans aucune station renvoyée par UISP : ils ne sont pas
            provisionnés dans l'inventaire — donc potentiellement non facturés. À régulariser
            côté UISP.
          </p>
        </header>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-white text-blue-900">
              <tr>
                <Th>Client</Th>
                <Th>MAC</Th>
                <Th>IP</Th>
                <Th>Site / AP</Th>
                <Th>État</Th>
                <Th>Vu par radio</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              {radioNotInUisp.map((r) => (
                <RadioRow key={r.id} row={r} />
              ))}
            </tbody>
          </table>
        </div>
        {radioNotInUisp.length === 0 && (
          <EmptyRow loading={isLoading} ok="Tout ce que le radio voit est provisionné dans UISP." />
        )}
      </section>
    </div>
  )
}

function SshRow({ row }: { row: SshRefusedRow }) {
  const s = SSH_STATUS[row.ssh_status]
  return (
    <tr className="hover:bg-blue-50/40">
      <Td className="font-medium text-blue-900">
        {row.name}
        {row.client_blocked && (
          <span className="ml-1.5 inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold
                           bg-red-100 text-red-700 border border-red-200">
            à couper ⚠
          </span>
        )}
      </Td>
      <Td className="font-mono text-xs text-blue-500">{row.mac ?? '—'}</Td>
      <Td>{row.ip_address ? <IpLink ip={row.ip_address} /> : '—'}</Td>
      <Td className="text-blue-500">
        {row.site ?? '—'}
        {row.ap_name && <span className="block text-[11px] text-blue-400">{row.ap_name}</span>}
      </Td>
      <Td>
        <span className={`inline-block px-2 py-0.5 rounded-md border text-xs font-semibold ${s.cls}`}>
          {s.label}
        </span>
        <p className="text-[11px] text-blue-400 mt-0.5 max-w-md">{s.hint}</p>
        {row.ssh_error && (
          <p className="text-[10px] text-blue-300 mt-0.5 font-mono max-w-md truncate" title={row.ssh_error}>
            {row.ssh_error}
          </p>
        )}
      </Td>
      <Td className="whitespace-nowrap text-blue-500 tabular-nums">{formatTs(row.ssh_checked_at)}</Td>
    </tr>
  )
}

function RadioRow({ row }: { row: RadioNotInUispRow }) {
  const up = row.status === 'up'
  return (
    <tr className="hover:bg-blue-50/40">
      <Td className="font-medium text-blue-900">{row.name}</Td>
      <Td className="font-mono text-xs text-blue-500">{row.mac ?? '—'}</Td>
      <Td>{row.ip_address ? <IpLink ip={row.ip_address} /> : '—'}</Td>
      <Td className="text-blue-500">
        {row.site ?? '—'}
        {row.ap_name && <span className="block text-[11px] text-blue-400">{row.ap_name}</span>}
      </Td>
      <Td>
        <span className={`text-xs font-semibold ${up ? 'text-green-700' : 'text-blue-400'}`}>
          {up ? 'En ligne' : row.status}
        </span>
      </Td>
      <Td className="whitespace-nowrap text-blue-500 tabular-nums">{formatTs(row.last_discovered_at)}</Td>
    </tr>
  )
}

function EmptyRow({ loading, ok }: { loading: boolean; ok: string }) {
  return (
    <p className="px-5 py-8 text-center text-sm text-blue-400">
      {loading ? 'Chargement…' : <span className="text-green-600">✓ {ok}</span>}
    </p>
  )
}

function StatCard({ label, value, tone }: { label: string; value: number; tone: 'red' | 'amber' }) {
  const cls = { red: 'text-red-700', amber: 'text-amber-700' }[tone]
  return (
    <div className="bg-white rounded-xl border border-blue-100 px-4 py-3">
      <p className="text-xs text-blue-400">{label}</p>
      <p className={`text-2xl font-bold mt-0.5 tabular-nums ${value > 0 ? cls : 'text-blue-900'}`}>
        {value}
      </p>
    </div>
  )
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-4 py-2.5 text-left text-xs font-bold uppercase tracking-wide">{children}</th>
  )
}

function Td({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-4 py-2.5 align-top ${className}`}>{children}</td>
}
