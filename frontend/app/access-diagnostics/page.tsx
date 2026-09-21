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

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 p-4 pt-[10vh]"
      onClick={() => !running && onClose()}
    >
      <div
        className="w-full max-w-lg bg-white rounded-xl border border-blue-100 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="px-5 py-3 border-b border-blue-100">
          <h3 className="text-sm font-bold text-blue-900">Rattacher au client CRM</h3>
          <p className="text-[11px] text-blue-400 mt-0.5">
            {row.name} · <span className="font-mono">{row.mac}</span>
          </p>
        </header>

        <div className="px-5 py-4 space-y-3">
          {!client ? (
            <>
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Nom ou id du client CRM"
                className="w-full px-3 py-2 text-sm rounded-lg border border-blue-200
                           focus:outline-none focus:ring-2 focus:ring-amber-300"
              />
              {query.trim() && (
                <div className="max-h-72 overflow-y-auto rounded-lg border border-blue-50 divide-y divide-blue-50">
                  {searchError ? (
                    <p className="px-3 py-3 text-xs text-red-700">{searchError}</p>
                  ) : searching && results.length === 0 ? (
                    <p className="px-3 py-3 text-xs text-blue-400">Recherche…</p>
                  ) : results.length === 0 ? (
                    <p className="px-3 py-3 text-xs text-blue-400">
                      Aucun client trouvé. Un client sans service n&apos;est pas rattachable.
                    </p>
                  ) : (
                    results.map((c) => (
                      <button
                        key={c.crm_client_id}
                        onClick={() => pick(c)}
                        className="w-full text-left px-3 py-2 hover:bg-amber-50 flex items-baseline gap-2"
                      >
                        <span className="text-sm text-blue-900">{c.name ?? '(sans nom)'}</span>
                        <span className="text-[11px] font-mono text-blue-400">id {c.crm_client_id}</span>
                        {c.services.length > 1 && (
                          <span className="ml-auto text-[10px] text-amber-700">
                            {c.services.length} services
                          </span>
                        )}
                      </button>
                    ))
                  )}
                </div>
              )}
            </>
          ) : (
            <>
              <div className="flex items-baseline justify-between gap-2 rounded-lg bg-amber-50
                              border border-amber-200 px-3 py-2">
                <span className="text-sm text-blue-900">
                  {client.name ?? '(sans nom)'}
                  <span className="ml-2 text-[11px] font-mono text-blue-400">id {client.crm_client_id}</span>
                </span>
                <button
                  onClick={() => setClient(null)}
                  disabled={running}
                  className="text-[11px] text-amber-800 underline disabled:opacity-40"
                >
                  changer
                </button>
              </div>

              {needsService && (
                <fieldset className="space-y-1">
                  <legend className="text-xs font-semibold text-blue-900 mb-1">
                    Ce client a {client.services.length} services — lequel ?
                  </legend>
                  {client.services.map((svc) => (
                    <label key={svc.crm_service_id} className="flex items-center gap-2 text-sm cursor-pointer">
                      <input
                        type="radio"
                        name="crm-service"
                        checked={serviceId === svc.crm_service_id}
                        onChange={() => setServiceId(svc.crm_service_id)}
                        disabled={running}
                        className="accent-amber-600"
                      />
                      <span className="text-blue-900">{svc.name ?? '(sans nom)'}</span>
                      <span className="text-[11px] font-mono text-blue-400">id {svc.crm_service_id}</span>
                    </label>
                  ))}
                </fieldset>
              )}

              {owner && (
                <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-800">
                  Cet équipement est déjà rattaché à{' '}
                  <strong>{owner.name ?? 'un autre client'}</strong>
                  {owner.id && <span className="font-mono"> (id {owner.id})</span>}. Le déplacer
                  retire son équipement à cet abonné.
                </div>
              )}
              {error && (
                <p className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
                  {error}
                </p>
              )}
              {running && (
                <p className="text-[11px] text-blue-400">
                  Rattachement en cours… Si l&apos;équipement est absent de UISP, sa clé est
                  posée puis on attend qu&apos;il se déclare : jusqu&apos;à 1 min 40.
                </p>
              )}
            </>
          )}
        </div>

        <footer className="px-5 py-3 border-t border-blue-100 flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={running}
            className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-blue-200
                       text-blue-900 bg-white hover:bg-blue-50 disabled:opacity-40"
          >
            Annuler
          </button>
          {owner ? (
            <button
              onClick={() => submit(true)}
              disabled={running}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold border
                         bg-red-600 text-white border-red-700 hover:bg-red-700 disabled:opacity-40"
            >
              {running ? 'Rattachement…' : 'Déplacer quand même'}
            </button>
          ) : (
            <button
              onClick={() => submit(false)}
              disabled={!client || (needsService && !serviceId) || running}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold border
                         bg-amber-600 text-white border-amber-700 hover:bg-amber-700
                         disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {running ? 'Rattachement…' : 'Rattacher'}
            </button>
          )}
        </footer>
      </div>
    </div>
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
