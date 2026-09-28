"""
Exporte le numéro WhatsApp de chaque client ACTIF, en CSV sur la sortie standard.

À lancer sur le SERVEUR (qui a la base mais pas Internet). L'envoi des messages
se fait ensuite depuis un poste connecté avec `send_outage_whatsapp.py`, qui lit
ce CSV et ne touche jamais à la base.

« Actif » = la tuile « Accès actif » de /access (même règle que
`fn_access_clients`) :
  - LR **non bloqué** (`client_blocked=False`) — un client coupé pour impayé n'a
    pas à recevoir d'excuses pour une panne ;
  - **pas hors supervision** (`schemas.device.is_out_of_supervision`, importée,
    jamais recopiée).

⚠️ Aucune colonne téléphone en base : le numéro vit dans le NOM du LR
(« 44910449- Habib Khoumeini »). On retient un mobile mauritanien à 8 chiffres
commençant par 2, 3 ou 4 (préfixe 222 toléré). Un nom sans numéro valide est
listé sur stderr, jamais deviné. Un même numéro (client à plusieurs services)
n'apparaît qu'une fois.

Le CSV part sur stdout, le résumé sur stderr — on peut donc rediriger :
    dc exec -T backend python scripts/export_active_client_phones.py > clients_actifs.csv
"""

import asyncio
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.device import Lr
from app.schemas.device import is_out_of_supervision

# Numéro en tête du nom d'abord (convention « <tel>- <nom> »), sinon n'importe
# où — jamais au milieu d'une suite de chiffres plus longue.
_LEADING_RE = re.compile(r"^\s*(?:\+?222)?([234]\d{7})(?!\d)")
_ANYWHERE_RE = re.compile(r"(?<!\d)(?:\+?222)?([234]\d{7})(?!\d)")


def extract_phone(name: str | None) -> str | None:
    """Numéro local à 8 chiffres lu dans le nom du LR, ou None."""
    if not name:
        return None
    m = _LEADING_RE.match(name) or _ANYWHERE_RE.search(name)
    return m.group(1) if m else None


async def main() -> int:
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(Lr.id, Lr.name, Lr.ip_address, Lr.uisp_last_seen, Lr.location)
                .where(Lr.client_blocked.is_(False))
                .order_by(Lr.id)
            )
        ).all()

    active = [r for r in rows if not is_out_of_supervision(r.ip_address, r.uisp_last_seen)]

    writer = csv.writer(sys.stdout)
    writer.writerow(["phone", "name", "site"])
    seen: set[str] = set()
    no_phone: list[str] = []
    for r in active:
        phone = extract_phone(r.name)
        if phone is None:
            no_phone.append(r.name or f"#{r.id}")
            continue
        if phone in seen:
            continue
        seen.add(phone)
        writer.writerow([phone, r.name, r.location or ""])

    err = sys.stderr
    print(f"Clients actifs            : {len(active)}", file=err)
    print(f"Numéros distincts exportés: {len(seen)}", file=err)
    print(f"Sans numéro dans le nom   : {len(no_phone)}", file=err)
    for name in no_phone:
        print(f"  - {name}", file=err)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
