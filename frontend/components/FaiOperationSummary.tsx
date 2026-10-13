'use client'

/**
 * Résumé visuel d'une demande de coupure — UNE SEULE carte à 3 zones :
 *
 *   ┌──────────┐   ┌─────────────┐   ┌─────────────────────────────┐
 *   │ 🕐 Date  │ → │ Source      │ → │  📱 Équipement → Résultat   │
 *   │  heure   │   │  ─ ─ ─      │   │       ↓                     │
 *   │ Type :   │   │  👤 Client  │   │    🔧 Routeur (si existant)│
 *   │ Blocage  │   └─────────────┘   └─────────────────────────────┘
 *   └──────────┘
 *
 * Règles tenues :
 *  - « Hors ligne » n'est AFFICHÉ que si le journal porte une preuve explicite
 *    (No route to host, injoignable, etc.). Un simple refus de connexion ou une
 *    valeur dans `unenforceable_reason` ne suffit pas — on affiche alors le
 *    motif réel sans dire « Hors ligne ».
 *  - « Terminé » n'apparaît QUE si `isCutCompleted(current)` est vrai, et la
 *    pastille suit le même état actuel que les filtres et les compteurs.
 *    Les confirmations datées restent liées à leur demande historique.
 *  - Le TYPE (Blocage / Déblocage) vient de l'ACTION réelle de la demande liée
 *    quand elle est disponible — jamais de `mode` qui décrit le TYPE D'ACCÈS
 *    (coupure totale / WhatsApp uniquement).
 *  - La SOURCE affichée est l'origine initiale ; « Renforcement auto » n'est
 *    gardé qu'à défaut de connaître la vraie source via la demande liée.
 *  - RETRY_OK n'est jamais classé automatiquement en coupure directe : si la
 *    demande liée porte un blocage routeur antérieur, l'étape routeur reste
 *    visible dans la 3e zone.
 */

import React from 'react'
import type { FaiJournalEntry, FaiRequestAction, FaiRequestEntry } from '@/lib/types'
import { readableMessage, sourceLabel } from '@/lib/faiActions'
import { isCutCompleted, currentCutResult } from '@/lib/faiRouterWait'
import { requestMatchesEvent } from '@/lib/faiRequestLink'

// ─── Formatage date (JJ/MM/AAAA HH:mm) ───────────────────────────────────────
function formatDateParts(value: string): { date: string; time: string } | null {
  const ts = Date.parse(value)
  if (!Number.isFinite(ts)) return null
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return {
    date: `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`,
    time: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
  }
}

// ─── Icônes SVG inline, taille régulière ─────────────────────────────────────
const Svg = ({ children, className = 'h-5 w-5' }: { children: React.ReactNode; className?: string }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor"
    strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
)
const IconClock = () => <Svg><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></Svg>
const IconUser = () => <Svg className="h-7 w-7"><circle cx="12" cy="8" r="3.5" />
  <path d="M5 20c0-3.5 3-6 7-6s7 2.5 7 6" /></Svg>
const IconDevice = () => <Svg className="h-7 w-7"><rect x="4" y="5" width="16" height="12" rx="2" />
  <path d="M8 20h8M12 17v3" /></Svg>
const IconRouter = () => <Svg className="h-7 w-7"><rect x="3" y="12" width="18" height="7" rx="1.5" />
  <path d="M7 16h.01M11 16h.01M12 8V5m0 3-3-2m3 2 3-2m-9 5a5 5 0 0 1 12 0" /></Svg>

// ─── État de l'équipement, détecté FINEMENT depuis le message ────────────────
// « Hors ligne » est réservé aux cas où le message l'établit EXPLICITEMENT.
// « Injoignable » (No route to host / Errno 113) prouve que l'équipement n'a
// pas répondu au réseau — c'est une absence de preuve, pas une preuve
// d'arrêt — donc on affiche « Équipement injoignable », pas « Hors ligne ».
// Un refus de connexion (Errno 111) prouve l'inverse : l'équipement est
// joignable au réseau et a REFUSÉ la session — on nomme ce motif tel quel.
type DeviceEvidence =
  | 'offline_confirmed'   // le message dit explicitement « hors ligne » / « offline »
  | 'unreachable'         // injoignable au niveau réseau — on n'arrive pas à le joindre
  | 'ssh_refused'         // joignable mais a refusé la connexion de gestion
  | 'auth_failed'         // mot de passe de gestion rejeté
  | 'host_key_mismatch'   // clé d'hôte changée
  | 'timeout'             // n'a pas répondu à temps
  | 'none'                // aucun indice exploitable

function detectDeviceEvidence(entry: FaiJournalEntry): DeviceEvidence {
  const raw = entry.message || ''
  if (/\bhors[- ]ligne\b|\boffline\b|éteint|power(ed)? off/i.test(raw)) return 'offline_confirmed'
  if (/No route to host|\[Errno 113\]|\binjoignable\b|indisponible/i.test(raw)) return 'unreachable'
  if (/Authentication failed|mot de passe .* rejeté/i.test(raw)) return 'auth_failed'
  if (/host key/i.test(raw)) return 'host_key_mismatch'
  if (/\[Errno 111\]|Connection refused|refuse la connexion/i.test(raw)) return 'ssh_refused'
  if (/timed out|time.?out|n'a pas répondu à temps/i.test(raw)) return 'timeout'
  return 'none'
}

function obstacleLabel(entry: FaiJournalEntry): string | null {
  if (!entry.message) return null
  const text = readableMessage(entry.message).text
  return text || null
}

// ─── Rattachement événement ↔ demande ────────────────────────────────────────
// On a UN événement du journal (figé) et parfois une FaiRequestEntry liée.
// Le journal ne porte PAS d'id de demande, le lien se fait donc par MAC +
// timestamp EXACT d'une action/received_at/cut_at de la demande. Un même MAC
// peut porter plusieurs demandes successives ; une collision parfaite au
// millième de seconde près reste possible mais rare. Si cette liaison existe
// déjà via le hook parent (`linkedRequest`), on la fait confiance.
function matchRequestToEvent(
  entry: FaiJournalEntry, request: FaiRequestEntry | null | undefined,
): FaiRequestEntry | null {
  return requestMatchesEvent(entry, request ?? null) ? request ?? null : null
}

// ─── Type réel de la demande (Blocage / Déblocage) ───────────────────────────
// Priorité :
//   1. Si la demande liée porte une `operation`, elle fait foi (block / unblock).
//   2. Sinon on dérive de l'ACTION du journal — fiable pour BLOCK/UNBLOCK et
//      leurs variantes routeur, inconnu pour RETRY_OK/ABANDON/IDENT_KO sans
//      demande liée (ambiguous : RETRY_OK peut suivre un BLOCK comme un UNBLOCK).
//   3. En dernier recours on tente d'extraire du message, sinon `null`.
// NB : on n'utilise JAMAIS `mode` ici — « full » / « whatsapp_only » décrit le
// TYPE D'ACCÈS demandé, pas si c'est un blocage ou un déblocage.
function requestType(entry: FaiJournalEntry, linked: FaiRequestEntry | null): 'Blocage' | 'Déblocage' | null {
  // A router cleanup belongs to the original cut request but remains a
  // withdrawal event. Never relabel it from another action in that request.
  if (entry.action === 'UNBLOCK' || entry.action === 'ROUTER_UNBLOCK') return 'Déblocage'
  if (linked) {
    const op = linked.actions.find((a: FaiRequestAction) => a.operation === 'block' || a.operation === 'unblock')?.operation
    if (op === 'block') return 'Blocage'
    if (op === 'unblock') return 'Déblocage'
  }
  switch (entry.action) {
    case 'BLOCK':
    case 'ROUTER_BLOCK':
      return 'Blocage'
    default: {
      const raw = entry.message || ''
      if (/^D[ée]blocage/i.test(raw)) return 'Déblocage'
      if (/^Blocage/i.test(raw)) return 'Blocage'
      return null
    }
  }
}

// ─── Source réelle de la demande ─────────────────────────────────────────────
// « Renforcement auto » (`enforce`) n'est, dans ce projet, qu'un mécanisme de
// relance. S'il figure sur l'événement mais que la demande liée nomme une autre
// origine, on affiche cette origine-ci. On NE fabrique PAS de source quand
// aucune donnée ne la nomme — on affiche alors « Source non renseignée ».
function realSource(entry: FaiJournalEntry, linked: FaiRequestEntry | null): string {
  const linkedSource = linked && linked.source ? linked.source : null
  if (entry.source === 'enforce' && linkedSource && linkedSource !== 'enforce') {
    return sourceLabel(linkedSource)
  }
  if (!entry.source && linkedSource) return sourceLabel(linkedSource)
  if (!entry.source) return 'Source non renseignée'
  return sourceLabel(entry.source)
}

// ─── Nom du client concerné (pas l'agent) ────────────────────────────────────
function clientName(entry: FaiJournalEntry, linked: FaiRequestEntry | null): string {
  return linked?.client_name || entry.name || linked?.name || 'Client non renseigné'
}

// ─── Identité de l'équipement ────────────────────────────────────────────────
function deviceIdentity(entry: FaiJournalEntry): string {
  if (entry.mac) return entry.mac
  if (entry.current.site) return entry.current.site
  return 'Équipement du client'
}

// ─── Palette ─────────────────────────────────────────────────────────────────
// Vert menthe contrasté sur fond blanc : emerald-500/600 passe AA.
const MINT_DOT = 'bg-emerald-500 ring-2 ring-white'
const MINT_TEXT = 'text-emerald-700'
const MINT_BG = 'bg-emerald-50 border-emerald-300 text-emerald-800'
const RED_DOT = 'bg-red-500 ring-2 ring-white'
const RED_BG = 'bg-red-50 border-red-300 text-red-800'
const AMBER_BG = 'bg-amber-50 border-amber-300 text-amber-900'
const SLATE_BG = 'bg-slate-100 border-slate-300 text-slate-700'
const NEUTRAL_BG = 'bg-white border-slate-200 text-slate-800'

// ─── Icône équipement avec pastille d'avancement d'opération ─────────────────
// La pastille représente l'AVANCEMENT de l'opération (pas l'état technique de
// l'équipement) :
//   - rouge  = opération « Non terminée » (en attente de finalisation)
//   - menthe = opération « Terminée » (achèvement confirmé pour cette demande)
// Le libellé technique (« Hors ligne », « Injoignable »…) reste à côté de
// l'icône, en texte, pour éviter toute confusion.
function DeviceIcon({ progress }: { progress: 'done' | 'pending' }) {
  const dotCls = progress === 'done' ? MINT_DOT : RED_DOT
  const dotLabel = progress === 'done'
    ? 'Opération terminée pour cette demande'
    : 'Opération en attente de finalisation'
  return (
    <span className="relative inline-flex items-center justify-center h-11 w-11 rounded-xl bg-blue-50 text-blue-800 border border-blue-100">
      <IconDevice />
      <span className={`absolute -top-1 -right-1 h-3.5 w-3.5 rounded-full ${dotCls}`}
        role="img" aria-label={dotLabel} />
    </span>
  )
}

// ─── Zone 1 — Date + Type ────────────────────────────────────────────────────
function Zone1({ entry, linked }: { entry: FaiJournalEntry; linked: FaiRequestEntry | null }) {
  const parts = formatDateParts(entry.timestamp)
  const type = requestType(entry, linked)
  return (
    <div className="flex flex-col items-stretch gap-2 min-w-[150px]">
      <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 flex items-center gap-2">
        <span className="inline-flex items-center justify-center h-9 w-9 rounded-lg bg-blue-50 text-blue-800 border border-blue-100 shrink-0">
          <IconClock />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-800 leading-tight tabular-nums">
            {parts?.date ?? 'Date inconnue'}
          </p>
          <p className="text-xs text-slate-500 leading-tight tabular-nums">
            {parts ? parts.time : '—'}
          </p>
        </div>
      </div>
      <p className="text-xs text-slate-700 px-1">
        <span className="font-semibold">Type : </span>
        <span className={type ? 'text-blue-900 font-semibold' : 'text-slate-500 italic'}>
          {type ?? 'Type non renseigné'}
        </span>
      </p>
    </div>
  )
}

// ─── Zone 2 — Source + Client (vertical) ─────────────────────────────────────
function Zone2({ entry, linked }: { entry: FaiJournalEntry; linked: FaiRequestEntry | null }) {
  const source = realSource(entry, linked)
  const name = clientName(entry, linked)
  const sourceKnown = source !== 'Source non renseignée'
  return (
    <div className="flex flex-col items-center gap-2 min-w-[160px]">
      <div className={`rounded-xl border px-3 py-2 w-full text-center ${
        sourceKnown ? 'bg-white border-slate-200 text-slate-800' : 'bg-white border-slate-200 text-slate-500 italic'
      }`}>
        <p className="text-[10px] uppercase tracking-wide text-slate-500 font-semibold">Source</p>
        <p className="text-sm font-semibold break-words leading-tight mt-0.5">{source}</p>
      </div>
      <span className="inline-flex items-center justify-center h-11 w-11 rounded-full bg-blue-50 text-blue-800 border border-blue-100">
        <IconUser />
      </span>
      <p className="text-sm font-semibold text-slate-800 text-center break-words leading-tight max-w-[180px]">
        {name}
      </p>
    </div>
  )
}

// ─── Zone 3 — Équipement + parcours + résultat ───────────────────────────────
function Zone3({ entry, linked }: { entry: FaiJournalEntry; linked: FaiRequestEntry | null }) {
  const evidence = detectDeviceEvidence(entry)
  const identity = deviceIdentity(entry)
  const currentCompleted = isCutCompleted(entry.current)
  const type = requestType(entry, linked)
  const whatsappOnly = (linked?.mode ?? entry.mode) === 'whatsapp_only'

  const routerConfirmed = linked?.actions.find(
    a => a.key === 'router' && a.result === 'confirmed' && a.confirmed_at)
  const clientConfirmed = linked?.actions.find(
    a => a.key === 'client' && a.result === 'confirmed' && a.confirmed_at)

  // Cette demande précise est-elle terminée ? La confirmation client doit
  // appartenir à la demande liée (pas juste à l'état actuel du MAC).
  const sameRequestCompleted = Boolean(
    linked && clientConfirmed && linked.status === 'completed',
  )

  // L'événement affiché EST-IL la confirmation finale ?
  const entryIsFinalCut = Boolean(
    entry.ok && linked && linked.cut_at === entry.timestamp &&
    linked.actions.some(a => a.key === 'client' && a.result === 'confirmed' && a.confirmed_at === entry.timestamp),
  )

  // Résultat final — deux états seulement : Terminée (coche vert menthe) ou
  // Non terminée. Calculé AVANT le libellé équipement : la pastille et le
  // texte doivent rester cohérents avec le statut.
  // The badge uses the same current state as the filters and counters.
  // Recorded confirmations below remain historical evidence for their request.
  const result = currentCutResult(entry.current)
  const resultBadgeCls = result.done ? MINT_BG : AMBER_BG
  const resultLabel = result.done ? '✔ Terminée' : 'Non terminée'

  // Short current-state label; historical errors remain in the event details.
  const equipmentLabel = (() => {
    if (result.done) return 'Coupé sur l’équipement'
    if (!entry.current.known) return 'État actuel inconnu'
    if (!entry.current.client_blocked) return 'Non coupé'
    if (entry.current.enforced_by === 'router') return 'Blocage routeur uniquement'
    return 'Coupure non confirmée'
  })()

  // Teinte du libellé : menthe quand l'opération est terminée, rouge quand
  // l'équipement est explicitement hors ligne, ambre pour tout autre obstacle
  // connu, ardoise par défaut.
  const labelCls = result.done ? MINT_TEXT
    : evidence === 'offline_confirmed' ? 'text-red-700'
    : evidence !== 'none' ? 'text-amber-800'
    : 'text-slate-700'

  // Étape finale (box sous l'icône équipement) — dépend du scénario :
  //
  // 1. Terminée via routeur + équipement :
  //    « Blocage/Déblocage sur l'équipement » avec l'horodatage RÉEL de la
  //    confirmation sur l'équipement (clientConfirmed.confirmed_at). L'ancien
  //    blocage routeur sort du résumé et reste visible dans « Voir plus ».
  //
  // 2. En attente (événement ROUTER_BLOCK ou blocage routeur enregistré mais
  //    pas encore de confirmation équipement) :
  //    « Blocage/Retrait sur le routeur » avec son horodatage.
  //
  // 3. Coupure directe (terminée sans aucun passage routeur) :
  //    pas de box — l'équipement et son libellé suffisent.
  //
  // On N'UTILISE PAS le nom de l'ancien événement routeur pour y accoler le
  // résultat équipement : la box de confirmation porte sa VRAIE action (sur
  // l'équipement) et son VRAI horodatage (celui de clientConfirmed).
  const padNum = (n: number) => String(n).padStart(2, '0')
  const shortTs = (iso: string) => {
    const d = new Date(iso)
    return `${padNum(d.getDate())}/${padNum(d.getMonth() + 1)} ${padNum(d.getHours())}:${padNum(d.getMinutes())}`
  }
  const finalStep = (() => {
    // Terminée AVEC une confirmation équipement connue : afficher l'action
    // sur l'équipement avec son horodatage RÉEL (celui de clientConfirmed).
    // Vaut pour les scénarios 2 (via routeur) ET 3 (coupure directe) — dans
    // les deux cas l'action terminante est celle qui a abouti sur
    // l'équipement. L'étape routeur, si elle existe, reste dans « Voir plus ».
    if (result.done && sameRequestCompleted && clientConfirmed && type !== 'Déblocage') {
      const title = whatsappOnly ? 'Restriction WhatsApp sur l’équipement' : 'Blocage sur l’équipement'
      return {
        title, note: `Confirmé le ${shortTs(clientConfirmed.confirmed_at!)}`,
        tone: MINT_BG, badge: 'OK' as const, icon: 'device' as const,
      }
    }
    // Terminée mais sans détail de confirmation équipement en main (par
    // exemple entrée = RETRY_OK sans FaiRequestEntry liée) : pas de box
    // supplémentaire, l'icône et son libellé portent déjà l'information.
    if (result.done) return null
    if (!entry.current.known || !entry.current.client_blocked) return null

    // En attente : afficher l'étape routeur réelle.
    if (entry.action === 'ROUTER_BLOCK') {
      return entry.ok
        ? { title: 'Blocage sur le routeur', note: 'Confirmé par le journal',
            tone: SLATE_BG, badge: 'OK' as const, icon: 'router' as const }
        : { title: 'Blocage sur le routeur', note: 'Tenté — non confirmé',
            tone: AMBER_BG, badge: null, icon: 'router' as const }
    }
    if (entry.action === 'ROUTER_UNBLOCK') {
      return {
        title: 'Retrait du blocage sur le routeur',
        note: entry.ok ? 'Confirmé par le journal' : 'Tenté — non confirmé',
        tone: entry.ok ? SLATE_BG : AMBER_BG,
        badge: null as 'OK' | null, icon: 'router' as const,
      }
    }
    if (routerConfirmed) {
      return {
        title: 'Blocage sur le routeur',
        note: `Historique · ${shortTs(routerConfirmed.confirmed_at!)}`,
        tone: SLATE_BG, badge: null as 'OK' | null, icon: 'router' as const,
      }
    }
    return null
  })()

  const currentNote = currentCompleted && !sameRequestCompleted && !entryIsFinalCut
    ? 'La coupure actuelle ne confirme pas le résultat de cet ancien événement.' : null

  return (
    <div className="flex-1 min-w-0 rounded-xl border border-slate-200 bg-white p-3 sm:p-4">
      {/* Ligne principale : équipement en haut à gauche, badge résultat compact
          en haut à droite. Routeur sous l'équipement si l'opération l'a touché. */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-start gap-3 min-w-0 flex-1">
          <DeviceIcon progress={result.done ? 'done' : 'pending'} />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-800 leading-tight">Équipement</p>
            <p className="text-[11px] font-mono text-slate-500 break-all leading-tight">{identity}</p>
            <p className={`text-xs mt-0.5 font-semibold ${labelCls}`}>{equipmentLabel}</p>
          </div>
        </div>
        {/* Flèche compacte vers le badge résultat (desktop seulement) */}
        <span aria-hidden="true" className="hidden sm:inline-flex self-center text-blue-300 shrink-0">
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </span>
        {/* Badge résultat compact : fit-content, pas un pavé qui domine */}
        <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-bold shrink-0 ${resultBadgeCls}`}>
          {result.done && <span className={`inline-flex items-center justify-center h-4 w-4 rounded-full ${MINT_DOT}`} aria-hidden="true" />}
          {resultLabel}
        </span>
      </div>

      {/* Étape finale sous l'équipement — box qui porte VRAIMENT l'action
          qui compte à cet instant : le blocage/déblocage CONFIRMÉ sur
          l'équipement si la demande est terminée, sinon le blocage routeur
          en attente. En coupure directe, pas de box : l'équipement suffit. */}
      {finalStep && (
        <div className="mt-2 pl-14">
          <span aria-hidden="true" className="inline-block text-blue-300 ml-1">
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none"
              stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 5v14M6 13l6 6 6-6" />
            </svg>
          </span>
          <div className={`mt-1 inline-flex items-center gap-2 rounded-lg border px-2.5 py-1 ${finalStep.tone}`}>
            <span className={`inline-flex items-center justify-center h-7 w-7 rounded-lg shrink-0 ${
              finalStep.icon === 'device' ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-700'
            }`}>
              {finalStep.icon === 'device' ? <IconDevice /> : <IconRouter />}
            </span>
            <div className="min-w-0">
              <p className="text-xs font-semibold leading-tight">{finalStep.title}</p>
              <p className="text-[11px] opacity-80 leading-tight">{finalStep.note}</p>
            </div>
            {finalStep.badge && (
              <span className={`ml-1 text-[10px] font-bold rounded px-1.5 py-0.5 text-white ${
                finalStep.icon === 'device' ? 'bg-emerald-600' : 'bg-slate-700'
              }`}>
                {finalStep.badge}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Phrase courte expliquant la raison de l'attente, si pertinent */}
      {result.note && (
        <p className={`mt-2 text-[11px] leading-snug ${result.done ? MINT_TEXT : 'text-amber-800'}`}>
          {result.note}
        </p>
      )}

      {/* Encart « État actuel » SÉPARÉ — reste dans la 3e zone */}
      {currentNote && (
        <p className="mt-2 rounded-lg border border-blue-200 bg-blue-50 p-2 text-[11px] text-blue-900">
          <strong>État actuel du client : </strong>{currentNote}
        </p>
      )}
    </div>
  )
}

// ─── Calcul du résultat final — deux états seulement ────────────────────────

// ─── Flèche principale entre zones (horizontale desktop / verticale mobile) ─
function ZoneArrow() {
  return (
    <span aria-hidden="true" className="shrink-0 self-center text-blue-400">
      <svg viewBox="0 0 24 24" className="h-6 w-6 rotate-90 lg:rotate-0" fill="none"
        stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 12h14M13 6l6 6-6 6" />
      </svg>
    </span>
  )
}

// ─── Composant exporté ───────────────────────────────────────────────────────
export default function FaiOperationSummary({
  entry, request,
}: { entry: FaiJournalEntry; request?: FaiRequestEntry | null }) {
  const linked = matchRequestToEvent(entry, request ?? null)
  return (
    <section
      aria-label="Résumé de la demande"
      className="flex flex-col lg:flex-row lg:items-stretch gap-3 lg:gap-2"
    >
      <Zone1 entry={entry} linked={linked} />
      <ZoneArrow />
      <Zone2 entry={entry} linked={linked} />
      <ZoneArrow />
      <Zone3 entry={entry} linked={linked} />
    </section>
  )
}
