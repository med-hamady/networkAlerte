// Barème de rendu PARTAGÉ par les deux vues du blocage client : le journal
// (/fai-journal, « ce qui s'est passé ») et les demandes (/fai-requests, « ce
// qui a été demandé, et où en est ce client »).
//
// ⚠️ Partagé et non recopié, pour la même raison que `lib/topologyColors` entre
// le graphe et la carte : les deux écrans décrivent LES MÊMES lignes de journal.
// Deux copies des libellés finiraient par se contredire au premier ajustement,
// et un opérateur qui lit « Abandonné » ici et « Échec » là ne peut plus savoir
// s'il regarde deux faits ou un seul.
import type { FaiEntryCurrent, FaiJournalEntry } from '@/lib/types'

// Une action = ce qu'on a essayé de faire. La couleur porte le sens métier :
// rouge = coupure, vert = rétablissement, ambre = échec définitif.
export const ACTION_STYLE: Record<FaiJournalEntry['action'], { label: string; cls: string }> = {
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

export const FALLBACK_ACTION_STYLE = 'bg-slate-50 text-slate-600 border-slate-200'

// Quel SYSTÈME a appelé. `script` = blocage de masse (migration depuis le
// MikroTik). Une origine absente de cette table est affichée telle quelle —
// c'est le cas des noms de scripts déduits du motif côté backend.
export const SOURCE_LABEL: Record<string, string> = {
  payment: 'Système de paiement',
  enforce: 'Renforcement auto',
  script:  'Blocage de masse',
}

export function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? source
}

// QUI est derrière l'action — distinct de l'origine : les gestes manuels d'un
// opérateur et les campagnes automatiques passent par le même système de
// paiement. Un e-mail est affiché tel quel ; seuls les deux libellés
// automatiques connus sont traduits, pour qu'on ne les lise pas comme des noms.
const USER_LABEL: Record<string, string> = {
  'auto system': 'Campagne impayés (auto)',
  'auto retry':  'Rejeu automatique (auto)',
}

export function userLabel(user: string | null): string | null {
  if (!user) return null
  return USER_LABEL[user.trim().toLowerCase()] ?? user
}

export function formatTs(ts: string): string {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  return d.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

// ─── Où en est le client MAINTENANT ─────────────────────────────────────────
// Quatre états, à ne jamais confondre avec le résultat de la demande (qui, lui,
// est figé à l'instant de l'action).
export type NowState = 'cut' | 'not_cut' | 'restored' | 'gone'

export function nowState(current: FaiEntryCurrent): NowState {
  // ⚠️ La fiche a disparu (station déprovisionnée dans UISP, que le sync
  // supprime) : on ne sait RIEN de ce client. Surtout pas « pas coupé ».
  if (!current.known) return 'gone'
  if (!current.client_blocked) return 'restored'
  // Ordre de coupure enregistré mais appliqué NULLE PART = le client navigue.
  return current.enforced_by ? 'cut' : 'not_cut'
}

export const NOW_STYLE: Record<NowState, { label: string; cls: string }> = {
  // Un fait, pas un jugement : ardoise. C'est l'état attendu d'un impayé.
  cut:      { label: 'Coupé',            cls: 'bg-slate-800 text-white border-slate-800'   },
  // LE cas qui coûte de l'argent — il doit sauter aux yeux.
  not_cut:  { label: 'NON COUPÉ ⚠',      cls: 'bg-red-600 text-white border-red-700'       },
  restored: { label: 'Rétabli',          cls: 'bg-green-50 text-green-700 border-green-200' },
  gone:     { label: 'Fiche supprimée',  cls: 'bg-slate-50 text-slate-500 border-slate-200' },
}

export const ENFORCED_BY_LABEL: Record<'lr' | 'router', string> = {
  lr:     'Sur son LR',
  router: 'Routeur (repli)',
}

// ─── Message du journal → phrase lisible ────────────────────────────────────
// Les messages sont rédigés pour le diagnostic et portent la sortie brute de
// paramiko (« (Port LAN: [Errno 111] Connection refused | Filtre WhatsApp:
// [Errno 111] Connection refused) »). Illisible pour un opérateur, et ça
// occupe trois lignes de tableau.
//
// ⚠️ On REFORMULE À L'AFFICHAGE, on ne réécrit jamais le journal : le fichier
// est une piste d'audit, et 6 451 lignes déjà écrites ne peuvent de toute
// façon pas être corrigées après coup. Le brut part donc en infobulle.
//
// ⚠️ Les causes techniques ne sont PAS fondues en une seule phrase. « Injoignable »
// (No route to host) et « refuse la connexion » (Connection refused) appellent
// des gestes différents — équipement éteint ou hors zone dans un cas, équipement
// vivant qui rejette dans l'autre. Les confondre enverrait un technicien pour rien.

export interface ReadableMessage {
  /** Phrase courte, lisible par un opérateur. */
  text: string
  /** Le message d'origine — jamais jeté, rendu en infobulle. */
  raw: string
}

// Ordre significatif : les causes STRUCTURELLES (on n'arrive pas à s'authentifier)
// avant les causes de JOIGNABILITÉ, car un message peut porter les deux et c'est
// la première qui décide de la suite — le job cesse de réessayer.
const CAUSES: [RegExp, string][] = [
  [/Authentication failed/i,          'mot de passe SSH rejeté'],
  [/host key/i,                       "clé d'hôte changée"],
  [/\[Errno 113\]|No route to host/i, 'le LR est injoignable'],
  [/\[Errno 111\]|Connection refused/i, 'le LR refuse la connexion'],
  [/timed out/i,                      "le LR n'a pas répondu à temps"],
]

function cause(raw: string): string | null {
  for (const [re, label] of CAUSES) if (re.test(raw)) return label
  return null
}

/** Suffixe « — <cause> » quand on sait la nommer, rien sinon. */
function withCause(head: string, raw: string): string {
  const c = cause(raw)
  return c ? `${head} — ${c}` : head
}

// Formes relevées sur le parc réel (6 451 lignes au 2026-09-16). Ordre
// significatif : du plus spécifique au plus général.
const RULES: [RegExp, (raw: string) => string][] = [
  [/^Déblocage enregistré/i,      (r) => withCause('Rétablissement en attente', r)],
  // ⚠️ Le MODE s'intercale entre les deux mots : « Blocage (coupure totale)
  // enregistré pour … », « Blocage (WhatsApp seul) enregistré pour … ». Une
  // règle ancrée sur « ^Blocage enregistré » manquait donc les ~63 lignes de
  // cette forme, qui est pourtant le pendant exact du déblocage ci-dessus.
  [/^Blocage\b.*enregistré/i,     (r) => withCause('Coupure en attente', r)],
  // Posée par scripts/mark_router_blocked.py : une coupure que le routeur
  // portait DÉJÀ avant que le superviseur ne la connaisse (reprise de
  // l'existant), pas une action que nous venons de déclencher.
  [/^Coupure routeur préexistante/i, () => 'Coupure routeur préexistante, enregistrée'],
  [/bloqué sur le routeur/i,      (r) => withCause('Coupé par le routeur', r)],
  [/Interface .* mise DOWN/i,     () => 'Coupé sur son LR — port LAN fermé, vérifié'],
  [/^Accès internet rétabli/i,    () => 'Accès rétabli'],
  [/^Rollback/i,                  () => "Annulé — la coupure n'avait jamais pris"],
  [/en attente appliqué sur le LR/i, () => 'Rattrapé — appliqué sur le LR'],
  [/impossible \(connexion refusée\)/i, (r) => withCause('Abandonné', r)],
  [/REFUSÉ/,                      () => "Refusé — l'équipement joint n'est pas celui de la fiche"],
  [/règle\(s\) de blocage retirée\(s\)/i, () => 'Règle routeur retirée'],
  [/Règle de blocage déjà présente/i,     () => 'Règle routeur déjà en place'],
  [/Règle de blocage posée/i,             () => 'Règle routeur posée'],
  [/Aucune règle de blocage/i,            () => 'Aucune règle sur le routeur'],
]

/** Message du journal → phrase courte + brut conservé.
 *
 *  ⚠️ Une forme NON RECONNUE est rendue telle quelle, jamais remplacée par un
 *  libellé générique : un message qu'on ne sait pas traduire doit rester
 *  lisible, sinon un cas nouveau disparaît de l'écran sans que personne le voie.
 */
export function readableMessage(raw: string): ReadableMessage {
  const message = (raw || '').trim()
  for (const [re, render] of RULES) {
    if (re.test(message)) return { text: render(message), raw: message }
  }
  return { text: message, raw: message }
}
