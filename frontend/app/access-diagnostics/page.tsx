'use client'

import React from 'react'
import useSWR from 'swr'
import { endpoints, enrollUisp, enrollUispBulk, fetcher } from '@/lib/api'
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
  const { data, isLoading, mutate } = useSWR<AccessDiagnosticsResponse>(
    endpoints.accessDiagnostics,
    fetcher,
    { refreshInterval: 60_000, keepPreviousData: true },
  )

  const sshRefused = data?.ssh_refused ?? []
  const radioNotInUisp = data?.radio_not_in_uisp ?? []
  const canEnroll = data?.enrollment_available ?? false

  // Enrôlement en cours : id du LR visé, ou 'bulk' pour le lot. Un seul à la
  // fois — chaque CPE prend jusqu'à 45 s (attente de l'adoption par le
  // contrôleur), et lancer un lot pendant une unité rendrait les deux illisibles.
  const [busy, setBusy] = React.useState<number | 'bulk' | null>(null)
  const [report, setReport] = React.useState<{ ok: boolean; text: string } | null>(null)
  // Écrase la clé même si l'équipement pointe déjà sur notre contrôleur. Sert au
  // cas de la clé ORPHELINE (équipement supprimé de UISP : il se connecte mais
  // n'est jamais adopté). ⚠️ Sur un équipement sain, forcer lui fait perdre la
  // clé propre que le contrôleur lui avait attribuée — d'où l'interrupteur
  // séparé, décoché par défaut, plutôt qu'un comportement implicite.
  const [force, setForce] = React.useState(false)

  async function runEnroll(row: RadioNotInUispRow) {
    setBusy(row.id)
    setReport(null)
    try {
      const res = await enrollUisp(row.id, force)
      setReport({ ok: res.ok, text: res.message })
    } catch (e) {
      setReport({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(null)
      mutate()
    }
  }

  async function runEnrollAll() {
    const targets = radioNotInUisp.filter((r) => r.enrollable)
    if (targets.length === 0) return
    if (
      !window.confirm(
        `Poser la clé UISP sur ${targets.length} équipement(s) ?\n\n` +
          `Chaque CPE est joint en SSH et l'opération attend que le contrôleur ` +
          `l'adopte — comptez jusqu'à 45 s par équipement. Aucun redémarrage, ` +
          `aucune coupure pour les abonnés.` +
          (force
            ? `\n\n⚠ MODE FORCER : la clé sera écrasée même sur les équipements ` +
              `déjà provisionnés pour ce contrôleur, ce qui leur fait PERDRE la ` +
              `clé propre attribuée par UISP. À n'utiliser que pour des clés ` +
              `orphelines.`
            : ''),
      )
    ) return
    setBusy('bulk')
    setReport(null)
    try {
      const res = await enrollUispBulk(targets.map((r) => r.id), force)
      const failures = res.results.filter((r) => !r.ok)
      setReport({
        // Un lot qui n'a RIEN enrôlé n'est pas un succès, même sans échec : ce
        // sont des équipements déjà provisionnés dont la clé est probablement
        // orpheline (à reprendre en mode « forcer »).
        ok: res.failed === 0 && res.enrolled > 0,
        text:
          res.message +
          (failures.length
            ? ` Échecs : ${failures.slice(0, 5).map((f) => f.name).join(', ')}` +
              (failures.length > 5 ? ` et ${failures.length - 5} autre(s).` : '.')
            : '') +
          (res.skipped > 0 && res.enrolled === 0
            ? ` Aucun n'a été modifié : leur clé pointe déjà sur ce contrôleur sans` +
              ` qu'ils soient adoptés — cocher « Forcer » pour la réécrire.`
            : ''),
      })
    } catch (e) {
      setReport({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(null)
      mutate()
    }
  }

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
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-sm font-bold text-amber-900">
                Découverts par radio mais absents de UISP — {radioNotInUisp.length}
              </h2>
              <p className="text-xs text-amber-700 mt-0.5">
                Ces clients sont physiquement connectés à une antenne (vus par la découverte
                radio), mais leur MAC n'apparaît dans aucune station renvoyée par UISP : ils ne
                sont pas provisionnés dans l'inventaire — donc potentiellement non facturés.
                L'enrôlement pose la clé du contrôleur sur l'équipement par SSH, sans
                redémarrage ni coupure pour l'abonné.
              </p>
            </div>
            {radioNotInUisp.length > 0 && (
              <div className="shrink-0 flex flex-col items-end gap-1.5">
              <button
                onClick={runEnrollAll}
                disabled={!canEnroll || busy !== null}
                title={
                  canEnroll
                    ? 'Poser la clé UISP sur tous les équipements joignables de cette liste'
                    : "Aucune clé UISP configurée côté serveur (UISP_DEVICE_KEY)"
                }
                className="px-3 py-1.5 rounded-lg text-xs font-semibold border
                           bg-amber-600 text-white border-amber-700 hover:bg-amber-700
                           disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {busy === 'bulk' ? 'Enrôlement en cours…' : 'Tout enrôler dans UISP'}
              </button>
              <label
                className="flex items-center gap-1.5 text-[11px] text-amber-800 cursor-pointer"
                title="Écrase la clé même sur un équipement déjà provisionné pour ce
                       contrôleur. Sur un équipement sain, il perd la clé propre que UISP
                       lui a attribuée — à réserver aux clés orphelines."
              >
                <input
                  type="checkbox"
                  checked={force}
                  onChange={(e) => setForce(e.target.checked)}
                  disabled={busy !== null}
                  className="accent-amber-600"
                />
                Forcer (écraser une clé existante)
              </label>
              </div>
            )}
          </div>
          {!canEnroll && radioNotInUisp.length > 0 && (
            <p className="text-[11px] text-amber-800 mt-1.5 bg-amber-100 border border-amber-300
                          rounded px-2 py-1">
              Enrôlement indisponible : aucune clé de contrôleur configurée. Renseigner
              <code className="mx-1 font-mono">UISP_DEVICE_KEY</code>
              (UISP → Paramètres → Équipements → clé UISP) dans le <code>.env</code> du serveur.
            </p>
          )}
          {report && (
            <p
              className={`text-[11px] mt-1.5 rounded px-2 py-1 border ${
                report.ok
                  ? 'bg-green-50 text-green-800 border-green-200'
                  : 'bg-red-50 text-red-700 border-red-200'
              }`}
            >
              {report.text}
            </p>
          )}
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
                <Th>Enrôlement</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              {radioNotInUisp.map((r) => (
                <RadioRow
                  key={r.id}
                  row={r}
                  canEnroll={canEnroll}
                  force={force}
                  busy={busy === r.id}
                  disabled={busy !== null}
                  onEnroll={() => runEnroll(r)}
                />
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

function RadioRow({
  row, canEnroll, force, busy, disabled, onEnroll,
}: {
  row: RadioNotInUispRow
  canEnroll: boolean
  force: boolean
  busy: boolean
  disabled: boolean
  onEnroll: () => void
}) {
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
      <Td>
        {row.uisp_enrolled_at && !force ? (
          // Enrôlé mais toujours dans cette liste : le contrôleur l'a adopté,
          // le roster ne l'a pas encore repris (sync quotidien). C'est une
          // attente normale, pas un échec — d'où le ton neutre. Le mode
          // « forcer » ré-affiche le bouton : si la ligne persiste bien après le
          // sync, c'est que l'adoption a été perdue et qu'il faut réécrire.
          <span className="text-[11px] text-green-700">
            ✓ Clé posée le {formatTs(row.uisp_enrolled_at)}
            <span className="block text-blue-400">en attente du prochain sync UISP</span>
          </span>
        ) : (
          <button
            onClick={onEnroll}
            disabled={!canEnroll || !row.enrollable || disabled}
            title={
              !canEnroll
                ? 'Aucune clé UISP configurée côté serveur (UISP_DEVICE_KEY)'
                : !row.enrollable
                  ? "Sans identifiants SSH ni adresse IP, rien à joindre sur cet équipement"
                  : "Poser la clé du contrôleur sur cet équipement (jusqu'à 45 s)"
            }
            className="px-2.5 py-1 rounded-md text-[11px] font-semibold border
                       bg-white text-amber-800 border-amber-300 hover:bg-amber-50
                       disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {busy ? 'Enrôlement…' : force ? 'Forcer' : 'Enrôler'}
          </button>
        )}
      </Td>
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
