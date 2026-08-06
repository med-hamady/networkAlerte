'use client'

import React from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type {
  FaiAttentionRow, FaiEvidence, FaiJournalEntry, FaiJournalResponse,
} from '@/lib/types'
import IpLink from '@/components/IpLink'

type StatusFilter = '' | 'ok' | 'failed' | 'abandoned'

const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: '',          label: 'Tout'                },
  { value: 'ok',        label: 'Appliqué'            },
  { value: 'failed',    label: 'Non appliqué'        },
  { value: 'abandoned', label: 'Abandonné ⚠'         },
]

// Une action = ce qu'on a essayé de faire. La couleur porte le sens métier :
// rouge = coupure, vert = rétablissement, ambre = échec définitif.
const ACTION_STYLE: Record<FaiJournalEntry['action'], { label: string; cls: string }> = {
  BLOCK:    { label: 'Blocage',        cls: 'bg-red-50 text-red-700 border-red-200'       },
  UNBLOCK:  { label: 'Déblocage',      cls: 'bg-green-50 text-green-700 border-green-200' },
  RETRY_OK: { label: 'Rattrapé',       cls: 'bg-blue-50 text-blue-700 border-blue-200'    },
  ABANDON:  { label: 'Abandonné',      cls: 'bg-amber-50 text-amber-800 border-amber-300' },
  // Rien n'a été tenté sur l'équipement : l'adresse de la fiche répondait, mais
  // c'était un AUTRE abonné. Violet pour ne pas le confondre avec une panne
  // (ambre) — ici il n'y a rien à réparer sur le terrain, c'est la fiche qui
  // est périmée, et elle se corrigera dès que la découverte reverra le client.
  IDENT_KO: { label: 'Identité refusée', cls: 'bg-purple-50 text-purple-700 border-purple-200' },
  // Le repli : la coupure n'a pas pu être posée sur l'équipement du client, elle
  // l'a été sur le routeur de cœur. Teinte ardoise pour marquer « autre plan » —
  // ce n'est ni un succès nominal (rouge/vert) ni un échec (ambre).
  ROUTER_BLOCK:   { label: 'Coupé (routeur)',   cls: 'bg-slate-100 text-slate-700 border-slate-300' },
  ROUTER_UNBLOCK: { label: 'Rétabli (routeur)', cls: 'bg-slate-100 text-slate-700 border-slate-300' },
}

// Quel SYSTÈME a appelé. `script` = blocage de masse (migration depuis le MikroTik).
const SOURCE_LABEL: Record<string, string> = {
  payment: 'Système de paiement',
  enforce: 'Renforcement auto',
  script:  'Blocage de masse',
}

// QUI est derrière l'action — distinct de l'origine : les gestes manuels d'un
// opérateur et les campagnes automatiques passent par le même système de paiement.
// Un e-mail est affiché tel quel ; seuls les deux libellés automatiques connus
// sont traduits, pour qu'on ne les lise pas comme des noms d'agents.
const USER_LABEL: Record<string, string> = {
  'auto system': 'Campagne impayés (auto)',
  'auto retry':  'Rejeu automatique (auto)',
}

function userLabel(user: string | null): string | null {
  if (!user) return null
  return USER_LABEL[user.trim().toLowerCase()] ?? user
}

function formatTs(ts: string): string {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  return d.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

export default function FaiJournalPage() {
  // Entrée dont on affiche la preuve d'exécution (null = modale fermée).
  const [proofOf, setProofOf] = React.useState<FaiJournalEntry | null>(null)
  const [status, setStatus] = React.useState<StatusFilter>('')
  const [search, setSearch] = React.useState('')
  const [debounced, setDebounced] = React.useState('')
  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), 250)
    return () => clearTimeout(t)
  }, [search])

  const { data, isLoading } = useSWR<FaiJournalResponse>(
    endpoints.faiJournal(status, debounced),
    fetcher,
    { refreshInterval: 30_000, keepPreviousData: true },
  )

  const stats = data?.stats ?? { total: 0, ok: 0, failed: 0, abandoned: 0 }
  const entries = data?.entries ?? []
  const attention = data?.attention ?? []
  const blocking = attention.filter((r) => r.kind === 'unenforceable')
  const pending = attention.filter((r) => r.kind === 'pending')

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Journal des blocages</h1>
        <p className="text-blue-400 text-sm mt-1">
          Chaque coupure et rétablissement, qu'il vienne du <strong>système de paiement</strong>{' '}
          ou du renforcement automatique. Un ordre non appliqué est rejoué toutes les 2 minutes.
          Quand la coupure ne peut pas être posée sur l'équipement du client, elle l'est{' '}
          <strong>sur le routeur</strong> : le client est coupé quand même, et la règle du routeur
          est retirée dès que la coupure locale aboutit.
        </p>
      </div>

      {/* Compteurs sur la fenêtre affichée */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Actions" value={stats.total} tone="neutral" />
        <StatCard label="Appliquées" value={stats.ok} tone="green" />
        <StatCard label="En attente de rattrapage" value={stats.failed} tone="blue" />
        <StatCard label="Abandonnées" value={stats.abandoned} tone="amber" />
      </div>

      {/* À traiter — l'état RÉEL en base, pas l'historique */}
      {blocking.length > 0 && (
        <section className="bg-white rounded-xl border border-amber-300 overflow-hidden">
          <header className="px-5 py-3 bg-amber-50 border-b border-amber-200">
            <h2 className="text-sm font-bold text-amber-900">
              À traiter — {blocking.length} LR refuse{blocking.length > 1 ? 'nt' : ''} la connexion
            </h2>
            <p className="text-xs text-amber-700 mt-0.5">
              Le LR répond mais rejette notre authentification SSH. Aucune nouvelle tentative
              automatique : corriger les identifiants sur la fiche, ou intervenir sur l'équipement.
              Ceux marqués « coupé sur le routeur » ne sont pas urgents — le client est déjà coupé.
            </p>
          </header>
          <AttentionTable rows={blocking} showReason />
        </section>
      )}

      {pending.length > 0 && (
        <section className="bg-white rounded-xl border border-blue-200 overflow-hidden">
          <header className="px-5 py-3 bg-blue-50 border-b border-blue-100">
            <h2 className="text-sm font-bold text-blue-900">
              En attente — {pending.length} ordre{pending.length > 1 ? 's' : ''} à rejouer
            </h2>
            <p className="text-xs text-blue-500 mt-0.5">
              Le LR est injoignable (éteint, radio coupée). L'ordre sera appliqué automatiquement
              dès son retour — rien à faire.
            </p>
          </header>
          <AttentionTable rows={pending} />
        </section>
      )}

      {/* Filtres */}
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map(({ value, label }) => (
          <button
            key={value || 'all'}
            onClick={() => setStatus(value)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors ${
              status === value
                ? 'bg-blue-900 text-white border-blue-900'
                : 'bg-white text-blue-700 border-blue-200 hover:bg-blue-50'
            }`}
          >
            {label}
          </button>
        ))}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filtrer par MAC, nom du client ou agent…"
          className="ml-auto w-72 px-3 py-1.5 rounded-lg border border-blue-200 text-sm
                     focus:outline-none focus:ring-2 focus:ring-blue-300"
        />
      </div>

      {/* Historique */}
      <div className="bg-white rounded-xl border border-blue-100 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-blue-50 text-blue-900">
              <tr>
                <Th>Date</Th>
                <Th>Action</Th>
                <Th>Client</Th>
                <Th>MAC</Th>
                <Th>Origine</Th>
                <Th>Résultat</Th>
                <Th>Preuve</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              {entries.map((e, i) => (
                <tr key={`${e.timestamp}-${e.mac}-${i}`} className="hover:bg-blue-50/40">
                  <Td className="whitespace-nowrap text-blue-500 tabular-nums">
                    {formatTs(e.timestamp)}
                  </Td>
                  <Td>
                    <span className={`inline-block px-2 py-0.5 rounded-md border text-xs font-semibold
                                      ${ACTION_STYLE[e.action]?.cls ?? 'bg-slate-50 text-slate-600 border-slate-200'}`}>
                      {ACTION_STYLE[e.action]?.label ?? e.action}
                    </span>
                    {e.mode === 'whatsapp_only' && (
                      <span className="ml-1.5 text-[10px] text-blue-400">WhatsApp</span>
                    )}
                  </Td>
                  <Td className="font-medium text-blue-900">{e.name}</Td>
                  <Td className="font-mono text-xs text-blue-500">{e.mac ?? '—'}</Td>
                  {/* Une seule colonne pour deux faits distincts : le SCRIPT qui a
                      appelé (déduit du motif) et l'AGENT derrière (transmis par
                      l'appelant). L'agent en seconde ligne, comme le message sous
                      le résultat — pas de colonne à lui, mais pas fondu dans
                      l'origine non plus : un même script porte plusieurs agents.
                      Absent = l'appelant ne l'a pas transmis (ou action interne) :
                      la ligne disparaît simplement, sans « inconnu ». */}
                  <Td className="text-xs text-blue-500">
                    {SOURCE_LABEL[e.source] ?? e.source}
                    {e.user && (
                      <p className="text-[11px] text-blue-400 mt-0.5 break-all max-w-[14rem]">
                        {userLabel(e.user)}
                      </p>
                    )}
                  </Td>
                  <Td>
                    <span className={`font-semibold ${e.ok ? 'text-green-700' : 'text-red-700'}`}>
                      {e.ok ? 'Appliqué' : 'Non appliqué'}
                    </span>
                    <p className="text-xs text-blue-400 mt-0.5 max-w-xl">{e.message}</p>
                  </Td>
                  <Td className="whitespace-nowrap">
                    {e.has_evidence ? (
                      <button
                        onClick={() => setProofOf(e)}
                        className="px-2.5 py-1 rounded-md border border-blue-200 bg-white text-xs
                                   font-semibold text-blue-700 hover:bg-blue-50 transition-colors"
                      >
                        Voir la preuve
                      </button>
                    ) : (
                      <span className="text-xs text-blue-300">—</span>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {entries.length === 0 && (
          <p className="px-5 py-10 text-center text-sm text-blue-400">
            {isLoading
              ? 'Chargement…'
              : debounced || status
              ? 'Aucune action ne correspond à ce filtre.'
              : 'Aucune action enregistrée pour le moment.'}
          </p>
        )}
      </div>

      {proofOf && <EvidenceModal entry={proofOf} onClose={() => setProofOf(null)} />}
    </div>
  )
}

// ─── Lecture de la transcription ────────────────────────────────────────────
// La preuve archivée est faite de trois sortes de blocs : l'en-tête, des notes
// `[…]` (contexte, verdicts) et des commandes `$ … / sortie / -- exit N`.
type ProofBlock = { kind: 'header' | 'note' | 'cmd'; text: string }

/** Découpe la transcription en blocs.
 *
 *  Découpage LIGNE À LIGNE et non sur les lignes vides : la sortie d'une
 *  commande peut en contenir une, ce qui couperait le bloc en deux et ferait
 *  passer la moitié d'une sortie d'équipement pour un bloc autonome. Une
 *  commande court donc de sa ligne `$ …` jusqu'à son `-- exit`.
 */
function parseTranscript(raw: string): ProofBlock[] {
  const blocks: ProofBlock[] = []
  let cur: ProofBlock | null = null
  let inCmd = false  // dans une commande dont on n'a pas encore vu le `-- exit`
  const flush = () => {
    if (cur && cur.text.trim() !== '') blocks.push({ ...cur, text: cur.text.replace(/\s+$/, '') })
    cur = null
  }

  for (const line of raw.split('\n')) {
    if (line.startsWith('$ ')) {
      flush(); cur = { kind: 'cmd', text: line }; inCmd = true; continue
    }
    if (!inCmd && line.startsWith('[')) {
      flush(); cur = { kind: 'note', text: line }; continue
    }
    if (!cur) {
      if (line.trim() === '') continue
      cur = { kind: 'header', text: line }; continue
    }
    cur.text += '\n' + line
    if (inCmd && line.startsWith('-- exit')) inCmd = false
  }
  flush()
  return blocks
}

/** Ce qu'un opérateur a besoin de voir : la DERNIÈRE étape et son verdict.
 *
 *  C'est elle qui atteste l'état final de l'équipement — le reste de la session
 *  (identité, garde-fous) est la traçabilité, utile en cas de contestation mais
 *  pas à la lecture courante.
 *
 *  On ne coupe pas « les N dernières lignes » : on garde le dernier bloc de
 *  commande PLUS les notes qui le suivent. La règle tient sur tous les chemins —
 *  succès (`ip addr show` + RÉSULTAT), refus d'identité (lecture des MAC +
 *  REFUS), refus du garde-fou. Et quand AUCUNE commande n'a tourné (connexion
 *  refusée, interface protégée), le motif est dans la dernière note : c'est tout
 *  ce qu'il y a à dire, et la version courte le dit quand même.
 */
function summarizeTranscript(blocks: ProofBlock[]): ProofBlock[] {
  let lastCmd = -1
  blocks.forEach((b, i) => { if (b.kind === 'cmd') lastCmd = i })
  if (lastCmd >= 0) return blocks.slice(lastCmd)
  const notes = blocks.filter((b) => b.kind === 'note')
  return notes.length > 0 ? [notes[notes.length - 1]] : blocks
}

/** Ce que l'ÉQUIPEMENT a reçu et répondu — pas ce que nous avons rédigé.
 *
 *  La colonne « Résultat » du journal porte une phrase construite par le
 *  backend à partir de ce qu'il a *demandé* ; elle ne prouve rien. La
 *  transcription est la trace de la session SSH : chaque commande envoyée, la
 *  sortie brute du LR (stderr compris) et le code de sortie.
 *
 *  ⚠️ Ouvre sur la dernière étape de vérification, mais la session COMPLÈTE
 *  reste à un clic — et surtout, l'archive, elle, n'est jamais tronquée : ce qui
 *  est réduit ici est l'affichage, pas la preuve. Rendu en `<pre>` sans
 *  coloration ni reformatage : une preuve qu'on embellit n'est plus une preuve.
 */
function EvidenceModal({ entry, onClose }: { entry: FaiJournalEntry; onClose: () => void }) {
  const [full, setFull] = React.useState(false)
  const { data, error, isLoading } = useSWR<FaiEvidence>(
    endpoints.faiJournalEvidence(entry.timestamp, entry.action, entry.mac),
    fetcher,
  )

  const blocks = React.useMemo(
    () => (data ? parseTranscript(data.transcript) : []),
    [data],
  )
  const shown = full ? blocks : summarizeTranscript(blocks)
  const hidden = blocks.length - shown.length

  React.useEffect(() => {
    const onKey = (ev: KeyboardEvent) => { if (ev.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-blue-950/40 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl border border-blue-200 shadow-xl w-full max-w-3xl
                   max-h-[85vh] flex flex-col"
        onClick={(ev) => ev.stopPropagation()}
      >
        <header className="px-5 py-3 border-b border-blue-100 flex items-start gap-4">
          <div className="min-w-0">
            <h2 className="text-sm font-bold text-blue-900">
              Preuve d'exécution — {ACTION_STYLE[entry.action]?.label ?? entry.action}
            </h2>
            <p className="text-xs text-blue-400 mt-0.5">
              {entry.name} · <span className="font-mono">{entry.mac ?? '—'}</span> ·{' '}
              {formatTs(entry.timestamp)}
              {/* Sur ordre de qui : c'est la moitié manquante d'une preuve
                  d'exécution — elle atteste ce qui a été fait, pas qui l'a voulu. */}
              {entry.user && <> · demandé par {userLabel(entry.user)}</>}
            </p>
          </div>
          <button
            onClick={onClose}
            className="ml-auto shrink-0 px-2.5 py-1 rounded-md border border-blue-200 text-xs
                       font-semibold text-blue-700 hover:bg-blue-50"
          >
            Fermer
          </button>
        </header>

        <div className="px-5 py-3 overflow-auto">
          {isLoading && <p className="text-sm text-blue-400">Chargement de la preuve…</p>}
          {error && (
            <p className="text-sm text-amber-800">
              Preuve introuvable — elle n'a pas été archivée pour cette action.
            </p>
          )}
          {data && (
            <>
              {!full && (
                <p className="text-[11px] text-blue-400 mb-2">
                  Dernière étape de vérification — l'état de l'équipement après l'action.
                </p>
              )}
              <pre className="text-xs font-mono text-blue-900 whitespace-pre-wrap break-words
                              bg-blue-50/50 rounded-lg p-3 border border-blue-100">
                {shown.map((b) => b.text).join('\n\n')}
              </pre>
              {hidden > 0 && (
                <button
                  onClick={() => setFull(true)}
                  className="mt-2 text-xs font-semibold text-blue-700 hover:underline"
                >
                  Afficher la session complète ({hidden} étape{hidden > 1 ? 's' : ''} en amont)
                </button>
              )}
              {full && (
                <button
                  onClick={() => setFull(false)}
                  className="mt-2 text-xs font-semibold text-blue-700 hover:underline"
                >
                  Réduire à la vérification finale
                </button>
              )}
            </>
          )}
        </div>

        <footer className="px-5 py-2.5 border-t border-blue-100">
          <p className="text-[11px] text-blue-400">
            {full
              ? <>Chaque bloc <span className="font-mono">$ commande</span> montre ce qui a été
                  envoyé au LR, sa réponse brute et son code de sortie. Une commande d'écriture
                  Unix qui réussit n'imprime rien — c'est la relecture d'état qui l'atteste.</>
              : <>Affichage réduit. La session complète reste archivée intégralement —
                  contrôle d'identité MAC et garde-fous compris.</>}
          </p>
        </footer>
      </div>
    </div>
  )
}

function AttentionTable({ rows, showReason }: { rows: FaiAttentionRow[]; showReason?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-white text-blue-900">
          <tr>
            <Th>Client</Th>
            <Th>MAC</Th>
            <Th>IP</Th>
            <Th>Site</Th>
            <Th>Ordre</Th>
            <Th>Couverture</Th>
            {showReason && <Th>Cause</Th>}
          </tr>
        </thead>
        <tbody className="divide-y divide-blue-50">
          {rows.map((r) => (
            <tr key={r.id} className="hover:bg-blue-50/40">
              <Td className="font-medium text-blue-900">{r.name}</Td>
              <Td className="font-mono text-xs text-blue-500">{r.mac ?? '—'}</Td>
              <Td>{r.ip_address ? <IpLink ip={r.ip_address} /> : '—'}</Td>
              <Td className="text-blue-500">{r.site ?? '—'}</Td>
              <Td>
                <span className={`inline-block px-2 py-0.5 rounded-md border text-xs font-semibold ${
                  r.intent === 'block'
                    ? 'bg-red-50 text-red-700 border-red-200'
                    : 'bg-green-50 text-green-700 border-green-200'
                }`}>
                  {r.intent === 'block' ? 'à couper' : 'à rétablir'}
                </span>
              </Td>
              <Td>
                {r.router_blocked ? (
                  <span className="inline-block px-2 py-0.5 rounded-md border text-xs font-semibold
                                   bg-slate-100 text-slate-700 border-slate-300">
                    coupé sur le routeur
                  </span>
                ) : r.intent === 'block' ? (
                  <span className="text-xs font-semibold text-red-700">client en ligne ⚠</span>
                ) : (
                  <span className="text-xs text-blue-400">—</span>
                )}
              </Td>
              {showReason && (
                <Td className="text-xs text-amber-800 max-w-md">{r.reason ?? '—'}</Td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function StatCard({
  label, value, tone,
}: { label: string; value: number; tone: 'neutral' | 'green' | 'blue' | 'amber' }) {
  const cls = {
    neutral: 'text-blue-900',
    green:   'text-green-700',
    blue:    'text-blue-700',
    amber:   'text-amber-700',
  }[tone]
  return (
    <div className="bg-white rounded-xl border border-blue-100 px-4 py-3">
      <p className="text-xs text-blue-400">{label}</p>
      <p className={`text-2xl font-bold mt-0.5 tabular-nums ${cls}`}>{value}</p>
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
