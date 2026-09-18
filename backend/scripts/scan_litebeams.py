#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Site survey sur TOUTES les LiteBeam d'un Rocket airMAX.

Ce que fait le script, d'un bout a l'autre :
  1. SSH sur le Rocket (l'AP) et lit sa liste de stations (wstalist).
  2. Ne garde que les LiteBeam (remote.platform contient "LiteBeam").
  3. Sur CHAQUE LiteBeam, declenche le site survey via l'API web airOS
     (login /api/auth -> /survey.json.cgi) et attend la fin du balayage.
  4. Ecrit le resultat agrege : resultat_scan.csv + resultat_scan.json.

/!\\ Chaque survey coupe brievement l'abonne (~10-40 s, il revient seul).
    Le script attend le retablissement (TCP 443) avant de passer au suivant.

Dependances : Python 3 + paramiko UNIQUEMENT (le reste est de la stdlib).
Se lance donc tel quel dans le conteneur backend, sur le serveur, ou sous Windows.

Exemples :
    # dans le conteneur backend sur le serveur :
    dc exec backend python scripts/scan_litebeams.py
    dc exec backend python scripts/scan_litebeams.py 10.135.81.2
    dc exec backend python scripts/scan_litebeams.py 10.135.25.1 --only 10.135.7.197

    # directement (poste avec paramiko installe) :
    python scan_litebeams.py

Il faut etre sur le reseau 10.135.x (le serveur l'est). Les fichiers de sortie
sont ecrits dans le repertoire de travail courant.
"""
import sys
import os
import json
import time
import socket
import ssl
import csv
import tempfile
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
from collections import defaultdict

import paramiko

# --- Reglages ---
ROCKET_DEFAULT = "10.135.25.1"
SSH_USER = "ubnt"
# Mots de passe SSH essayes sur le ROCKET (le 1er qui marche est retenu).
ROCKET_PWDS = ["A2AT1@4321$A2", "A2HQ@87654321", "A2HQ@4321", "ubnt"]
# Login de l'API web des LiteBeam.
LB_WEB_USER = "ubnt"
LB_WEB_PWDS = ["A2HQ@87654321", "A2HQ@4321", "ubnt"]
IFACE = "ath0"

POLL_MAX = 60          # nb max de sondages (x POLL_EVERY) => ~2 min par LiteBeam
POLL_EVERY = 2.0
RECOVER_MAX = 60       # attente retablissement (x2 s) avant l'equipement suivant

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Ne suit pas les 3xx : on veut lire le 302 (et son en-tete X-CSRF-ID)."""
    def redirect_request(self, *args, **kwargs):
        return None


def host_up(ip, port=443, timeout=2):
    """Retablissement = le port web (443) de la radio repond de nouveau."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_recover(ip):
    ok = 0
    for _ in range(RECOVER_MAX):
        ok = ok + 1 if host_up(ip) else 0
        if ok >= 2:
            return True
        time.sleep(2)
    return False


def list_litebeams(rocket_ip):
    """SSH sur le Rocket, retourne la liste des LiteBeam [{ip, mac, host, platform}]."""
    last_err = None
    for pw in ROCKET_PWDS:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            c.connect(rocket_ip, 22, SSH_USER, pw, timeout=20,
                      banner_timeout=30, auth_timeout=30,
                      look_for_keys=False, allow_agent=False)
        except paramiko.AuthenticationException:
            last_err = f"auth refusee ({pw})"
            c.close()
            continue
        except Exception as ex:  # noqa: BLE001
            last_err = f"{type(ex).__name__}: {ex}"
            c.close()
            continue
        try:
            _, out, _ = c.exec_command("wstalist", timeout=40)
            raw = out.read().decode("utf-8", "replace")
        finally:
            c.close()
        stas = json.loads(raw[raw.find("["):])
        lbs = []
        for s in stas:
            r = s.get("remote") or {}
            platform = r.get("platform") or ""
            low = platform.lower()
            if "litebeam" not in low and not low.startswith("lbe"):
                continue
            ip = r.get("ipaddr") or [s.get("lastip")]
            ip = ip[0] if isinstance(ip, list) and ip else s.get("lastip")
            lbs.append({"ip": ip, "mac": s.get("mac"),
                        "host": r.get("hostname", ""), "platform": platform})
        return lbs
    raise SystemExit(f"Impossible de se connecter au Rocket {rocket_ip} : {last_err}")


def _web_login(opener, base):
    """Login /api/auth ; retourne le jeton X-CSRF-ID (ou None si echec)."""
    for pw in LB_WEB_PWDS:
        data = urllib.parse.urlencode(
            {"username": LB_WEB_USER, "password": pw}).encode()
        req = urllib.request.Request(base + "/api/auth", data=data, method="POST")
        try:
            resp = opener.open(req, timeout=18)
            code, headers = resp.status, resp.headers
        except urllib.error.HTTPError as e:      # 302 arrive ici (NoRedirect)
            code, headers = e.code, e.headers
        except Exception:                        # noqa: BLE001
            continue
        if code in (200, 302):
            csrf = headers.get("X-CSRF-ID")
            return csrf if csrf is not None else ""  # "" = connecte sans csrf
    return None


def _survey_get(opener, base, csrf, update):
    url = base + "/survey.json.cgi?" + urllib.parse.urlencode(
        {"iface": IFACE, "update": update})
    headers = {"X-CSRF-ID": csrf} if csrf else {}
    req = urllib.request.Request(url, headers=headers)
    resp = opener.open(req, timeout=12)
    return json.loads(resp.read().decode("utf-8", "replace"))


def survey_one(ip):
    """Declenche + lit le site survey d'une LiteBeam. -> {ip, ok, error, aps:[...]}"""
    base = f"https://{ip}"
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=_SSL),
        urllib.request.HTTPCookieProcessor(cj),
    )
    res = {"ip": ip, "ok": False, "error": None, "aps": []}

    csrf = _web_login(opener, base)
    if csrf is None:
        res["error"] = "login refuse"
        return res

    # Declenche un nouveau balayage (parametre update vide).
    try:
        _survey_get(opener, base, csrf, "")
    except Exception:  # noqa: BLE001
        pass  # la coupure interrompt souvent la reponse ; le scan tourne cote device

    best = None
    seen_scanning = False
    for _ in range(POLL_MAX):
        time.sleep(POLL_EVERY)
        try:
            j = _survey_get(opener, base, csrf, "last")
            st = j.get("scan_status")
            data = j.get("scan_data", [])
            if st == "scanning":
                seen_scanning = True
            if data and (best is None or len(data) >= len(best)):
                best = data
            if st == "completed" and seen_scanning:
                res["ok"] = True
                res["aps"] = data or best or []
                return res
        except Exception:  # noqa: BLE001
            new = _web_login(opener, base)  # lien off-channel : on se reconnecte
            if new is not None:
                csrf = new

    res["ok"] = best is not None
    res["aps"] = best or []
    if not res["ok"] and not res["error"]:
        res["error"] = "timeout scan"
    return res


def _writable(d):
    try:
        t = os.path.join(d, ".wtest")
        with open(t, "w"):
            pass
        os.remove(t)
        return True
    except OSError:
        return False


def pick_out_dir(preferred=None):
    """Dossier de sortie inscriptible : --out si donne, sinon CWD, sinon /tmp.

    Le conteneur backend tourne en utilisateur non-root et /app ne lui est pas
    ouvert en ecriture -> repli automatique sur le repertoire temporaire.
    """
    for d in (preferred, os.getcwd(), tempfile.gettempdir()):
        if d and _writable(d):
            return d
    return tempfile.gettempdir()


def main():
    args = list(sys.argv[1:])
    rocket = ROCKET_DEFAULT
    only = set()
    out_pref = None
    if args and not args[0].startswith("--"):
        rocket = args.pop(0)
    if "--only" in args:
        i = args.index("--only")
        only = set(a for a in args[i + 1:] if not a.startswith("--"))
    if "--out" in args:
        i = args.index("--out")
        if i + 1 < len(args):
            out_pref = args[i + 1]

    print(f"== Rocket {rocket} : recherche des LiteBeam ==", flush=True)
    lbs = list_litebeams(rocket)
    if only:
        lbs = [x for x in lbs if x["ip"] in only]
    print(f"{len(lbs)} LiteBeam a scanner\n", flush=True)

    results = []
    for i, x in enumerate(lbs, 1):
        ip, host = x["ip"], x.get("host", "")
        print(f"[{i}/{len(lbs)}] {ip}  {host}", flush=True)
        r = survey_one(ip)
        r["host"] = host
        r["mac"] = x.get("mac")
        tag = "OK" if r["ok"] else f"ECHEC ({r['error']})"
        print(f"      -> {tag}, {len(r['aps'])} AP(s) vus", flush=True)
        for a in r["aps"]:
            print(f"         {a.get('essid'):18} {a.get('mac')}  "
                  f"{a.get('frequency')}GHz ch{a.get('channel')} "
                  f"sig={a.get('signal_level')} noise={a.get('noise_level')}",
                  flush=True)
        results.append(r)
        if i < len(lbs):
            back = wait_recover(ip)
            print(f"      (retabli: {'oui' if back else 'NON - a verifier'})\n",
                  flush=True)

    # --- ecriture des resultats (dans un dossier inscriptible) ---
    out_dir = pick_out_dir(out_pref)
    json_path = os.path.join(out_dir, "resultat_scan.json")
    csv_path = os.path.join(out_dir, "resultat_scan.csv")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, ensure_ascii=False)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ip", "abonne", "essid_vu", "bssid", "freq_ghz", "canal",
                    "signal_dbm", "noise_dbm", "snr_db"])
        for r in results:
            for a in (r["aps"] or [{}]):
                sig, noise = a.get("signal_level"), a.get("noise_level")
                try:
                    snr = int(sig) - int(noise)
                except (TypeError, ValueError):
                    snr = ""
                w.writerow([r["ip"], r["host"], a.get("essid", ""),
                            a.get("mac", ""), a.get("frequency", ""),
                            a.get("channel", ""),
                            sig if sig is not None else "",
                            noise if noise is not None else "", snr])

    ok = sum(1 for r in results if r["ok"])
    print("\n" + "=" * 55, flush=True)
    print(f"SYNTHESE : {ok}/{len(results)} scans reussis", flush=True)
    seen = defaultdict(int)
    for r in results:
        for a in r["aps"]:
            seen[a.get("essid")] += 1
    for essid, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"  {essid:20} vu par {n} LiteBeam", flush=True)
    print(f"\nFichiers ecrits :\n  {csv_path}\n  {json_path}", flush=True)


if __name__ == "__main__":
    main()
