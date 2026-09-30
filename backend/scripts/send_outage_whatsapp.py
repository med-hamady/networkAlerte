"""
Envoie le message WhatsApp de PANNE à chaque numéro du CSV exporté.

À lancer depuis un POSTE CONNECTÉ À INTERNET (le serveur de prod n'en a pas).
Autonome : bibliothèque standard Python seulement, ne touche jamais à la base.
Le CSV vient de `export_active_client_phones.py`, lancé sur le serveur.

Identifiants Ultramsg (ceux du .env du serveur, `WHATSAPP_INSTANCE_ID` /
`WHATSAPP_TOKEN`) passés par variables d'environnement, jamais en dur :
    $env:ULTRAMSG_INSTANCE = "instance12345"
    $env:ULTRAMSG_TOKEN    = "xxxxxxxx"

⚠️ Envoi individuel en masse depuis le numéro de l'entreprise : WhatsApp bannit
les rafales. Un message à la fois, espacés de `--delay` secondes (défaut 4 s →
~1 h pour 900 clients). Ne pas descendre sous 2-3 s.

⚠️ Reprise : chaque envoi RÉUSSI est écrit dans `--log`. Relancer la même
commande saute les numéros déjà servis et retente les échecs — une interruption
ne fait jamais envoyer deux fois le même message.

Usage (PowerShell, depuis backend/) :
    python scripts/send_outage_whatsapp.py clients_actifs.csv                          # DRY-RUN
    python scripts/send_outage_whatsapp.py clients_actifs.csv --test-number 4XXXXXXX   # 1 message de contrôle
    python scripts/send_outage_whatsapp.py clients_actifs.csv --send --limit 20        # premier lot
    python scripts/send_outage_whatsapp.py clients_actifs.csv --send                   # tout le reste
"""

import argparse
import contextlib
import csv
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MESSAGE = (
    "Chers clients, nous nous excusons pour la coupure d’Internet. L’incident est "
    "indépendant de notre volonté et nos équipes travaillent activement pour "
    "rétablir le service dans les meilleurs délais. Merci pour votre patience et "
    "votre compréhension.\n"
    "\n"
    "عملاءنا الكرام، نعتذر عن انقطاع خدمة الإنترنت بشكل عام. العطل خارج عن إرادتنا، "
    "وفرقنا تعمل جاهدة على إعادة الخدمة في أقرب وقت ممكن. نشكركم على صبركم وتفهمكم."
)

COUNTRY_CODE = "222"
BASE_URL = os.environ.get("ULTRAMSG_BASE_URL", "https://api.ultramsg.com")
_TIMEOUT_S = 15


def _normalize(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith(COUNTRY_CODE) and len(digits) == 11:
        digits = digits[3:]
    return digits if re.fullmatch(r"[234]\d{7}", digits) else None


def send_one(instance: str, token: str, phone: str, text: str) -> tuple[bool, str]:
    url = f"{BASE_URL.rstrip('/')}/{instance}/messages/chat"
    body = urllib.parse.urlencode(
        {"token": token, "to": f"+{COUNTRY_CODE}{phone}", "body": text}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            # Cloudflare (devant Ultramsg) rejette le User-Agent par défaut
            # « Python-urllib » : 403 « error code: 1010 ».
            "User-Agent": "a2-network-supervisor/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}: {exc.read()[:150]!r}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"réseau: {exc}"
    try:
        data = json.loads(text)
    except ValueError:
        return False, f"réponse non JSON: {text[:150]}"
    # Ultramsg répond 200 même en cas d'échec : on lit le corps.
    if data.get("sent") in (True, "true", "True") or data.get("message") == "ok":
        return True, str(data.get("id", "ok"))
    return False, str(data)[:150]


def load_csv(path: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    # utf-8-sig : tolère un BOM si le fichier est passé par un éditeur Windows.
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            phone = _normalize(row.get("phone", ""))
            if phone and phone not in seen:
                seen.add(phone)
                out.append((phone, row.get("name", "")))
    return out


def already_sent(log_path: str) -> set[str]:
    if not os.path.exists(log_path):
        return set()
    done: set[str] = set()
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and parts[2] == "OK":
                done.add(parts[1])
    return done


def main() -> int:
    # La console Windows n'est pas en UTF-8 par défaut : un nom accentué ferait
    # planter un print au milieu de l'envoi.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("csv_path", help="CSV exporté par export_active_client_phones.py")
    parser.add_argument("--send", action="store_true", help="envoyer pour de bon (sinon dry-run)")
    parser.add_argument("--test-number", help="envoyer UN message à ce numéro seulement")
    parser.add_argument("--limit", type=int, help="ne traiter que les N premiers restants")
    parser.add_argument("--delay", type=float, default=4.0, help="secondes entre deux envois (défaut 4)")
    parser.add_argument(
        "--message-file",
        help="texte à envoyer (UTF-8) ; défaut : le message de panne intégré",
    )
    parser.add_argument(
        "--log",
        help="journal des envois (sert à la reprise) ; défaut : dérivé du texte du message",
    )
    args = parser.parse_args()

    if args.message_file:
        with open(args.message_file, encoding="utf-8-sig") as fh:
            text = fh.read().strip()
    else:
        text = MESSAGE
    if not text:
        print("ERREUR : message vide.")
        return 2
    # Un journal PAR MESSAGE : sinon une 2e campagne (« service rétabli »)
    # sauterait tous les numéros déjà servis par la 1re (« panne »).
    if not args.log:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        args.log = f"outage_notice_{digest}.log"

    instance = os.environ.get("ULTRAMSG_INSTANCE", "").strip()
    token = os.environ.get("ULTRAMSG_TOKEN", "").strip()
    if (args.send or args.test_number) and not (instance and token):
        print("ERREUR : définir ULTRAMSG_INSTANCE et ULTRAMSG_TOKEN (valeurs du .env du serveur).")
        return 2

    if args.test_number:
        phone = _normalize(args.test_number)
        if phone is None:
            print(f"Numéro invalide : {args.test_number}")
            return 2
        ok, detail = send_one(instance, token, phone, text)
        print(f"{'OK' if ok else 'ÉCHEC'} +{COUNTRY_CODE}{phone} — {detail}")
        return 0 if ok else 1

    recipients = load_csv(args.csv_path)
    done = already_sent(args.log)
    todo = [(p, n) for p, n in recipients if p not in done]
    if args.limit is not None:
        todo = todo[: args.limit]

    print(f"Numéros dans le CSV   : {len(recipients)}")
    print(f"Déjà servis (journal) : {len(done & {p for p, _ in recipients})}")
    print(f"À envoyer maintenant  : {len(todo)}  (~{len(todo) * args.delay / 60:.0f} min)")
    print(f"Journal               : {args.log}")
    print("\nMessage :\n" + text)

    if not args.send:
        print("\nDRY-RUN — aperçu :")
        for phone, name in todo[:30]:
            print(f"  +{COUNTRY_CODE}{phone}  {name}")
        if len(todo) > 30:
            print(f"  … et {len(todo) - 30} autres")
        print("\nRien n'a été envoyé. Relancer avec --send.")
        return 0

    sent = failed = 0
    with open(args.log, "a", encoding="utf-8") as log:
        try:
            for i, (phone, name) in enumerate(todo, 1):
                ok, detail = send_one(instance, token, phone, text)
                stamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
                log.write(f"{stamp}\t{phone}\t{'OK' if ok else 'KO'}\t{name}\t{detail}\n")
                log.flush()
                sent += ok
                failed += not ok
                print(
                    f"[{i}/{len(todo)}] {'OK' if ok else 'KO'} +{COUNTRY_CODE}{phone}  {name}"
                    + ("" if ok else f"  — {detail}")
                )
                if i < len(todo):
                    time.sleep(args.delay)
        except KeyboardInterrupt:
            print("\nInterrompu — relancer la même commande reprend là où on s'est arrêté.")

    print(f"\nTerminé : {sent} envoyé(s), {failed} échec(s). Journal : {os.path.abspath(args.log)}")
    if failed:
        print("Relancer la même commande pour retenter les échecs (les OK sont sautés).")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
