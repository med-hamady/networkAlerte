"""
Contrôle d'identité avant toute action de blocage — `ssh_service`.

Une fiche identifie son client par sa **MAC**, mais la session SSH part sur son
**IP**. Or une IP est distribuée en DHCP : pendant qu'un abonné est éteint, la
sienne peut être redonnée à un autre. Sans ce contrôle, une coupure demandée par
le système de paiement pour le client A tombait sur le client B, qui paie.

Le contrôle coûte une commande sur la session DÉJÀ ouverte — ce qui est cher sur
ces radios, c'est la poignée de main SSH (elles décrochent au-delà d'environ 150
simultanées), pas une lecture dans /sys.
"""

from unittest.mock import patch

from app.services import client_block_service
from app.services.ssh_service import IDENTITY_REFUSAL_PREFIX, identity_refusal

_EXPECTED = "1c:6a:1b:b8:79:b0"
_OTHER = "6c:63:f8:cc:d1:bb"


def _macs(*values: str):
    """Patch la lecture des MAC de l'équipement joint."""
    return patch("app.services.ssh_service._device_macs", return_value=set(values))


def test_matching_mac_lets_the_action_through():
    with _macs(_EXPECTED, "1c:6a:1b:b8:79:b1"):
        assert identity_refusal(object(), _EXPECTED) is None


def test_case_and_spacing_do_not_matter():
    """La MAC de la fiche et celle du firmware n'ont pas la même casse."""
    with _macs(_EXPECTED):
        assert identity_refusal(object(), "  1C:6A:1B:B8:79:B0 ") is None


def test_different_mac_is_refused():
    """LE cas qui protège : l'IP de la fiche répond, mais c'est quelqu'un d'autre."""
    with _macs(_OTHER):
        refusal = identity_refusal(object(), _EXPECTED)
    assert refusal is not None
    assert refusal.startswith(IDENTITY_REFUSAL_PREFIX)
    assert _OTHER in refusal      # l'opérateur voit QUI a répondu
    assert _EXPECTED in refusal   # et qui était attendu


def test_unreadable_device_is_allowed_through():
    """Invérifiable ≠ refusé.

    Un firmware sans `/sys/class/net` rendrait sinon TOUT blocage impossible sur
    cette famille d'équipements — une panne bien pire que le risque couvert. On
    ne refuse que sur une preuve positive de non-correspondance.
    """
    with _macs():
        assert identity_refusal(object(), _EXPECTED) is None


def test_no_expected_mac_skips_the_check():
    """Fiche sans MAC connue : rien à comparer, on n'invente pas un refus."""
    assert identity_refusal(object(), None) is None
    assert identity_refusal(object(), "") is None


def test_refusal_is_treated_as_structural():
    """Réessayer toutes les 120 s ne peut pas aider : l'IP restera fausse.

    Le job d'enforcement doit donc sortir cette ligne de sa boucle de reprise,
    comme pour une authentification refusée — sinon il rejoue indéfiniment une
    action qu'on refuse d'appliquer.
    """
    with _macs(_OTHER):
        refusal = identity_refusal(object(), _EXPECTED)
    assert client_block_service._structural_failure(refusal) is not None
    assert client_block_service.is_identity_refusal(refusal) is True


def test_a_plain_ssh_failure_is_not_an_identity_refusal():
    """« mot de passe faux » et « mauvais équipement » ne se confondent pas.

    Les deux sont des abandons, mais seul le second demande de corriger la
    FICHE plutôt que l'équipement — d'où deux entrées distinctes au journal.
    """
    assert client_block_service.is_identity_refusal("Authentication failed") is False
    assert client_block_service.is_identity_refusal(None) is False
