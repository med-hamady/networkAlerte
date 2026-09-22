'use client'

import React from 'react'
import useSWR from 'swr'
import { assignUisp, endpoints, enrollUisp, fetcher, searchCrmClients } from '@/lib/api'
import type {
  AccessDiagnosticsResponse,
  CrmClient,
  RadioNotInUispRow,
  SshRefusalStatus,
  SshRefusedRow,
} from '@/lib/types'
import IpLink from '@/components/IpLink'
import { PERM, usePermissions } from '@/lib/permissions'

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
  const { can } = usePermissions()
  // ⚠️ À ne pas confondre avec `canEnroll` plus bas, qui dit si la
  // FONCTIONNALITÉ est disponible (clé du contrôleur configurée). Celui-ci dit
  // si CE COMPTE a le droit de s'en servir : voir les anomalies d'accès et
  // écrire une clé sur un CPE sont deux choses distinctes. C'est le droit que
  // la route unitaire `/devices/{id}/enroll-uisp` exige réellement.
  const mayEnroll = can(PERM.deviceEnrollUisp)
  // Rattacher à un client CRM ÉCRIT dans le contrôleur : un autre pouvoir que
  // poser une clé, donc un autre droit (le même que la route /uisp/assign).
  const mayAssign = can(PERM.uispAssign)
  const { data, isLoading, mutate } = useSWR<AccessDiagnosticsResponse>(
    endpoints.accessDiagnostics,
    fetcher,
    { refreshInterval: 60_000, keepPreviousData: true },
  )

  const sshRefused = data?.ssh_refused ?? []
  const radioNotInUisp = data?.radio_not_in_uisp ?? []
  const canEnroll = data?.enrollment_available ?? false

  // Enrôlement en cours : id du LR visé. Un seul à la fois — chaque CPE prend
  // jusqu'à 45 s (attente de l'adoption par le contrôleur).
  const [busy, setBusy] = React.useState<number | null>(null)
  // Ligne dont la fenêtre de rattachement au client CRM est ouverte.
  const [assigning, setAssigning] = React.useState<RadioNotInUispRow | null>(null)
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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Diagnostics d'accès</h1>
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
            </div>
            {radioNotInUisp.length > 0 && mayEnroll && (
              <label
                className="shrink-0 flex items-center gap-1.5 text-[11px] text-amber-800 cursor-pointer"
                title="S'applique au bouton « Injecter la clé » : écrase la clé même sur un
                       équipement déjà provisionné pour ce contrôleur. Sur un équipement sain,
                       il perd la clé propre que UISP lui a attribuée — à réserver aux clés
                       orphelines."
              >
                <input
                  type="checkbox"
                  checked={force}
                  onChange={(e) => setForce(e.target.checked)}
                  disabled={busy !== null}
                  className="accent-amber-600"
                />
                Forcer l&apos;injection (écraser une clé existante)
              </label>
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
                <Th>Action</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              {radioNotInUisp.map((r) => (
                <RadioRow
                  key={r.id}
                  row={r}
                  canEnroll={canEnroll}
                  mayEnroll={mayEnroll}
                  mayAssign={mayAssign}
                  force={force}
                  busy={busy === r.id}
                  disabled={busy !== null}
                  onEnroll={() => runEnroll(r)}
                  onAssign={() => setAssigning(r)}
                />
              ))}
            </tbody>
          </table>
        </div>
        {radioNotInUisp.length === 0 && (
          <EmptyRow loading={isLoading} ok="Tout ce que le radio voit est provisionné dans UISP." />
        )}
      </section>

      {assigning && (
        <AssignCrmModal
          row={assigning}
          onClose={() => setAssigning(null)}
          onDone={(ok, text) => {
            setAssigning(null)
            setReport({ ok, text })
            mutate()
          }}
        />
      )}
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
  row, canEnroll, mayEnroll, mayAssign, force, busy, disabled, onEnroll, onAssign,
}: {
  row: RadioNotInUispRow
  /** La FONCTIONNALITÉ est disponible (clé du contrôleur configurée). */
  canEnroll: boolean
  /** CE COMPTE a le droit de s'en servir. Deux questions distinctes. */
  mayEnroll: boolean
  /** CE COMPTE peut rattacher un équipement à un client CRM. */
  mayAssign: boolean
  force: boolean
  busy: boolean
  disabled: boolean
  onEnroll: () => void
  onAssign: () => void
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
        <div className="flex flex-col items-start gap-1.5">
          {mayAssign && (
            <button
              onClick={onAssign}
              disabled={!row.mac || disabled}
              title={
                row.mac
                  ? "Rattacher cet équipement au compte de l'abonné dans UISP. S'il est " +
                    'absent de UISP, sa clé est posée d\'abord.'
                  : 'Sans MAC, impossible de désigner cet équipement dans UISP'
              }
              className="px-2.5 py-1 rounded-md text-[11px] font-semibold border whitespace-nowrap
                         bg-amber-600 text-white border-amber-700 hover:bg-amber-700
                         disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Rattacher au client
            </button>
          )}
          {mayEnroll && (row.uisp_enrolled_at && !force ? (
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
                    ? 'Sans identifiants SSH ni adresse IP, rien à joindre sur cet équipement'
                    : "Poser seulement la clé du contrôleur, sans rattacher de client (jusqu'à 45 s)"
              }
              className="px-2.5 py-1 rounded-md text-[11px] font-semibold border whitespace-nowrap
                         bg-white text-amber-800 border-amber-300 hover:bg-amber-50
                         disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {busy ? 'Injection…' : force ? "Forcer l'injection" : 'Injecter la clé'}
            </button>
          ))}
        </div>
      </Td>
    </tr>
  )
}

/**
 * Fenêtre « Rattacher au client » : chercher un client CRM par nom ou par id,
 * choisir son service s'il en a plusieurs, valider.
 *
 * ⚠️ L'id est affiché à côté de chaque nom : deux clients distincts portent le
 * même nom (« Ba, Amadou » = 1361 et 1369). Le service se choisit lui aussi par
 * son id, leurs noms étant souvent identiques.
 */
function AssignCrmModal({
  row, onClose, onDone,
}: {
  row: RadioNotInUispRow
  onClose: () => void
  onDone: (ok: boolean, text: string) => void
}) {
  const [query, setQuery] = React.useState('')
  const [results, setResults] = React.useState<CrmClient[]>([])
  const [searching, setSearching] = React.useState(false)
  const [searchError, setSearchError] = React.useState<string | null>(null)
  const [client, setClient] = React.useState<CrmClient | null>(null)
  const [serviceId, setServiceId] = React.useState<string | null>(null)
  const [running, setRunning] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  // Détenteur actuel, quand le serveur refuse parce que l'équipement est déjà
  // rattaché à un AUTRE client : on propose alors de le déplacer, jamais
  // d'office — une MAC saisie de travers retirerait son matériel à un abonné.
  const [owner, setOwner] = React.useState<{ id: string | null; name: string | null } | null>(null)

  // Recherche à la frappe, avec un délai : inutile d'interroger à chaque lettre.
  React.useEffect(() => {
    const q = query.trim()
    if (!q) {
      setResults([])
      setSearchError(null)
      return
    }
    let cancelled = false
    const t = setTimeout(async () => {
      setSearching(true)
      try {
        const found = await searchCrmClients(q)
        if (!cancelled) {
          setResults(found)
          setSearchError(null)
        }
      } catch (e) {
        if (!cancelled) setSearchError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setSearching(false)
      }
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(t)
    }
  }, [query])

  function pick(c: CrmClient) {
    setClient(c)
    setServiceId(c.services.length === 1 ? c.services[0].crm_service_id : null)
    setError(null)
    setOwner(null)
  }

  const needsService = (client?.services.length ?? 0) > 1

  async function submit(force: boolean) {
    if (!client || !row.mac) return
    setRunning(true)
    setError(null)
    try {
      const res = await assignUisp({
        mac: row.mac,
        crm_client_id: client.crm_client_id,
        crm_service_id: needsService ? serviceId : null,
        force,
      })
      if (res.assigned) {
        onDone(true, res.message ?? `Équipement rattaché au client ${client.crm_client_id}.`)
      } else if (res.pending_registration) {
        // Pas un échec : la clé est posée, le contrôleur n'a pas encore vu
        // l'équipement. Un nouveau clic dans quelques secondes termine le travail.
        onDone(false, res.message ?? 'Clé posée, équipement pas encore déclaré à UISP : réessayer.')
      } else if (res.error_code === 'device_already_assigned') {
        setOwner({ id: res.current_crm_client_id ?? null, name: res.current_client_name ?? null })
      } else {
        setError(res.message ?? res.error_code ?? 'Rattachement échoué.')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  // Échap ferme la fenêtre (sauf pendant un rattachement, qui ne s'annule pas).
  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && !running) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [running, onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-slate-900/40 backdrop-blur-[2px] p-4 pt-[10vh]"
      onClick={() => !running && onClose()}
    >
      <div
        className="w-full max-w-lg bg-white rounded-2xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* En-tête : le geste, puis l'équipement visé */}
        <header className="px-6 pt-5 pb-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-3">
              <span className="flex items-center justify-center w-10 h-10 rounded-full bg-blue-50 text-blue-700 shrink-0">
                <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.8} className="w-5 h-5" aria-hidden>
                  <path d="M8.5 11.5a3 3 0 0 0 4.2 0l2.6-2.6a3 3 0 0 0-4.2-4.2l-1 1" strokeLinecap="round" />
                  <path d="M11.5 8.5a3 3 0 0 0-4.2 0l-2.6 2.6a3 3 0 0 0 4.2 4.2l1-1" strokeLinecap="round" />
                </svg>
              </span>
              <div>
                <h3 className="text-base font-bold text-slate-800">Rattacher au client CRM</h3>
                <p className="text-xs text-slate-500 mt-0.5">Associer cet équipement à un abonné dans UISP</p>
              </div>
            </div>
            <button
              onClick={onClose}
              disabled={running}
              aria-label="Fermer"
              className="p-1.5 -mr-1.5 rounded-full text-slate-400 hover:text-slate-700 hover:bg-slate-100 disabled:opacity-40"
            >
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={2} className="w-4 h-4" aria-hidden>
                <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
              </svg>
            </button>
          </div>

          <div className="mt-4 flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-4 py-2.5">
            <div className="min-w-0">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">Équipement</p>
              <p className="text-sm font-semibold text-slate-800 truncate">{row.name}</p>
            </div>
            <span className="font-mono text-[11px] text-slate-600 bg-white border border-slate-200 rounded-full px-2.5 py-0.5 shrink-0">
              {row.mac}
            </span>
          </div>
        </header>

        <div className="px-6 pb-5 space-y-3">
          {!client ? (
            <>
              <label className="block text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                Client CRM
              </label>
              <div className="relative">
                <svg
                  aria-hidden
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                  className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none"
                >
                  <circle cx="9" cy="9" r="6" />
                  <path d="M14 14l4 4" strokeLinecap="round" />
                </svg>
                <input
                  autoFocus
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    // Entrée choisit le premier résultat : le cas courant est
                    // une recherche par id, qui n'en rend qu'un.
                    if (e.key === 'Enter' && results.length > 0) pick(results[0])
                  }}
                  placeholder="Nom ou id du client"
                  className="w-full pl-10 pr-10 py-2.5 text-sm rounded-full bg-slate-100 border border-transparent
                             placeholder:text-slate-400 focus:bg-white focus:border-blue-300
                             focus:outline-none focus:ring-2 focus:ring-blue-100 transition-colors"
                />
                {searching && (
                  <span
                    aria-hidden
                    className="absolute right-3.5 top-1/2 -translate-y-1/2 w-4 h-4 rounded-full border-2 border-slate-200 border-t-blue-600 animate-spin"
                  />
                )}
              </div>

              {query.trim() && (
                <div className="max-h-72 overflow-y-auto -mx-1 px-1 space-y-1">
                  {searchError ? (
                    <p className="rounded-xl bg-red-50 px-4 py-3 text-xs text-red-700">{searchError}</p>
                  ) : searching && results.length === 0 ? (
                    <p className="px-4 py-3 text-xs text-slate-400">Recherche…</p>
                  ) : results.length === 0 ? (
                    <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500">
                      Aucun client trouvé. Un client sans service n&apos;est pas rattachable.
                    </p>
                  ) : (
                    results.map((c, i) => (
                      <button
                        key={c.crm_client_id}
                        onClick={() => pick(c)}
                        className={`group w-full text-left flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors
                                    hover:bg-blue-50 ${i === 0 ? 'bg-slate-50' : ''}`}
                      >
                        <ClientAvatar name={c.name} />
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-semibold text-slate-800 truncate">
                            {c.name ?? '(sans nom)'}
                          </span>
                          <span className="block text-[11px] text-slate-500">
                            Client <span className="font-mono">#{c.crm_client_id}</span>
                          </span>
                          <ServiceChips services={c.services} />
                        </span>
                        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={2}
                          className="w-4 h-4 text-slate-300 group-hover:text-blue-700 shrink-0" aria-hidden>
                          <path d="M8 5l5 5-5 5" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </button>
                    ))
                  )}
                </div>
              )}
            </>
          ) : (
            <>
              <label className="block text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                Client CRM
              </label>
              <div className="flex items-center gap-3 rounded-xl border border-blue-200 bg-blue-50/60 px-3 py-2.5">
                <ClientAvatar name={client.name} />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold text-slate-800 truncate">
                    {client.name ?? '(sans nom)'}
                  </span>
                  <span className="block text-[11px] text-slate-500">
                    Client <span className="font-mono">#{client.crm_client_id}</span>
                  </span>
                  {!needsService && <ServiceChips services={client.services} />}
                </span>
                <button
                  onClick={() => setClient(null)}
                  disabled={running}
                  className="text-xs font-semibold text-blue-700 hover:text-blue-900 px-2 py-1 rounded-full
                             hover:bg-blue-100 disabled:opacity-40"
                >
                  Changer
                </button>
              </div>

              {needsService && (
                <fieldset className="space-y-1.5">
                  <legend className="text-xs font-semibold text-slate-700 mb-1.5">
                    Ce client a {client.services.length} services — lequel ?
                  </legend>
                  {client.services.map((svc) => {
                    const checked = serviceId === svc.crm_service_id
                    return (
                      <label
                        key={svc.crm_service_id}
                        className={`flex items-center gap-3 rounded-xl border px-3 py-2 text-sm cursor-pointer transition-colors ${
                          checked ? 'border-blue-300 bg-blue-50' : 'border-slate-200 hover:bg-slate-50'
                        }`}
                      >
                        <input
                          type="radio"
                          name="crm-service"
                          checked={checked}
                          onChange={() => setServiceId(svc.crm_service_id)}
                          disabled={running}
                          className="accent-blue-700"
                        />
                        <span className="flex-1 text-slate-800">{svc.name ?? '(sans nom)'}</span>
                        <span className="text-[11px] font-mono text-slate-400">#{svc.crm_service_id}</span>
                      </label>
                    )
                  })}
                </fieldset>
              )}

              {owner && (
                <div className="rounded-xl bg-red-50 border border-red-200 px-4 py-3 text-xs text-red-800">
                  Cet équipement est déjà rattaché à{' '}
                  <strong>{owner.name ?? 'un autre client'}</strong>
                  {owner.id && <span className="font-mono"> (#{owner.id})</span>}. Le déplacer
                  retire son équipement à cet abonné.
                </div>
              )}
              {error && (
                <p className="rounded-xl bg-red-50 border border-red-200 px-4 py-3 text-xs text-red-700">
                  {error}
                </p>
              )}
              {running && (
                <div className="flex items-start gap-2.5 rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-600">
                  <span
                    aria-hidden
                    className="mt-0.5 w-3.5 h-3.5 shrink-0 rounded-full border-2 border-slate-200 border-t-blue-600 animate-spin"
                  />
                  <span>
                    Rattachement en cours… Si l&apos;équipement est absent de UISP, sa clé est
                    posée puis on attend qu&apos;il se déclare : jusqu&apos;à 1 min 40.
                  </span>
                </div>
              )}
            </>
          )}
        </div>

        <footer className="px-6 py-4 bg-slate-50 border-t border-slate-100 flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={running}
            className="px-4 py-2 rounded-full text-xs font-semibold text-slate-600 hover:bg-slate-200/70 disabled:opacity-40"
          >
            Annuler
          </button>
          {owner ? (
            <button
              onClick={() => submit(true)}
              disabled={running}
              className="px-5 py-2 rounded-full text-xs font-semibold bg-red-600 text-white hover:bg-red-700
                         shadow-sm disabled:opacity-40"
            >
              {running ? 'Rattachement…' : 'Déplacer quand même'}
            </button>
          ) : (
            <button
              onClick={() => submit(false)}
              disabled={!client || (needsService && !serviceId) || running}
              className="px-5 py-2 rounded-full text-xs font-semibold bg-blue-700 text-white hover:bg-blue-800
                         shadow-sm disabled:bg-slate-200 disabled:text-slate-400 disabled:shadow-none disabled:cursor-not-allowed"
            >
              {running ? 'Rattachement…' : 'Rattacher'}
            </button>
          )}
        </footer>
      </div>
    </div>
  )
}

// Pastille du client : l'icône « client » de la marque (dessin noir au trait,
// teinté en bleu pétrole par masque — une <img> resterait noire).
function ClientAvatar(_: { name: string | null }) {
  const src = 'url(/brand/icons/client.png)'
  return (
    <span aria-hidden className="flex items-center justify-center w-9 h-9 rounded-full bg-blue-50 shrink-0">
      <span
        className="inline-block w-5 h-5 bg-current text-blue-700"
        style={{
          maskImage: src, WebkitMaskImage: src,
          maskSize: 'contain', WebkitMaskSize: 'contain',
          maskRepeat: 'no-repeat', WebkitMaskRepeat: 'no-repeat',
          maskPosition: 'center', WebkitMaskPosition: 'center',
        }}
      />
    </span>
  )
}

// Service(s) CRM du client, sous son nom — information de premier plan : c'est
// au SERVICE (donc à l'abonnement) que l'équipement est rattaché. Tous sont
// listés, jamais tronqués.
function ServiceChips({ services }: { services: CrmClient['services'] }) {
  if (services.length === 0) return null
  return (
    <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
        {services.length > 1 ? `${services.length} services` : 'Service'}
      </span>
      {services.map((svc) => (
        <span
          key={svc.crm_service_id}
          className="inline-flex items-center gap-1.5 rounded-md border border-blue-200 bg-blue-50
                     px-2 py-0.5 text-xs font-semibold text-blue-900"
        >
          {svc.name ?? '(sans nom)'}
          <span className="font-mono text-[10px] font-medium text-blue-500">#{svc.crm_service_id}</span>
        </span>
      ))}
    </span>
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
