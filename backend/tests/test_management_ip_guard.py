"""
Garde-fou du plan d'adressage de management — `discovery_service`.

Une radio annonce plusieurs adresses et la plupart ne nous concernent pas : le
LAN du CPE (`192.168.10.1`, `192.168.1.20`, `172.16.0.1` = valeurs d'usine
airOS), une APIPA `169.254.x` quand le DHCP n'a pas répondu, ou `0.0.0.0` quand
elle n'a rien du tout.

Ce qui rend leur écriture DANGEREUSE et pas seulement inutile : ces adresses
sont les MÊMES sur des dizaines de CPE, alors que `devices.ip_address` est
UNIQUE. Chaque écriture vole donc la ligne du détenteur précédent
(`_release_ip_if_held`), qui se retrouve sans IP et en `status="unknown"`,
c.-à-d. hors du sweep de ping. Un CPE qui annonce brièvement son LAN éteignait
ainsi un AUTRE client, sain, ailleurs sur le réseau.

Observé en prod le 2026-07-22 sur plusieurs abonnés, en boucle toutes les
3 min : `10.135.3.116 → 169.254.210.235 → 10.135.3.116 → 192.168.10.1 → …`
"""

import pytest

from app.services.discovery_service import is_management_ip, pick_management_ip


@pytest.mark.parametrize("ip", ["10.135.3.116", "10.135.4.13", "10.135.164.2"])
def test_management_plan_addresses_are_accepted(ip):
    assert is_management_ip(ip) is True


@pytest.mark.parametrize(
    "ip",
    [
        "192.168.10.1",     # LAN airOS d'usine — partagé par des dizaines de CPE
        "192.168.1.20",     # idem
        "172.16.0.1",       # idem
        "169.254.210.235",  # APIPA : le DHCP n'a pas répondu
        "0.0.0.0",          # aucune adresse
        "127.0.0.1",
        "pas-une-ip",
        "",
        None,
    ],
)
def test_addresses_outside_the_plan_are_refused(ip):
    assert is_management_ip(ip) is False


def test_pick_selects_on_the_plan_not_on_the_position():
    """Le LAN arrive souvent EN PREMIER — prendre `[0]` serait un tirage au sort."""
    assert pick_management_ip(["192.168.10.1", "10.135.3.116"]) == "10.135.3.116"
    assert pick_management_ip(["10.135.3.116", "192.168.10.1"]) == "10.135.3.116"


def test_pick_returns_none_when_nothing_is_eligible():
    """Aucune candidate valable → None, et l'appelant laisse l'IP existante.

    C'est le comportement qui coupe le battement : une station qui n'annonce
    que son LAN ne provoque plus AUCUNE écriture.
    """
    assert pick_management_ip(["192.168.10.1", "0.0.0.0", None]) is None
    assert pick_management_ip([]) is None


def test_empty_configuration_disables_the_filter(monkeypatch):
    """`MANAGEMENT_IP_CIDRS` vide = filtre désactivé (retour au comportement d'avant).

    Une porte de sortie sans redéploiement de code si un site sort du plan.
    """
    from app.core import config

    settings = config.get_settings()
    monkeypatch.setattr(type(settings), "management_ip_cidr_list", property(lambda _s: []))
    assert is_management_ip("192.168.10.1") is True


def test_invalid_cidr_in_config_does_not_crash_discovery(monkeypatch):
    """Un préfixe mal saisi est ignoré (log), il ne fait pas tomber le cycle."""
    from app.core import config

    settings = config.get_settings()
    monkeypatch.setattr(
        type(settings),
        "management_ip_cidr_list",
        property(lambda _s: ["pas-un-cidr", "10.135.0.0/16"]),
    )
    assert is_management_ip("10.135.3.116") is True
    assert is_management_ip("192.168.10.1") is False
