import type { FaiJournalEntry } from './types'
import { isCutCompleted } from './faiRouterWait'

export const eventTitles: Record<FaiJournalEntry['action'], string> = {
  BLOCK: 'Tentative de coupure',
  UNBLOCK: 'Tentative de remise en service',
  RETRY_OK: 'Nouvelle tentative de coupure',
  ABANDON: 'Tentative interrompue',
  IDENT_KO: 'Identité du client non confirmée',
  ROUTER_BLOCK: 'Blocage sur le routeur',
  ROUTER_UNBLOCK: 'Retrait du blocage sur le routeur',
}

export function eventExplanation(e: FaiJournalEntry): string {
  if (e.action === 'ABANDON') return 'La tentative a été arrêtée à cette date. Cet événement ne confirme pas la coupure de l’équipement.'
  if (e.action === 'IDENT_KO') return 'L’identité de l’équipement n’a pas été validée. Cette tentative ne confirme aucune coupure.'
  if (!e.ok) return 'Cette tentative n’a pas abouti selon le journal. Son échec reste conservé dans l’historique.'
  switch (e.action) {
    case 'ROUTER_BLOCK': return 'Le journal indique un blocage de l’accès sur le routeur. Cela ne confirme pas encore la coupure sur l’équipement du client.'
    case 'ROUTER_UNBLOCK': return 'Le journal indique le retrait du blocage sur le routeur. Cela ne prouve pas, à lui seul, le retour effectif du service.'
    case 'UNBLOCK': return 'Le journal indique une remise en service réussie à cette date. Le client peut avoir changé de situation depuis.'
    case 'RETRY_OK': return 'Le journal indique qu’une nouvelle tentative a réussi à cette date. Ce résultat appartient à cet événement.'
    default: return 'Le journal indique une tentative réussie. Cette mention seule ne précise pas si la coupure a été appliquée sur le routeur ou sur l’équipement.'
  }
}

export function currentExplanation(e: FaiJournalEntry) {
  if (!e.current.known) return { text: 'Les données actuelles ne permettent pas de confirmer la coupure sur l’équipement.', next: null }
  if (!e.current.client_blocked) return { text: 'La coupure sur l’équipement n’est pas confirmée dans l’état actuel.', next: null }
  if (isCutCompleted(e.current)) return { text: 'La coupure est confirmée sur l’équipement du client dans l’état actuel.', next: 'Aucune coupure supplémentaire à effectuer pour l’ordre actuel.' }
  if (e.current.enforced_by === 'router') return {
    text: 'L’accès est bloqué sur le routeur. La coupure sur l’équipement du client n’est pas encore confirmée.',
    next: e.current.unenforceable_reason
      ? 'Résoudre le problème d’accès à l’équipement, puis confirmer sa coupure.'
      : 'Dès que l’équipement est accessible, effectuer sa coupure et confirmer le résultat.',
  }
  return { text: 'Un ordre de coupure est actif, mais la coupure sur l’équipement n’est pas confirmée.', next: e.current.unenforceable_reason
    ? 'Résoudre le problème d’accès à l’équipement avant de reprendre la coupure.'
    : 'Faire vérifier l’application de la coupure et confirmer son résultat.' }
}

export function explainDifference(e: FaiJournalEntry): string | null {
  if (!e.ok && isCutCompleted(e.current)) return 'Cette tentative avait échoué. Aujourd’hui, la coupure est confirmée sur l’équipement. La réussite actuelle ne change pas le résultat de cette ancienne tentative.'
  if (!e.current.known || !e.current.client_blocked) return null
  if (e.action === 'UNBLOCK' || e.action === 'ROUTER_UNBLOCK') return 'Cette ancienne remise en service et l’ordre de coupure actuel décrivent des moments différents. Le journal ne permet pas de les attribuer à un même cycle de demande.'
  if (e.action === 'ROUTER_BLOCK' && isCutCompleted(e.current)) return 'Cet événement décrit le blocage sur le routeur. Aujourd’hui, la coupure est confirmée sur l’équipement du client.'
  return null
}
