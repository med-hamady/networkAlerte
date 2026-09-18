/**
 * Droits de l'utilisateur connecté, côté dashboard.
 *
 * ⚠️ **Rien ici ne PROTÈGE quoi que ce soit.** Ce module sert à ne pas montrer
 * à quelqu'un des écrans et des boutons qui lui répondraient 403. Le contrôle
 * réel est posé sur chaque route du backend (`deps.require_permission`) : le
 * proxy du dashboard relaie les appels avec le cookie de session, donc un
 * bouton simplement masqué resterait appelable depuis l'onglet réseau du
 * navigateur. Masquer sans garder la route serait une illusion de
 * cloisonnement — la pire des deux situations, puisqu'on cesserait de chercher.
 *
 * ⚠️ **Les clés ne sont pas recopiées ici.** Elles viennent de
 * `app/core/permissions.py` et arrivent dans `/auth/me`. Les seules chaînes
 * écrites dans le frontend sont celles des points où l'on décide d'afficher ou
 * non — et elles sont groupées dans `PERM` ci-dessous plutôt que dispersées,
 * pour qu'un renommage se voie d'un seul endroit.
 */

import useSWR from 'swr'
import { endpoints, fetcher, type CurrentUser } from '@/lib/api'

/**
 * Les clés utilisées par le dashboard. Miroir partiel du catalogue backend :
 * seules y figurent celles sur lesquelles une décision d'affichage est prise.
 *
 * ⚠️ Une clé absente du backend ne fait rien échouer — elle rend simplement
 * `can()` faux, donc masque l'élément. Le test `test_permission_catalog.py`
 * vérifie que toutes les clés citées ici existent bien au catalogue, sinon une
 * faute de frappe masquerait un écran à tout le monde en silence.
 */
export const PERM = {
  dashboard: 'dashboard.view',
  sites: 'sites.view',
  lrHealth: 'lr_health.view',
  clients: 'clients.view',
  capacity: 'capacity.view',
  topology: 'topology.view',
  topologyExport: 'topology.export',
  topologySync: 'topology.sync',
  map: 'map.view',
  traffic: 'traffic.view',

  deviceCreate: 'devices.create',
  deviceEdit: 'devices.edit',
  deviceDelete: 'devices.delete',
  deviceDiagnostics: 'devices.diagnostics',
  devicePowerOutput: 'devices.power_output',
  deviceEnrollUisp: 'devices.enroll_uisp',
  devicePlanSync: 'devices.plan_sync',
  uispSync: 'uisp.sync',

  fai: 'fai.view',
  faiStats: 'fai.stats',
  faiRequests: 'fai.requests.view',
  faiJournal: 'fai.journal.view',
  routerRules: 'fai.router_rules.view',
  contentFilter: 'fai.content_filter.view',
  contentFilterEdit: 'fai.content_filter.edit',
  faiBlock: 'fai.block',

  incidents: 'incidents.view',
  accessDiagnostics: 'access_diagnostics.view',
  accessDiagnosticsEnroll: 'access_diagnostics.enroll',
  manualAlertAck: 'manual_alerts.acknowledge',

  reports: 'reports.view',
  thresholds: 'thresholds.view',
  thresholdsEdit: 'thresholds.edit',
  testWhatsapp: 'system.test_whatsapp',

  adminAccess: 'admin.access',
  adminProfiles: 'admin.profiles',
  adminUsers: 'admin.users',
} as const

export type PermissionKey = (typeof PERM)[keyof typeof PERM]

export interface PermissionState {
  /** Vrai tant que /auth/me n'a pas répondu — rien ne doit être décidé avant. */
  loading: boolean
  user: CurrentUser | undefined
  /** Profil système : tout, y compris ce qui sera ajouté plus tard. */
  isAdmin: boolean
  /** Vrai si le compte détient AU MOINS UNE des clés données. */
  can: (...keys: string[]) => boolean
}

/**
 * Droits du compte connecté.
 *
 * ⚠️ `loading` existe pour éviter le clignotement inverse du bon : sans lui,
 * le premier rendu (avant la réponse de /auth/me) ne détient aucun droit et
 * ferait disparaître tout le menu pendant une fraction de seconde, y compris
 * pour un administrateur. Les appelants affichent donc l'élément pendant le
 * chargement, ou rien du tout — jamais un « accès refusé ».
 *
 * La requête est dédupliquée par SWR sur la clé d'URL : la barre latérale, le
 * bandeau et la page courante partagent un seul appel réseau.
 */
export function usePermissions(): PermissionState {
  const { data: user, isLoading } = useSWR<CurrentUser>(
    endpoints.authMe,
    fetcher,
    { refreshInterval: 60_000, shouldRetryOnError: false },
  )

  const granted = new Set(user?.permissions ?? [])
  const isAdmin = user?.is_admin === true

  return {
    loading: isLoading && user === undefined,
    user,
    isAdmin,
    // `is_admin` court-circuite la liste : le profil système détient tout par
    // construction côté serveur, et le frontend doit dire la même chose même
    // si la liste envoyée devait un jour être tronquée.
    can: (...keys: string[]) =>
      isAdmin || keys.some((key) => granted.has(key)),
  }
}

/**
 * La première page que ce compte a le droit de voir, dans l'ordre du menu.
 *
 * Sert de destination de repli quand quelqu'un arrive sur une page qui ne lui
 * est pas ouverte (un signet, un lien partagé). Le renvoyer vers `/` serait
 * faux : le tableau de bord est lui-même une permission, et un agent qui ne
 * l'a pas rebondirait indéfiniment.
 */
export function firstAllowedRoute(can: (...keys: string[]) => boolean): string | null {
  const ordered: Array<[string, string]> = [
    ['/', PERM.dashboard],
    ['/sites', PERM.sites],
    ['/lr-health', PERM.lrHealth],
    ['/clients', PERM.clients],
    ['/capacity', PERM.capacity],
    ['/topology', PERM.topology],
    ['/map', PERM.map],
    ['/traffic', PERM.traffic],
    ['/access', PERM.fai],
    ['/fai-requests', PERM.faiRequests],
    ['/fai-journal', PERM.faiJournal],
    ['/router-rules', PERM.routerRules],
    ['/content-block', PERM.contentFilter],
    ['/incidents', PERM.incidents],
    ['/access-diagnostics', PERM.accessDiagnostics],
    ['/reports', PERM.reports],
    ['/settings', PERM.thresholds],
    ['/admin', PERM.adminAccess],
  ]
  for (const [route, key] of ordered) {
    if (can(key)) return route
  }
  return null
}
