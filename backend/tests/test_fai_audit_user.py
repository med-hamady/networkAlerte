"""Le champ « agent » (`user=`) du journal FAI, ajouté sur un journal déjà écrit.

Le système de paiement transmet désormais l'agent à l'origine de chaque ordre
(e-mail d'un opérateur, ou libellé automatique « auto system » / « auto retry »).
Il est écrit comme un champ de plus dans la ligne d'audit.

⚠️ Ce qui est réellement en jeu ici n'est pas l'écriture mais la **relecture** :
le journal de production contient des dizaines de milliers de lignes SANS ce
champ. Une relecture à arité fixe les rejetterait toutes d'un coup — c.-à-d.
effacerait la piste d'audit en la laissant sur disque. Ces tests fixent donc les
deux formats, et le cas tordu qui les distingue mal (un ancien message contenant
lui-même un ` | `).
"""

import pytest

from app.services import fai_audit

_MAC = "d0:21:f9:f6:07:c2"
_AGENT = "ali.brahim@a2ict.com"


def _legacy_line(message: str = "message") -> str:
    """Une ligne telle qu'écrite AVANT l'ajout du champ agent."""
    return (
        f"2026-07-14T08:00:00Z | BLOCK     | ok=True  | {_MAC:<17} | 48191327-Selma "
        f"| mode=full | source=payment | {message}\n"
    )


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Redirige le journal (et les preuves) vers un dossier temporaire."""
    path = tmp_path / "fai_actions.log"
    settings = fai_audit.get_settings()
    monkeypatch.setattr(settings, "fai_log_path", str(path), raising=False)
    monkeypatch.setattr(settings, "fai_evidence_dir", str(tmp_path / "evidence"), raising=False)
    return path


# ── Écriture ────────────────────────────────────────────────────────────────

def test_l_agent_est_ecrit_puis_relu(journal):
    fai_audit.log_action(
        "BLOCK", ok=True, mac=_MAC, name="48191327-Selma", mode="full",
        user=_AGENT, message="Client bloqué.",
    )

    assert "user=" + _AGENT in journal.read_text(encoding="utf-8")
    entries, _ = fai_audit.read_entries()
    assert entries[0]["user"] == _AGENT
    assert entries[0]["message"] == "Client bloqué."


def test_sans_agent_le_champ_vaut_null(journal):
    """Un appelant qui ne transmet rien (job d'enforcement, script) reste valide."""
    fai_audit.log_action(
        "RETRY_OK", ok=True, mac=_MAC, name="48191327-Selma",
        mode="full", source="enforce", message="Blocage en attente appliqué.",
    )

    entries, _ = fai_audit.read_entries()
    assert entries[0]["user"] is None


def test_un_pipe_dans_l_agent_ne_decale_pas_les_colonnes(journal):
    """L'agent vient d'un système tiers : il ne doit pas pouvoir casser le format."""
    fai_audit.log_action(
        "BLOCK", ok=True, mac=_MAC, name="48191327-Selma", mode="full",
        user="a | b\nc", message="Client bloqué.",
    )

    entries, _ = fai_audit.read_entries()
    assert entries[0]["user"] == "a / b c"
    assert entries[0]["message"] == "Client bloqué."  # le message reste intact


# ── Relecture de l'historique déjà écrit ────────────────────────────────────

def test_les_lignes_anterieures_restent_lisibles(journal):
    journal.write_text(_legacy_line(), encoding="utf-8")

    entries, stats = fai_audit.read_entries()

    assert stats["total"] == 1
    assert entries[0]["mac"] == _MAC
    assert entries[0]["message"] == "message"
    assert entries[0]["user"] is None


def test_un_ancien_message_avec_un_pipe_reste_entier(journal):
    """Le cas qui piège une détection par NOMBRE de champs.

    Une ligne d'avant produit elle aussi 9 morceaux quand son message contient un
    ` | ` — c'est la FORME du 8e champ, pas leur nombre, qui distingue les deux
    formats.
    """
    journal.write_text(_legacy_line("Blocage refusé | MAC attendue absente"), encoding="utf-8")

    entries, _ = fai_audit.read_entries()

    assert entries[0]["user"] is None
    assert entries[0]["message"] == "Blocage refusé | MAC attendue absente"


def test_les_deux_formats_cohabitent_dans_le_meme_fichier(journal):
    journal.write_text(_legacy_line(), encoding="utf-8")
    fai_audit.log_action(
        "UNBLOCK", ok=True, mac=_MAC, name="48191327-Selma",
        mode="full", user="auto retry", message="Accès rétabli.",
    )

    entries, stats = fai_audit.read_entries()

    assert stats["total"] == 2
    assert [e["user"] for e in entries] == ["auto retry", None]  # plus récent en tête


# ── Recherche ───────────────────────────────────────────────────────────────

def test_la_recherche_porte_aussi_sur_l_agent(journal):
    """« Toutes les coupures ordonnées par untel » : la question d'une piste d'audit."""
    for user, mac in ((_AGENT, _MAC), ("auto system", "aa:bb:cc:dd:ee:ff")):
        fai_audit.log_action(
            "BLOCK", ok=True, mac=mac, name="client", mode="full",
            user=user, message="Client bloqué.",
        )

    entries, stats = fai_audit.read_entries(search="ali.brahim")

    assert [e["mac"] for e in entries] == [_MAC]
    assert stats["total"] == 2  # les compteurs décrivent le journal, pas la vue
