"""
Catalogue des permissions — SOURCE UNIQUE DE VÉRITÉ des droits d'accès.

Même rôle que `alert_constants.py` pour les `alert_type` : toute clé de
permission est définie ICI et nulle part ailleurs. Un profil ne stocke que des
clés de ce catalogue ; l'API de l'administration le publie tel quel, donc la
page « Profils » se construit toute seule à partir de ce fichier — **ajouter une
permission ici suffit à la rendre cochable**, sans migration et sans toucher au
frontend.

⚠️ **Deux natures de permission, et la distinction porte l'écran** :

* `PAGE` — « cet utilisateur voit cette interface ». Pilote l'entrée de menu,
  l'accès à la route du dashboard, et les lectures qui alimentent la page.
* `ACTION` — « cet utilisateur a le droit de faire ce geste ». Un agent peut
  parfaitement voir la page FAI sans pouvoir couper un abonné.

Les fondre en une seule case rendrait impossible le cas qui justifie tout ce
travail : donner à quelqu'un la vue sans le pouvoir.

⚠️ **Une clé n'est JAMAIS renommée ni supprimée à la légère** : elle est
persistée en base dans `profiles.permissions`. La renommer retire silencieusement
un droit à tous les profils qui la portaient — c.-à-d. verrouille des gens dehors
sans le moindre message d'erreur. Si une clé doit disparaître, il faut une
migration qui la retire (ou la remplace) dans les lignes existantes, comme la
migration `b1c2d3e4f5a6` l'a fait pour la catégorie `google` du filtre de contenu.

⚠️ **Le profil Administrateur ne consomme PAS ce catalogue** : il est
`is_system=True` et détient tout **par construction** (`profile_service`), pas
par une liste de cases cochées. Sinon, ajouter une permission ici retirerait un
pouvoir à l'admin jusqu'à ce que quelqu'un pense à re-cocher la case — et la
toute première permission oubliée pourrait être celle qui donne accès à
l'administration elle-même.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PermissionKind(StrEnum):
    """Nature d'une permission — trois, et la distinction porte l'écran.

    ⚠️ `DATA` n'est pas un raffinement cosmétique de `PAGE` : il répond à une
    question que `PAGE` ne sait pas poser — « il a le droit d'ouvrir cet écran,
    mais a-t-il le droit d'en voir TOUS les chiffres ? ». Le premier cas est
    les compteurs d'abonnés de la page FAI : un profil de supervision doit
    pouvoir traiter la ligne d'un client sans lire le nombre total d'abonnés,
    d'actifs et de bloqués — c'est-à-dire la taille commerciale du parc.

    Le ranger dans `ACTION` aurait marché techniquement et menti à l'écran :
    le bloc s'intitule « les gestes que ce profil peut poser », et voir un
    chiffre n'est pas un geste.
    """

    PAGE = "page"
    ACTION = "action"
    DATA = "data"


@dataclass(frozen=True)
class Permission:
    """Une case à cocher du formulaire de profil."""

    key: str
    label: str
    description: str
    kind: PermissionKind
    # Chemin de la page du dashboard, pour les permissions de nature PAGE. Sert
    # au frontend à savoir quelle entrée de menu masquer, et à rediriger un
    # utilisateur qui arrive sur une page qu'il n'a pas le droit de voir.
    route: str | None = None
    # ⚠️ MASQUAGE D'AFFICHAGE SEULEMENT — la donnée continue de voyager dans la
    # réponse, elle est simplement retirée de l'écran.
    #
    # Ce drapeau existe pour que la différence soit ÉCRITE plutôt que supposée.
    # Un droit `DATA` ordinaire est applique côté serveur (la donnée est retirée
    # de la réponse) ; un droit `ui_only` ne l'est pas, donc il reste lisible
    # dans l'onglet réseau du navigateur par qui va le chercher.
    #
    # C'est un choix d'exploitation légitime quand la donnée n'est pas
    # confidentielle mais encombre l'écran d'un profil qui n'en a pas l'usage.
    # Ce qui ne serait PAS légitime, c'est de ne pas pouvoir distinguer les deux
    # d'un coup d'œil : sans ce drapeau, on finirait par croire cloisonné ce qui
    # ne l'est pas — et on cesserait de chercher.
    ui_only: bool = False


@dataclass(frozen=True)
class PermissionGroup:
    """Un bloc du formulaire — reprend les sections de la barre latérale."""

    key: str
    label: str
    description: str
    permissions: tuple[Permission, ...]


def _page(key: str, label: str, description: str, route: str) -> Permission:
    return Permission(key=key, label=label, description=description,
                      kind=PermissionKind.PAGE, route=route)


def _action(key: str, label: str, description: str) -> Permission:
    return Permission(key=key, label=label, description=description,
                      kind=PermissionKind.ACTION)


def _ui(key: str, label: str, description: str) -> Permission:
    """Un bloc d'information RETIRÉ DE L'ÉCRAN, mais pas de la réponse.

    ⚠️ À ne pas confondre avec `_data` : ici rien n'est applique côté serveur.
    À réserver aux chiffres qu'on retire par confort de lecture, jamais à une
    donnée dont la divulgation compte.
    """
    return Permission(key=key, label=label, description=description,
                      kind=PermissionKind.DATA, ui_only=True)


def _data(key: str, label: str, description: str) -> Permission:
    """Un bloc d'information À L'INTÉRIEUR d'une page déjà autorisée.

    ⚠️ Un droit de ce type doit être appliqué **côté serveur** : la donnée
    voyage dans la même réponse que le reste de la page, donc la masquer dans
    le navigateur la laisserait parfaitement lisible dans l'onglet réseau.
    C'est `deps.caller_has_permission` qui sert à la retirer de la réponse.
    """
    return Permission(key=key, label=label, description=description,
                      kind=PermissionKind.DATA)


# ---------------------------------------------------------------------------
# Le catalogue
# ---------------------------------------------------------------------------
#
# L'ordre des groupes et des permissions est celui de l'écran : il reprend la
# barre latérale, pour que l'admin coche dans l'ordre où il navigue.

PERMISSION_GROUPS: tuple[PermissionGroup, ...] = (
    PermissionGroup(
        key="supervision",
        label="Supervision",
        description="Les écrans de lecture du réseau.",
        permissions=(
            _page("dashboard.view", "Tableau de bord",
                  "Vue d'ensemble : santé du réseau, journal des coupures.", "/"),
            _ui("dashboard.stats", "Compteurs du tableau de bord",
                "La barre de chiffres en haut du tableau de bord : total "
                "d'équipements, en ligne, hors ligne, incidents ouverts, sites, "
                "pannes et clients. ⚠️ Masquage d'affichage seulement — ces "
                "chiffres restent lisibles par qui les cherche dans son "
                "navigateur."),
            _page("sites.view", "Sites",
                  "Les sites et les équipements qu'ils portent, avec leurs fiches.",
                  "/sites"),
            _data("sites.client_counts", "Compteurs clients par site",
                  "Sur chaque carte de site : « Clients en ligne » et « Clients "
                  "bloqués ». « Équipements infra » et « Pannes » restent "
                  "visibles — un profil de supervision en a besoin pour "
                  "travailler."),
            _page("lr_health.view", "Liaisons clients",
                  "Qualité des liens abonnés et des liaisons entre sites.",
                  "/lr-health"),
            _page("clients.view", "Consommation clients",
                  "Volumes consommés par abonné sur 24 h / 7 j / 30 j.", "/clients"),
            _page("capacity.view", "Capacité du réseau",
                  "Charge des Rockets et budget d'équipements par site.", "/capacity"),
            _page("topology.view", "Topologie du réseau",
                  "Le maillage des backhauls entre sites, et les routes vers Internet.",
                  "/topology"),
            _page("map.view", "Carte des clients",
                  "Position géographique des abonnés.", "/map"),
            _page("traffic.view", "Destinations Internet",
                  "Débit et volume par opérateur / CDN.", "/traffic"),
            _action("topology.export",
                    "Exporter la carte des sites (Word)",
                    "Produire le document imprimable des sites et de leurs liaisons."),
            _action("topology.sync",
                    "Rapatrier le câblage inter-sites",
                    "Forcer la lecture des liaisons depuis le contrôleur UISP, "
                    "sans attendre la synchronisation quotidienne."),
        ),
    ),
    PermissionGroup(
        key="devices",
        label="Équipements",
        description="Consultation et intervention sur le matériel supervisé.",
        permissions=(
            _action("devices.create", "Ajouter un équipement",
                    "Créer une fiche d'équipement à superviser."),
            _action("devices.edit", "Modifier un équipement",
                    "Changer le nom, l'IP, le site ou les identifiants d'une fiche."),
            _action("devices.delete", "Supprimer un équipement",
                    "Effacer définitivement une fiche et tout son historique. "
                    "Irréversible."),
            _action("devices.diagnostics", "Lancer les diagnostics",
                    "Test SSH, ping depuis l'équipement, découverte des modems "
                    "sur le LAN d'un abonné."),
            _action("devices.power_output", "Piloter les sorties UISP Power",
                    "Couper ou rétablir une sortie électrique. Coupe physiquement "
                    "l'alimentation du matériel branché dessus."),
            _action("devices.enroll_uisp", "Enrôler un équipement dans UISP",
                    "Poser la clé du contrôleur sur un CPE pour qu'il remonte dans "
                    "l'inventaire."),
            _action("devices.plan_sync", "Relever les forfaits abonnés",
                    "Lire le forfait configuré sur les LR et le rapatrier en base."),
            _action("uisp.sync", "Importer l'inventaire UISP",
                    "Rapatrier les équipements d'infrastructure et les stations "
                    "depuis le contrôleur UISP."),
            _action("uisp.assign", "Associer un équipement à un client CRM",
                    "Rattacher un CPE installé au compte de l'abonné dans UISP."),
        ),
    ),
    PermissionGroup(
        key="fai",
        label="FAI",
        description="Accès abonnés, coupures et filtrage de contenu.",
        permissions=(
            _page("fai.view", "Accès clients",
                  "La liste des abonnés et l'état de leur accès.", "/access"),
            _page("fai.requests.view", "Demandes de coupure",
                  "Ce que le système de paiement a demandé, et l'état réel du client.",
                  "/fai-requests"),
            _page("fai.journal.view", "Journal des blocages",
                  "La piste d'audit de toutes les coupures et rétablissements.",
                  "/fai-journal"),
            _page("fai.router_rules.view", "Règles du routeur",
                  "Les coupures réellement posées sur le routeur de cœur.",
                  "/router-rules"),
            _page("fai.content_filter.view", "Filtre de contenu",
                  "Les plateformes filtrées chez chaque abonné.", "/content-block"),
            _data("fai.stats", "Compteurs d'abonnés",
                  "Les chiffres en haut de la page FAI : nombre total de clients, "
                  "accès actifs, bloqués, et les badges des onglets. Sans ce droit, "
                  "le profil traite les lignes une par une sans voir la taille du parc."),
            _action("fai.block", "Couper / rétablir un abonné",
                    "Bloquer l'accès Internet d'un client, ou le lui rendre."),
            _action("fai.content_filter.edit", "Modifier le filtre de contenu",
                    "Bloquer ou débloquer une plateforme chez un abonné."),
        ),
    ),
    PermissionGroup(
        key="anomalies",
        label="Anomalies",
        description="Ce que le système a détecté et ce qu'on en fait.",
        permissions=(
            _page("incidents.view", "Incidents",
                  "Les anomalies actuellement détectées sur l'infrastructure.",
                  "/incidents"),
            _page("access_diagnostics.view", "Diagnostics d'accès",
                  "LR qui refusent le SSH, et abonnés absents de UISP.",
                  "/access-diagnostics"),
            _action("manual_alerts.acknowledge", "Acquitter le bandeau d'anomalies",
                    "Retirer une anomalie du bandeau, pour toute l'équipe."),
            _action("access_diagnostics.enroll", "Enrôler en lot dans UISP",
                    "Poser la clé du contrôleur sur tous les abonnés absents de UISP."),
        ),
    ),
    PermissionGroup(
        key="configuration",
        label="Configuration",
        description="Réglages du superviseur lui-même.",
        permissions=(
            _page("reports.view", "Rapports",
                  "Les rapports périodiques du réseau.", "/reports"),
            _page("thresholds.view", "Seuils",
                  "Les seuils qui déclenchent les alertes.", "/settings"),
            _action("thresholds.edit", "Modifier les seuils",
                    "Changer un seuil d'alerte pour tout le réseau."),
            _action("system.test_whatsapp", "Tester l'envoi WhatsApp",
                    "Envoyer un message de test au groupe d'alerte."),
        ),
    ),
    PermissionGroup(
        key="administration",
        label="Administration",
        description=(
            "Gestion des profils et des comptes. À ne donner qu'à qui administre "
            "le superviseur — ces droits permettent de s'attribuer tous les autres."
        ),
        permissions=(
            _page("admin.access", "Section Administration",
                  "Voir la section de gestion des profils et des utilisateurs.",
                  "/admin"),
            _action("admin.profiles", "Gérer les profils",
                    "Créer, modifier et supprimer les profils et leurs droits."),
            _action("admin.users", "Gérer les utilisateurs",
                    "Créer des comptes, réinitialiser un mot de passe, affecter "
                    "un profil."),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Index dérivés — construits une fois, jamais recopiés à la main
# ---------------------------------------------------------------------------

ALL_PERMISSIONS: tuple[Permission, ...] = tuple(
    perm for group in PERMISSION_GROUPS for perm in group.permissions
)

PERMISSION_KEYS: frozenset[str] = frozenset(perm.key for perm in ALL_PERMISSIONS)

PERMISSIONS_BY_KEY: dict[str, Permission] = {perm.key: perm for perm in ALL_PERMISSIONS}

# Les permissions de nature PAGE, indexées par la route du dashboard qu'elles
# gardent. Sert au frontend (masquer une entrée de menu) et au contrôle d'arrivée
# sur une page.
PAGE_PERMISSION_BY_ROUTE: dict[str, str] = {
    perm.route: perm.key
    for perm in ALL_PERMISSIONS
    if perm.kind is PermissionKind.PAGE and perm.route
}

# Les pages qui listent ou ouvrent une fiche d'équipement. `GET /devices` est la
# lecture la plus partagée de l'API : la laisser ouverte à un compte qui n'a
# AUCUNE de ces pages lui livrerait l'inventaire complet (noms, IP, sites, et le
# téléphone des clients, qui vit dans le nom des LR) par un simple appel à la
# main. Définie ICI et importée — deux copies finiraient par diverger, et la
# divergence se lirait comme un droit accordé à qui ne l'a pas.
DEVICE_READ_PERMISSIONS: tuple[str, ...] = (
    "sites.view", "map.view", "fai.view", "fai.content_filter.view",
    "lr_health.view", "capacity.view", "topology.view", "incidents.view",
    "dashboard.view", "access_diagnostics.view", "clients.view",
)

# Le nom du profil système qui détient tout. Verrouillé : il ne peut être ni
# renommé, ni supprimé, ni vidé de ses droits (cf. profile_service).
ADMIN_PROFILE_NAME = "Administrateur"


def normalize_permissions(keys: object) -> list[str]:
    """Ne garder que des clés RÉELLES du catalogue, dédoublonnées et ordonnées.

    Tolérante à l'entrée (une valeur inattendue est ignorée, pas une erreur)
    parce qu'elle sert aussi à RELIRE une colonne écrite par une version
    antérieure du catalogue : une clé retirée du code ne doit pas faire échouer
    le chargement du profil des utilisateurs qui la portaient encore.

    L'ordre rendu est celui du catalogue, pas celui de la saisie : deux profils
    aux mêmes droits produisent alors exactement la même valeur en base, donc
    un diff lisible.
    """
    if not isinstance(keys, (list, tuple, set, frozenset)):
        return []
    present = {k for k in keys if isinstance(k, str) and k in PERMISSION_KEYS}
    return [perm.key for perm in ALL_PERMISSIONS if perm.key in present]


def unknown_permissions(keys: object) -> list[str]:
    """Les clés soumises qui n'existent pas au catalogue.

    Utilisée par l'API d'administration, qui REFUSE (422) une clé inconnue là où
    `normalize_permissions` l'ignore en silence. Le silence est juste pour une
    RELECTURE, faux pour une saisie : un `fai.blok` mal orthographié rendrait
    « profil enregistré » en n'ayant donné aucun droit, et le défaut se
    découvrirait le jour où l'agent ne peut pas travailler.
    """
    if not isinstance(keys, (list, tuple, set, frozenset)):
        return []
    seen: list[str] = []
    for k in keys:
        if not isinstance(k, str) or k in PERMISSION_KEYS or k in seen:
            continue
        seen.append(k)
    return seen
