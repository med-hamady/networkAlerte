"""FAI — contrôle pré-vol LIVE d'un LR par MAC (SSH temps réel) pour un tiers.

Route séparée du reste de `/fai` (block / unblock / status) pour une raison
d'**autorisation** : elle est appelée par un consommateur DIFFÉRENT du système de
paiement — un système qui interroge l'état d'un LR et lit le verdict. Il tient sa
propre clé (`lr_verify_api_key`, dépendance `require_verify_client`), scellée à
cette seule route ; les routes d'action gardent la clé paiement `fai_api_key`.

C'est pourquoi le router est distinct de `fai.py` : une dépendance au niveau du
router est additive et ne peut pas être surchargée par route, donc la seule façon
de donner à `/fai/verify` une auth propre est de la sortir dans son propre router
(monté sous le même préfixe `/fai` dans `api/router.py`).

⚠️ **Contrôle LIVE** : au moment de l'appel, on ouvre une **vraie session SSH** sur
le LR avec le mot de passe standard attendu et on lit son mode réseau — on NE lit
PAS les colonnes `ssh_status` / `topology_mode` remplies par les sondes. La seule
lecture en base est la résolution MAC → équipement (IP, nom, creds) : sans elle on
ne sait pas *quel* équipement ni *où* le joindre. Conséquence assumée : un LR
éteint / SSH injoignable au moment de l'appel ressort en `KO` (ssh_active=false),
et l'appel prend le temps d'une poignée de main SSH (quelques secondes).
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
    # La poignée de main SSH a abouti au moment de l'appel (daemon SSH répond).
    ssh_active: bool
    # Session SSH ouverte EN DIRECT avec le mot de passe standard attendu.
    password_valid: bool
    # `netmode == "router"` lu en direct dans system.cfg sur la session.
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
    # Valeurs brutes issues du test LIVE : catégorie SSH constatée et mode réseau lu.
    ssh_status: str | None
    topology_mode: str | None
    # Instant du test live (l'appel EST la mesure — pas une valeur de sonde stockée).
    ssh_checked_at: datetime.datetime | None


# Message de KO quand le SSH n'aboutit pas / le mot de passe est rejeté, par
# catégorie retournée par `ssh_service.verify_lr_live`.
_SSH_STATUS_REASONS: dict[str, str] = {
    ssh_service.SSH_STATUS_AUTH_FAILED: "SSH : mot de passe standard rejeté (≠ attendu)",
    ssh_service.SSH_STATUS_DISABLED: "SSH désactivé sur le LR (connexion refusée)",
    ssh_service.SSH_STATUS_HOST_KEY_MISMATCH: "SSH : clé d'hôte incompatible",
    ssh_service.SSH_STATUS_UNREACHABLE: "LR injoignable en SSH (hors ligne)",
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
    """Contrôle pré-vol LIVE d'un LR par MAC, pour un système tiers.

    Teste l'équipement **en direct par SSH** au moment de l'appel :

    1. le LR **existe** en base (résolution MAC → équipement ; sinon `KO`,
       `name` = None) ;
    2. **SSH actif** : la poignée de main SSH aboutit maintenant ;
    3. **mot de passe standard** : la session est ouverte avec le mot de passe
       attendu (`fai_expected_lr_ssh_password`) — succès = mot de passe valide ;
    4. **mode routeur** : `netmode` lu en direct dans `system.cfg` sur la session.

    Tout conforme → `ok=True`, `status="OK"`. Sinon `status="KO"` et `reason`
    liste les contrôles en échec. `checks` porte le détail par contrôle.

    - 400 : MAC mal formée.
    - 200 « KO » (pas 404) : aucun LR pour cette MAC — l'existence EST un contrôle.

    Un LR éteint / SSH injoignable ressort en `KO` (ssh_active=false) : un test
    live ne peut pas se prononcer sur un équipement qu'il ne joint pas.
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
    checked_at = datetime.datetime.now(datetime.UTC)

    if not lr.ip_address:
        # Pas d'IP → aucune cible SSH : injoignable, on ne peut rien tester live.
        return FaiVerifyResult(
            ok=False,
            status="KO",
            mac=lr.mac_address,
            name=lr.name,
            reason="LR sans adresse IP — injoignable en SSH",
            checks=FaiVerifyChecks(
                exists=True, ssh_active=False, password_valid=False, router_mode=False
            ),
            ssh_status=ssh_service.SSH_STATUS_UNREACHABLE,
            topology_mode=None,
            ssh_checked_at=checked_at,
        )

    ssh_active, password_valid, netmode, ssh_status, _message = (
        await ssh_service.verify_lr_live(
            host=lr.ip_address,
            port=lr.ssh_port or 22,
            username=lr.ssh_username or "ubnt",
            password=settings.fai_expected_lr_ssh_password,
            expected_fingerprint=lr.ssh_host_fingerprint,
            expected_mac=lr.mac_address,
        )
    )
    router_mode = netmode == "router"

    reasons: list[str] = []
    if not ssh_active:
        reasons.append(_SSH_STATUS_REASONS.get(ssh_status, "SSH inactif"))
    elif not password_valid:
        # SSH répond mais l'auth avec le mot de passe standard a échoué (ou host key).
        reasons.append(_SSH_STATUS_REASONS.get(ssh_status, "Mot de passe SSH invalide"))
    if not router_mode:
        reasons.append(
            f"Le LR n'est pas en mode routeur (mode : {netmode or 'inconnu'})"
        )

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
        ssh_status=ssh_status,
        topology_mode=netmode or "unknown",
        ssh_checked_at=checked_at,
    )
