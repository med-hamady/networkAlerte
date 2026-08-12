"""Bandeau d'anomalies à acquitter à la main — les règles qui portent tout.

Trois anomalies (F60 dégradée / vitesse de port dégradée / équipement instable)
sont répétées dans un bandeau en haut du dashboard, d'où elles ne partent QUE
sur un clic « Résoudre ».

Deux propriétés font l'essentiel du comportement, et aucune n'est visible en
lisant le code de l'appelant :

1. **La liste des types est fermée** — y ajouter une variante « hors service »
   (`switch_port_down`, `af60_link_down`) doublerait le canal WhatsApp d'un
   bandeau qu'il faut ensuite éteindre à la main, sur des pannes qui se voient
   déjà. Le choix des 3 types « dégradés » est délibéré (2026-08-12).

2. **Une ligne naît quand un incident NOUVEAU s'ouvre, jamais sur un
   ré-déclenchement.** C'est la règle de récidive : une anomalie qui dure ne se
   resignale pas, une anomalie qui revient après rétablissement, si. Elle tient
   entièrement au fait que le point d'accroche est SOUS le `return existing,
   False` de `open_incident` — un déplacement de deux lignes plus haut la
   casserait sans qu'aucun autre test ne bronche.

⚠️ Ce canal ne doit RIEN changer au cycle de vie des incidents (ouverture,
résolution automatique, purge, notification WhatsApp) — c'est la contrainte
posée à la demande. Les tests d'ouverture/résolution existants restent la
référence de ce côté-là.
"""

import datetime

from app.core.alert_constants import (
    AT_AF60_LINK_DOWN,
    AT_AF60_LINK_SATURATED,
    AT_AF60_LINK_SUBSTANDARD,
    AT_DEVICE_FLAPPING,
    AT_ROCKET_DOWN,
    AT_SWITCH_PORT_DOWN,
    AT_SWITCH_PORT_SPEED_LOW,
    KNOWN_ALERT_TYPES,
    MANUAL_ACK_ALERT_TYPES,
)
from app.services import manual_alert_service


class _FakeSession:
    """Assez de session pour `record_detection`, qui ne fait qu'un `add`."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)


class _FakeDevice:
    id = 42
    name = "A2-CT1-EST"
    ip_address = "10.135.80.1"


def _record(session: _FakeSession, alert_type: str | None):
    return manual_alert_service.record_detection(
        session,
        _FakeDevice(),
        alert_type=alert_type,
        title="Titre",
        severity="critical",
        description=None,
        detected_at=datetime.datetime.now(datetime.UTC),
    )


# ---------------------------------------------------------------------------
# 1. La liste des types est fermée
# ---------------------------------------------------------------------------

def test_the_three_degraded_types_are_the_whole_list():
    expected = frozenset({
        AT_AF60_LINK_SUBSTANDARD,
        AT_SWITCH_PORT_SPEED_LOW,
        AT_DEVICE_FLAPPING,
    })
    assert expected == MANUAL_ACK_ALERT_TYPES


def test_the_down_variants_are_deliberately_excluded():
    """Un équipement franchement mort se voit et réveille déjà WhatsApp.

    Le bandeau existe pour la dégradation silencieuse — celle qui passe et
    repasse sans laisser de trace, l'incident étant purgé à sa résolution.
    """
    assert AT_SWITCH_PORT_DOWN not in MANUAL_ACK_ALERT_TYPES
    assert AT_AF60_LINK_DOWN not in MANUAL_ACK_ALERT_TYPES
    assert AT_ROCKET_DOWN not in MANUAL_ACK_ALERT_TYPES
    # Saturation ≠ dégradation : elle n'est même pas encore notifiée, le temps
    # de caler ses seuils sur du vécu (cf. alert_constants).
    assert AT_AF60_LINK_SATURATED not in MANUAL_ACK_ALERT_TYPES


def test_every_manual_type_is_a_real_alert_type():
    """Une faute de frappe ici serait un bandeau qui ne s'affiche jamais."""
    assert MANUAL_ACK_ALERT_TYPES <= KNOWN_ALERT_TYPES


# ---------------------------------------------------------------------------
# 2. record_detection ne pose une ligne que pour ces types
# ---------------------------------------------------------------------------

def test_a_manual_type_is_recorded():
    session = _FakeSession()
    alert = _record(session, AT_SWITCH_PORT_SPEED_LOW)
    assert alert is not None
    assert session.added == [alert]
    assert alert.device_id == 42
    assert alert.acknowledged_at is None   # naît dans le bandeau, donc en attente


def test_any_other_type_is_ignored():
    for alert_type in (AT_ROCKET_DOWN, AT_SWITCH_PORT_DOWN, AT_AF60_LINK_DOWN, None):
        session = _FakeSession()
        assert _record(session, alert_type) is None
        assert session.added == []


def test_record_detection_does_not_flush():
    """La ligne doit partager la transaction de l'incident qui l'a fait naître.

    Un flush prématuré (ou pire, un commit) ferait survivre une ligne de bandeau
    à un rollback qui a annulé l'incident : le bandeau signalerait alors une
    anomalie dont plus aucune trace n'existe. `_FakeSession` n'expose ni `flush`
    ni `commit` — ce test échoue en AttributeError si on en ajoute un.
    """
    session = _FakeSession()
    assert _record(session, AT_DEVICE_FLAPPING) is not None


# ---------------------------------------------------------------------------
# 3. Le point d'accroche : sous le `return existing, False` d'open_incident
# ---------------------------------------------------------------------------

def test_open_incident_records_only_on_a_brand_new_incident():
    """Verrouille la règle de récidive au bon endroit du fichier.

    `open_incident` a deux sorties : le `return existing, False` du
    ré-déclenchement, et le `return incident, True` de la détection nouvelle.
    L'appel à `record_detection` doit être APRÈS le premier — sinon une anomalie
    qui dure repeuplerait le bandeau à chaque cycle de poll (soit ~1 ligne par
    minute à acquitter), et l'acquittement deviendrait impossible à tenir.
    """
    import inspect

    from app.services import incident_service

    source = inspect.getsource(incident_service.open_incident)
    early_return = source.index("return existing, False")
    hook = source.index("record_detection")
    assert early_return < hook
