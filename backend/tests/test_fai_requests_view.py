"""Page « Demandes de coupure » : filtres du journal + verdict « coupé où ».

Deux mécanismes s'y cassent en SILENCE, c'est-à-dire sans lever, et les deux
répondraient alors quelque chose de faux plutôt que rien :

  - **La fenêtre de dates.** Le filtre compare des CHAÎNES (le format ISO du
    journal le permet, cf. ``fai_audit.read_entries``). Une borne de fin rendue
    exclusive au lieu d'inclusive ferait répondre « aucune demande » sur la
    journée que l'opérateur vient exactement de saisir — un négatif faux, le
    pire mode de défaillance pour une piste d'audit.
  - **Le verdict `enforcement_state`.** Son cas piégeux est le LR ABANDONNÉ :
    il porte un ``client_block_enforced_at`` datant d'avant l'abandon, et le
    lire comme « coupé sur son LR » masquerait que c'est en réalité le routeur
    qui tient ce client — donc ferait croire la coupure deux fois plus solide
    qu'elle ne l'est.
"""

import datetime

import pytest

from app.services import client_block_service, fai_audit

_MAC_A = "d0:21:f9:f6:07:c2"
_MAC_B = "6c:63:f8:d2:56:a6"


def _line(
    ts: str,
    *,
    source: str = "Block_all.php",
    mac: str = _MAC_A,
    action: str = "BLOCK",
    ok: bool = True,
    name: str = "36086261-Toutou",
) -> str:
    """Une ligne au format courant du journal (champ ``user=`` compris)."""
    return (
        f"{ts} | {action:<9} | ok={str(ok):<5} | {mac:<17} | {name} "
        f"| mode=full | source={source} | user=auto system | Client bloqué.\n"
    )


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Trois jours, deux origines — de quoi croiser les deux filtres."""
    path = tmp_path / "fai_actions.log"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_line("2026-09-11T23:59:59Z"))                       # veille
        fh.write(_line("2026-09-12T00:00:00Z", mac=_MAC_B))           # jour visé
        fh.write(_line("2026-09-12T23:59:59Z"))                       # jour visé
        fh.write(_line("2026-09-13T00:00:00Z"))                       # lendemain
        fh.write(_line("2026-09-12T08:00:00Z", source="enforce",
                       action="RETRY_OK"))                            # autre origine

    settings = fai_audit.get_settings()
    monkeypatch.setattr(settings, "fai_log_path", str(path), raising=False)
    monkeypatch.setattr(settings, "fai_evidence_dir", str(tmp_path / "evidence"), raising=False)
    return path


# ── Fenêtre de dates ────────────────────────────────────────────────────────

def test_un_seul_jour_rend_ce_jour_entier(journal):
    """De 00:00:00 à 23:59:59 — et rien de la veille ni du lendemain."""
    entries, _ = fai_audit.read_entries(
        start=datetime.date(2026, 9, 12), end=datetime.date(2026, 9, 12),
    )

    assert {e["timestamp"] for e in entries} == {
        "2026-09-12T00:00:00Z", "2026-09-12T08:00:00Z", "2026-09-12T23:59:59Z",
    }


def test_la_borne_de_fin_est_incluse(journal):
    """La régression à craindre : `end` exclusif viderait le dernier jour saisi."""
    entries, _ = fai_audit.read_entries(
        start=datetime.date(2026, 9, 11), end=datetime.date(2026, 9, 12),
    )

    assert any(e["timestamp"].startswith("2026-09-12") for e in entries)
    assert not any(e["timestamp"].startswith("2026-09-13") for e in entries)


def test_une_borne_de_debut_seule_vaut_depuis(journal):
    """Le service accepte une borne isolée ; c'est l'endpoint qui exige la paire."""
    entries, _ = fai_audit.read_entries(start=datetime.date(2026, 9, 13))

    assert [e["timestamp"] for e in entries] == ["2026-09-13T00:00:00Z"]


# ── Origine ─────────────────────────────────────────────────────────────────

def test_filtre_origine_insensible_a_la_casse(journal):
    entries, _ = fai_audit.read_entries(source="block_all.php")

    assert len(entries) == 4
    assert {e["source"] for e in entries} == {"Block_all.php"}


def test_l_origine_est_exacte_et_non_un_prefixe(journal):
    """`enforce` ne doit pas ramasser les lignes d'un autre script."""
    entries, _ = fai_audit.read_entries(source="enforce")

    assert [e["action"] for e in entries] == ["RETRY_OK"]


def test_les_filtres_n_amputent_pas_les_compteurs(journal):
    """Les compteurs décrivent le JOURNAL, pas la sélection — contrat existant."""
    _, stats = fai_audit.read_entries(
        source="Block_all.php",
        start=datetime.date(2026, 9, 12), end=datetime.date(2026, 9, 12),
    )

    assert stats["total"] == 5


# ── Par quel mécanisme le client est-il coupé ? ─────────────────────────────

_ENFORCED = datetime.datetime(2026, 9, 12, 3, 14, tzinfo=datetime.UTC)


def test_aucun_ordre_ne_coupe_personne():
    assert client_block_service.enforcement_state(
        client_blocked=False, enforced_at=_ENFORCED,
        unenforceable_reason=None, router_blocked=True,
    ) is None


def test_coupure_posee_sur_le_lr():
    assert client_block_service.enforcement_state(
        client_blocked=True, enforced_at=_ENFORCED,
        unenforceable_reason=None, router_blocked=False,
    ) == "lr"


def test_ordre_pris_mais_jamais_applique_le_client_navigue():
    """Le cas qui coûte de l'argent : la demande existe, la coupure non."""
    assert client_block_service.enforcement_state(
        client_blocked=True, enforced_at=None,
        unenforceable_reason=None, router_blocked=False,
    ) is None


def test_repli_routeur():
    assert client_block_service.enforcement_state(
        client_blocked=True, enforced_at=None,
        unenforceable_reason=None, router_blocked=True,
    ) == "router"


def test_un_lr_abandonne_est_tenu_par_le_routeur_malgre_son_enforced_at():
    """LE piège : `enforced_at` date d'AVANT l'abandon, un reboot a pu l'effacer.

    Même raisonnement que `desired_router_block`, qui laisse justement le
    routeur couvrir ces clients-là. Conclure « coupé sur le LR » ici ferait
    croire la coupure posée sur un équipement qu'on a cessé de piloter.
    """
    assert client_block_service.enforcement_state(
        client_blocked=True, enforced_at=_ENFORCED,
        unenforceable_reason="authentication failed", router_blocked=True,
    ) == "router"


def test_un_lr_abandonne_sans_repli_ne_coupe_rien():
    assert client_block_service.enforcement_state(
        client_blocked=True, enforced_at=_ENFORCED,
        unenforceable_reason="authentication failed", router_blocked=False,
    ) is None
