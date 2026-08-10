"""Lecture des règles de coupure du routeur — `mikrotik_service.parse_rules` +
`router_rules_service.classify`.

Ce que ces tests protègent, dans l'ordre d'importance :

  1. **La portée** — la page promet « les blocages d'abonnés ». Laisser passer
     une règle d'une autre chaîne, une règle sans MAC ou une règle qui n'est pas
     un ``drop`` afficherait du pare-feu d'infrastructure comme une coupure
     client. Un opérateur y lirait un client coupé qui ne l'est pas.
  2. **Les deux désaccords** — règle en trop (client coupé alors qu'il a payé)
     et MAC inconnue. C'est la seule valeur de la page par rapport à un
     `print` sur le routeur.
"""

from app.services import mikrotik_service, router_rules_service


def _rule(**over) -> dict:
    """Une règle telle que RouterOS la rend (forme observée en production)."""
    raw = {
        ".id": "*1A",
        "chain": "forward",
        "action": "drop",
        "src-mac-address": "D0:21:F9:F6:07:C2",
        "comment": "supervisor block 2026-08-06 09:12:00",
        "disabled": False,
        "dynamic": False,
        "packets": 42,
        "bytes": 4096,
    }
    raw.update(over)
    return raw


# ── Portée : ce qui compte comme une coupure client ─────────────────────────


def test_une_regle_de_coupure_client_est_retenue():
    rules = mikrotik_service.parse_rules([_rule()])
    assert len(rules) == 1
    assert rules[0]["mac"] == "D0:21:F9:F6:07:C2"
    assert rules[0]["id"] == "*1A"


def test_une_autre_chaine_est_ecartee():
    """Une règle `input` protège le routeur lui-même : rien à voir avec un abonné."""
    assert mikrotik_service.parse_rules([_rule(chain="input")]) == []


def test_une_action_non_drop_est_ecartee():
    assert mikrotik_service.parse_rules([_rule(action="accept")]) == []


def test_une_regle_sans_mac_est_ecartee():
    """Un drop de sous-réseau n'est pas la coupure d'un client."""
    assert mikrotik_service.parse_rules([_rule(**{"src-mac-address": ""})]) == []
    raw = _rule()
    del raw["src-mac-address"]
    assert mikrotik_service.parse_rules([raw]) == []


def test_la_mac_est_normalisee_en_majuscules():
    """RouterOS compare en majuscules ; le croisement avec notre base, en
    minuscules. Une seule forme sort d'ici pour que le rapprochement tienne."""
    rules = mikrotik_service.parse_rules([_rule(**{"src-mac-address": "d0:21:f9:f6:07:c2"})])
    assert rules[0]["mac"] == "D0:21:F9:F6:07:C2"


def test_une_regle_desactivee_est_listee_mais_signalee():
    """Elle existe sur le routeur sans couper personne — c'est justement ce qui
    explique un client « bloqué » toujours en ligne. La cacher effacerait
    l'explication."""
    rules = mikrotik_service.parse_rules([_rule(disabled="true")])
    assert len(rules) == 1
    assert rules[0]["disabled"] is True


def test_les_valeurs_texte_de_routeros_sont_castees():
    """Selon la version, librouteros rend `true` en chaîne ou en booléen."""
    rules = mikrotik_service.parse_rules([_rule(dynamic="false", disabled="true")])
    assert rules[0]["dynamic"] is False
    assert rules[0]["disabled"] is True


def test_des_compteurs_absents_ne_cassent_pas_la_lecture():
    raw = _rule()
    del raw["packets"]
    rules = mikrotik_service.parse_rules([raw])
    assert rules[0]["packets"] is None


# ── Origine : nos règles vs celles du système historique ────────────────────


def test_notre_propre_commentaire_est_reconnu():
    """Le préfixe est écrit par `build_comment` : les deux côtés doivent rester
    d'accord, sinon la page classe toutes nos règles en « historique »."""
    assert mikrotik_service.is_supervisor_comment(mikrotik_service.build_comment("block A2"))


def test_un_commentaire_legacy_nest_pas_le_notre():
    assert mikrotik_service.is_supervisor_comment("Toutoumedlimam impaye") is False
    assert mikrotik_service.is_supervisor_comment("") is False


# ── Les désaccords base ↔ routeur ───────────────────────────────────────────


class _FakeLr:
    def __init__(self, **kwargs):
        self.client_blocked = True
        self.__dict__.update(kwargs)


def test_coupure_voulue_par_la_base_est_normale():
    assert router_rules_service.classify(_rule(), _FakeLr(client_blocked=True)) == "expected"


def test_client_a_ne_plus_couper_est_signale():
    """LE cas qui coûte : le client a payé, la base ne veut plus le couper, et le
    routeur le coupe encore. Personne d'autre ne le voit — le renforcement ne
    parle au routeur que sur transition."""
    assert router_rules_service.classify(_rule(), _FakeLr(client_blocked=False)) == "unexpected"


def test_mac_hors_inventaire_nest_pas_une_anomalie_de_coupure():
    """Règle du système historique, ou LR supprimé de la base : à montrer, mais
    ce n'est pas la même urgence qu'un abonné coupé à tort."""
    assert router_rules_service.classify(_rule(), None) == "unknown"
