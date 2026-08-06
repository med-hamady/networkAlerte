"""Le journal FAI est lu EN ENTIER, jamais sur une fenêtre de queue.

Ce module a longtemps borné la lecture aux 5000 dernières lignes. Une recherche
sur une MAC dont les lignes étaient plus anciennes répondait alors « aucun
événement » — un négatif FAUX dans une piste d'audit, c.-à-d. le pire mode de
défaillance possible pour ce fichier : on s'en sert pour répondre « ce client
a-t-il été coupé, et sur ordre de qui ? ».

Les tests écrivent un journal synthétique de plus de 5000 lignes et vérifient
qu'on retrouve bien ce qui est au tout début.
"""

import pytest

from app.services import fai_audit

# Au-delà de l'ancienne fenêtre : ce qui précède la 5000e ligne en partant de la
# fin était invisible. On dépasse franchement pour que le test ait un sens même
# si quelqu'un remonte un plafond quelque part.
_FILLER_LINES = 6000

_OLD_MAC = "6c:63:f8:d2:56:a6"
_RECENT_MAC = "d0:21:f9:fa:a3:2b"


def _line(ts: str, action: str, ok: bool, mac: str, name: str) -> str:
    return (
        f"{ts} | {action:<9} | ok={str(ok):<5} | {mac:<17} | {name} "
        f"| mode=full | source=payment | message\n"
    )


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Écrit un journal dont la 1re ligne est loin derrière l'ancienne fenêtre."""
    path = tmp_path / "fai_actions.log"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_line("2026-07-14T08:00:00Z", "BLOCK", True, _OLD_MAC, "48191327-Selma"))
        for i in range(_FILLER_LINES):
            fh.write(_line(
                f"2026-07-15T00:00:{i % 60:02d}Z", "BLOCK", True,
                f"aa:bb:cc:dd:{i // 256:02x}:{i % 256:02x}", f"remplissage-{i}",
            ))
        fh.write(_line("2026-08-04T10:00:00Z", "UNBLOCK", True, _RECENT_MAC, "46222284-Idrissa"))

    settings = fai_audit.get_settings()
    monkeypatch.setattr(settings, "fai_log_path", str(path), raising=False)
    monkeypatch.setattr(settings, "fai_evidence_dir", str(tmp_path / "evidence"), raising=False)
    return path


def test_recherche_trouve_une_entree_anterieure_a_la_fenetre(journal):
    """La régression d'origine : cette MAC n'est QUE dans la 1re ligne."""
    entries, _ = fai_audit.read_entries(search=_OLD_MAC)

    assert [e["mac"] for e in entries] == [_OLD_MAC]
    assert entries[0]["timestamp"] == "2026-07-14T08:00:00Z"


def test_les_compteurs_portent_sur_tout_le_fichier(journal):
    _, stats = fai_audit.read_entries()

    assert stats["total"] == _FILLER_LINES + 2
    assert stats["ok"] == _FILLER_LINES + 2
    assert stats["failed"] == 0


def test_limit_borne_le_rendu_mais_pas_le_scan(journal):
    """`limit` plafonne ce qui est RENDU ; les compteurs voient tout."""
    entries, stats = fai_audit.read_entries(limit=10)

    assert len(entries) == 10
    assert stats["total"] == _FILLER_LINES + 2
    # Les plus récentes d'abord — donc la dernière ligne du fichier en tête.
    assert entries[0]["mac"] == _RECENT_MAC


def test_filtre_action_traverse_aussi_tout_le_fichier(journal):
    entries, stats = fai_audit.read_entries(action="UNBLOCK")

    assert [e["mac"] for e in entries] == [_RECENT_MAC]
    # Le filtre ne doit pas amputer les compteurs.
    assert stats["total"] == _FILLER_LINES + 2


def test_journal_absent_ne_leve_pas(tmp_path, monkeypatch):
    settings = fai_audit.get_settings()
    monkeypatch.setattr(settings, "fai_log_path", str(tmp_path / "nexistepas.log"), raising=False)

    entries, stats = fai_audit.read_entries()

    assert entries == []
    assert stats == {"total": 0, "ok": 0, "failed": 0, "abandoned": 0}
