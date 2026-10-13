import type { FaiRequestAction, FaiRequestEntry, FaiRequestEvent } from './types'

export const requestLabels = { completed: 'Terminée', incomplete: 'Non terminée', unverified: 'Historique non vérifiable' }

export const actionLabels: Record<string, string> = {
  client: "Coupure de l'accès sur l'équipement",
  router: "Blocage de l'accès sur le routeur",
  router_cleanup: 'Vérification finale du routeur',
  mode_cleanup: "Préparation de l'accès autorisé",
}

export function actionLabel(action: Pick<FaiRequestAction, 'key'>, mode: string): string {
  if (action.key === 'client' && mode === 'whatsapp_only') return "Restriction d'accès"
  if (action.key === 'mode_cleanup' && mode === 'full') return "Nettoyage complémentaire de l'accès"
  return actionLabels[action.key] ?? 'Action demandée'
}

// Durée totale entre le DÉBUT de l'attente et la DÉSACTIVATION RÉUSSIE —
// couvre aussi la période post-retour où la coupure n'a pas encore abouti.
// Renvoie null dès qu'une des conditions de la section 10 n'est pas réunie :
//   - aucune attente enregistrée ;
//   - pas encore de coupure confirmée (⇒ rien pendant l'attente) ;
//   - coupure directe sans attente (periods vide, cut_at présent).
// Le backend expose déjà `waiting.total_seconds` calculé sur la même plage
// (premier `started_at` → `ended_at = cut_at`), ce qui garantit qu'un
// rafraîchissement ne change pas la valeur affichée.
export function finalWaitSeconds(entry: FaiRequestEntry): number | null {
  if (!entry.cut_at) return null
  const periods = entry.waiting?.periods
  if (!periods || periods.length === 0) return null
  const total = entry.waiting?.total_seconds
  if (typeof total === 'number' && Number.isFinite(total) && total >= 0) return total
  const startedAt = entry.waiting?.started_at || periods[0]?.started_at
  if (!startedAt) return null
  const start = new Date(startedAt).getTime()
  const end = new Date(entry.cut_at).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null
  return Math.floor((end - start) / 1000)
}

export function duration(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds))
  const days = Math.floor(whole / 86400)
  const hours = Math.floor(whole % 86400 / 3600)
  const minutes = Math.floor(whole % 3600 / 60)
  return `${days ? `${days} j ` : ''}${hours ? `${hours} h ` : ''}${minutes} min ${whole % 60} s`
}

export function obstacle(message?: string | null): string {
  const value = (message ?? '').toLowerCase()
  if (/authentic|password|identifiant|host key/.test(value))
    return "Accès à l'équipement refusé. Une intervention est nécessaire."
  if (/identité|identity/.test(value))
    return "L'identité du client doit être vérifiée avant de poursuivre."
  if (/bridge|reconfiguration/.test(value))
    return 'La configuration doit être corrigée avant de poursuivre.'
  if (/adresse ip|sans ip/.test(value))
    return 'Les coordonnées de connexion sont manquantes. Une vérification est nécessaire.'
  if (/timed out|timeout|joignable|connexion|unreachable|no route/.test(value))
    return "L'équipement n'est pas accessible pour effectuer la coupure."
  return "Le résultat de la coupure n'a pas pu être confirmé. Une vérification reste nécessaire."
}

// Phrase courte et explicite — section 10 de la spec. Doit se lire sans aucune
// connaissance du réseau et sans jamais présenter une étape prévue comme déjà
// effectuée.
export function progress(entry: FaiRequestEntry): string {
  const whatsappOnly = entry.mode === 'whatsapp_only'
  const router = entry.actions.find(action => action.key === 'router')
  const routerConfirmed = router?.result === 'confirmed'
  switch (entry.stage) {
    case 'waiting':
      return routerConfirmed
        ? "L'accès de ce client est bloqué sur le routeur. La demande n'est pas encore terminée : nous attendons que l'équipement redevienne accessible pour désactiver son accès."
        : "La demande n'est pas encore terminée : nous attendons que l'équipement redevienne accessible pour effectuer la coupure."
    case 'to_cut':
      return "L'équipement est de nouveau accessible. Sa désactivation n'est pas encore confirmée."
    case 'attention':
      return "La désactivation n'a pas réussi. La demande reste non terminée."
    case 'finalizing':
      return "L'équipement a été désactivé. La vérification finale du routeur reste à terminer."
    case 'closed':
      return 'La demande a été arrêtée. Aucune nouvelle coupure ne sera lancée.'
    case 'legacy':
      return 'Ancien historique : les confirmations nécessaires ne sont pas disponibles.'
    case 'completed':
      return whatsappOnly
        ? "La restriction d'accès a été appliquée avec succès. La demande est terminée."
        : "L'équipement a été désactivé avec succès. La demande est terminée."
    default:
      return entry.status === 'completed'
        ? 'La désactivation a été confirmée. La demande est terminée.'
        : "La désactivation n'est pas encore confirmée. La demande n'est pas encore terminée."
  }
}

// Impératif court : ce que l'opérateur doit faire ensuite. Renvoie null quand
// rien n'est attendu, pour que l'UI puisse omettre entièrement la section.
export function nextAction(entry: FaiRequestEntry): string | null {
  if (entry.status === 'completed') return null
  if (!entry.active) return null
  const client = entry.actions.find(action => action.key === 'client')
  const reason = (client?.reason ?? '').toLowerCase()
  if (/authentic|password|identifiant|host key/.test(reason))
    return "Corriger les accès de gestion de l'équipement, puis reprendre la coupure et vérifier son résultat."
  if (/adresse ip|sans ip/.test(reason))
    return "Renseigner les coordonnées de connexion, vérifier l'identité du client, puis reprendre la coupure."
  if (/identité|identity/.test(reason))
    return "Vérifier l'identité du client avant toute nouvelle tentative de coupure."
  if (/bridge|reconfiguration/.test(reason))
    return "Corriger la configuration de l'équipement avant de reprendre la coupure et vérifier son résultat."
  if (entry.stage === 'waiting') {
    const router = entry.actions.find(action => action.key === 'router')
    const needsRouter = router && router.result !== 'confirmed'
    return needsRouter
      ? "Rétablir l'accès au routeur et confirmer le blocage de secours, puis attendre que l'équipement redevienne accessible pour effectuer la coupure."
      : "Attendre que l'équipement redevienne accessible, puis désactiver son accès et confirmer la coupure."
  }
  if (client?.result === 'unknown')
    return "Vérifier le résultat sur l'équipement. Si la coupure n'est pas appliquée, la reprendre et confirmer son succès."
  if (entry.stage === 'finalizing')
    return 'Terminer les vérifications restantes du routeur avant de confirmer la demande.'
  return "Reprendre la désactivation de l'accès sur l'équipement et vérifier que la coupure a réussi."
}

export function eventText(event: FaiRequestEvent, entry: FaiRequestEntry) {
  const key = event.action_key ?? entry.actions.find(action => action.target === event.target)?.key
  const label = actionLabel({ key: key ?? '' }, entry.mode)
  switch (event.kind) {
    case 'received':
      return 'Demande reçue.'
    case 'waiting_started':
      return "Début de l'attente du retour de l'équipement."
    case 'waiting_ended':
      return /n'est plus active|arrêtée/.test(event.message)
        ? 'Attente arrêtée : demande clôturée.'
        : "Attente terminée : coupure de l'équipement confirmée."
    case 'online':
      return /lors de la confirmation/.test(event.message)
        ? "Retour de l'équipement constaté lors de la confirmation de la coupure."
        : "Retour de l'équipement détecté."
    case 'offline':
      return "Équipement inaccessible lors du contrôle. Sa désactivation n'est pas confirmée."
    case 'completed':
      return 'Demande terminée : toutes les actions sont confirmées.'
    case 'closed':
      return 'Demande arrêtée : les nouvelles tentatives sont désactivées.'
    case 'legacy':
      return 'Ancien journal : résultat non confirmé.'
    case 'attempt':
      return `${label} : tentative effectuée.`
    default:
      return event.result === 'confirmed'
        ? `${label} effectuée et confirmée.`
        : `${label} non confirmée. ${obstacle(event.message)}`
  }
}
