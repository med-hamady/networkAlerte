"""FAI — contrôle pré-vol d'un LR par MAC (lecture seule) pour un système tiers.

Route séparée du reste de `/fai` (block / unblock / status) pour une raison
d'**autorisation** : elle est appelée par un consommateur DIFFÉRENT du système de
paiement — un système qui interroge l'état d'un LR et lit le verdict. Il tient sa
propre clé (`lr_verify_api_key`, dépendance `require_verify_client`), scellée à
cette seule route ; les routes d'action gardent la clé paiement `fai_api_key`.

C'est pourquoi le router est distinct de `fai.py` : une dépendance au niveau du
router est additive et ne peut pas être surchargée par route, donc la seule façon
de donner à `/fai/verify` une auth propre est de la sortir dans son propre router
(monté sous le même préfixe `/fai` dans `api/router.py`).

Tout est lu en base (colonnes rafraîchies par les sondes) — cette route ne touche
jamais au LR.
"""

from __future__ import annotations

import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.services import client_block_service, ssh_service

router = APIRouter()


class FaiVerifyChecks(BaseModel):
    """Détail de chaque contrôle (True = conforme)."""

    # Un LR existe en base pour cette MAC.
    exists: bool
    # `ssh_status == "ok"` : la dernière sonde a ouvert une session SSH.
    ssh_active: bool
    # SSH actif ET le mot de passe stocké sur la fiche est le standard attendu
    # (donc c'est bien celui qui ouvre la session).
    password_valid: bool
    # `topology_mode == "router"` : le LR est en mode routeur.
    router_mode: bool


class FaiVerifyResult(BaseModel):
    ok: bool
    status: Literal["OK", "KO"]
    mac: str | None
    # Nom du LR quand il existe (demandé pour identification côté appelant), None sinon.
    name: str | None
    # Résumé lisible des contrôles en échec ; None quand tout est conforme.
    reason: str | None
    checks: FaiVerifyChecks
    # Valeurs brutes utiles pour comprendre un KO (contexte, jamais None si le LR
    # existe) : statut SSH de la dernière sonde et mode topologique détecté.
    ssh_status: str | None
    topology_mode: str | None
    # Fraîcheur : quand la dernière sonde SSH a écrit `ssh_status`.
    ssh_checked_at: datetime.datetime | None


# Messages de KO par statut SSH (lus de `lrs.ssh_status`, rempli par la sonde).
_SSH_STATUS_REASONS: dict[str | None, str] = {
    ssh_service.SSH_STATUS_AUTH_FAILED: "SSH : authentification refusée (mot de passe rejeté)",
    ssh_service.SSH_STATUS_DISABLED: "SSH désactivé sur le LR (connexion refusée)",
    ssh_service.SSH_STATUS_HOST_KEY_MISMATCH: "SSH : clé d'hôte incompatible",
    ssh_service.SSH_STATUS_UNREACHABLE: "LR injoignable en SSH (hors ligne / muet)",
    None: "SSH jamais testé (LR hors ligne — aucune sonde)",
}


@router.get("/verify", response_model=FaiVerifyResult)
async def fai_verify(
    mac: str = Query(
        ...,
        description="MAC du LR client (formats acceptés : aa:bb:cc:dd:ee:ff, "
        "aa-bb-..., aabb.ccdd.eeff, aabbccddeeff)",
    ),
    db: AsyncSession = Depends(get_db),
) -> FaiVerifyResult:
    """Contrôle pré-vol d'un LR par MAC, pour un système tiers (lecture seule).

    Vérifie, sans toucher au LR (tout est lu en base, rafraîchi par les sondes) :

    1. le LR **existe** (sinon `KO`, `name` = None) ;
    2. **SSH actif** (`ssh_status == "ok"`) ;
    3. **mot de passe standard** : la fiche porte le mot de passe attendu
       (`fai_expected_lr_ssh_password`) ET la sonde a authentifié — l'auto-repair
       promeut le mot de passe qui marche, donc les deux ensemble prouvent qu'il
       ouvre bien la session ;
    4. **mode routeur** (`topology_mode == "router"`).

    Tout conforme → `ok=True`, `status="OK"`, `name` renseigné. Sinon `status="KO"`
    et `reason` liste les contrôles en échec. `checks` porte le détail par contrôle.

    - 400 : MAC mal formée.
    - 200 « KO » (pas 404) : aucun LR pour cette MAC — l'existence EST un contrôle.
    """
    try:
        lr = await client_block_service.find_lr_by_mac(db, mac)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if lr is None:
        return FaiVerifyResult(
            ok=False,
            status="KO",
            mac=mac,
            name=None,
            reason="Aucun LR en base pour cette MAC",
            checks=FaiVerifyChecks(
                exists=False, ssh_active=False, password_valid=False, router_mode=False
            ),
            ssh_status=None,
            topology_mode=None,
            ssh_checked_at=None,
        )

    settings = get_settings()
    ssh_active = lr.ssh_status == ssh_service.SSH_STATUS_OK
    password_valid = ssh_active and lr.ssh_password == settings.fai_expected_lr_ssh_password
    router_mode = lr.topology_mode == "router"

    reasons: list[str] = []
    if not ssh_active:
        reasons.append(_SSH_STATUS_REASONS.get(lr.ssh_status, "SSH inactif"))
    elif not password_valid:
        # SSH ouvre bien, mais avec un autre mot de passe que le standard attendu.
        reasons.append("Le mot de passe SSH n'est pas le mot de passe standard attendu")
    if not router_mode:
        reasons.append(f"Le LR n'est pas en mode routeur (mode actuel : {lr.topology_mode})")

    ok = ssh_active and password_valid and router_mode
    return FaiVerifyResult(
        ok=ok,
        status="OK" if ok else "KO",
        mac=lr.mac_address,
        name=lr.name,
        reason=None if ok else " ; ".join(reasons),
        checks=FaiVerifyChecks(
            exists=True,
            ssh_active=ssh_active,
            password_valid=password_valid,
            router_mode=router_mode,
        ),
        ssh_status=lr.ssh_status,
        topology_mode=lr.topology_mode,
        ssh_checked_at=lr.ssh_checked_at,
    )
