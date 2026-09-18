"""
nginx ne relaie un changement de protocole au frontend que pour un WebSocket.

`location /` posait `Connection: upgrade` sur TOUTES les requêtes et relayait
l'en-tête `Upgrade` du client tel quel. Un client HTTP/1.1 qui propose
`Upgrade: h2c` — ce que font des outils de surveillance — transmettait donc au
serveur Next une demande de changement de protocole qu'il ne sait pas servir :
la requête restait suspendue jusqu'à l'abandon du client.

Mesuré le 2026-09-15 : 30 s avec l'en-tête, 0,03 s sans ; 233 appels « GET // »
par heure depuis deux sondes internes, tous en 499 après ~20 s — elles voyaient
le superviseur EN PANNE alors qu'un navigateur (HTTP/2, sans cet en-tête)
n'avait aucun problème. Rien d'autre que ce test ne signalerait un retour en
arrière : la page reste parfaitement fonctionnelle dans un navigateur.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONF = REPO_ROOT / "nginx" / "nginx.conf"


def _conf() -> str:
    if not CONF.exists():
        pytest.skip("nginx.conf hors de portée (tests lancés dans le conteneur backend)")
    return CONF.read_text(encoding="utf-8")


def _frontend_location(conf: str) -> str:
    blocks = re.findall(r"\n\s*location / \{(.*?)\n\s*\}", conf, re.S)
    frontend = [b for b in blocks if "frontend" in b]
    assert len(frontend) == 1, "location / vers le frontend introuvable (ou en double)"
    return frontend[0]


def _map(conf: str, name: str) -> str:
    m = re.search(r"map \$http_upgrade \$" + name + r" \{(.*?)\}", conf, re.S)
    assert m, f"map $http_upgrade ${name} introuvable"
    return m.group(1)


def test_la_route_frontend_ne_force_plus_connection_upgrade():
    block = _frontend_location(_conf())

    assert not re.search(r'proxy_set_header\s+Connection\s+"?upgrade"?\s*;', block)
    assert not re.search(r"proxy_set_header\s+Upgrade\s+\$http_upgrade\s*;", block)
    assert re.search(r"proxy_set_header\s+Upgrade\s+\$ws_upgrade\s*;", block)
    assert re.search(r"proxy_set_header\s+Connection\s+\$ws_connection\s*;", block)


def test_seul_un_websocket_declenche_le_changement_de_protocole():
    conf = _conf()
    for name in ("ws_upgrade", "ws_connection"):
        body = _map(conf, name)
        # Une seule entrée non vide, et elle ne vise que websocket (donc pas h2c).
        assert re.search(r"~\*\^websocket\$", body), name
        assert re.search(r'default\s+""\s*;', body), name
        assert "h2c" not in body, name
