"""
Bootstrap an admin user — run once on the production server.

Usage (interactive, from a host shell):
    docker compose -f docker-compose.yml -f docker-compose.prod.yml \\
        exec backend python scripts/create_admin.py

The script prompts for username + full name + password (typed twice) and
inserts a single row in `users`. It refuses to overwrite an existing
account with the same username — for password resets, use the matching
helper `python scripts/reset_password.py <username>` (out of scope here).

Passwords are bcrypt-hashed before being written.
"""

from __future__ import annotations

import asyncio
import getpass
import sys

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models.user import User
from app.services.auth_service import hash_password


def _read_non_empty(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("  → valeur vide, recommence.", file=sys.stderr)


def _read_password() -> str:
    while True:
        pw1 = getpass.getpass("Mot de passe (au moins 8 caractères) : ")
        if len(pw1) < 8:
            print("  → trop court, recommence.", file=sys.stderr)
            continue
        pw2 = getpass.getpass("Confirme le mot de passe : ")
        if pw1 != pw2:
            print("  → mots de passe différents, recommence.", file=sys.stderr)
            continue
        return pw1


async def main() -> int:
    print("=== Création d'un compte administrateur ===")
    username = _read_non_empty("Nom d'utilisateur : ").lower()
    full_name = input("Nom complet (optionnel) : ").strip() or None
    password = _read_password()

    async with async_session_factory() as session:
        existing = await session.execute(select(User).where(User.username == username))
        if existing.scalar_one_or_none() is not None:
            print(
                f"\n✗ Un utilisateur '{username}' existe déjà.\n"
                "  Choisis un autre nom, ou utilise un script de réinitialisation.",
                file=sys.stderr,
            )
            return 1

        user = User(
            username=username,
            password_hash=hash_password(password),
            full_name=full_name,
            enabled=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    print(f"\n✓ Compte administrateur '{user.username}' créé (id={user.id}).")
    print("  Tu peux maintenant te connecter sur le dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
