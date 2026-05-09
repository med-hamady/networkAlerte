# Déploiement sur Windows Server

Runbook de mise en production du Network Supervisor sur un serveur Windows
(Windows Server 2019 / 2022). Cible : Docker + nginx en reverse proxy TLS.

---

## 1. Prérequis serveur

| Composant | Version | Notes |
|---|---|---|
| OS | Windows Server 2019 ou 2022 | 2022 recommandé (WSL2 natif) |
| CPU / RAM | 2 vCPU / 4 Go min | 4 vCPU / 8 Go conseillé |
| Disque | 40 Go min | SSD recommandé pour PostgreSQL |
| Accès réseau | Sortant : registre Docker, Git ; Entrant : 80/443 | |
| Git for Windows | 2.40+ | Fournit `git`, `bash` et `openssl.exe` (utilisé par le script de génération de certificat) |
| Docker Compose | v2.24+ | La directive `!reset` du compose prod n'existe qu'à partir de cette version |

Comptes de service : un compte local non-administrateur dédié pour exécuter la
tâche planifiée (cf. §6).

⚠️ Si vous installez OpenSSL séparément (sans Git for Windows), assurez-vous
qu'il soit dans le `PATH` ou dans un des chemins standards
`C:\Program Files\Git\usr\bin\openssl.exe`,
`C:\Program Files\Git\mingw64\bin\openssl.exe`. Le script
`scripts/generate-self-signed-cert.ps1` les détecte automatiquement.

---

## 2. Installer Docker

### Option A — Docker Desktop (le plus simple)

1. Télécharger Docker Desktop pour Windows.
2. Installer en cochant **WSL 2 backend**.
3. Au premier démarrage, accepter l'installation automatique de WSL2.
4. Vérifier :
   ```powershell
   docker version
   docker compose version
   ```

⚠️ **Licence** : Docker Desktop est payant pour les organisations
> 250 employés ou > 10 M$ de CA. Voir Option B sinon.

### Option B — Docker CE via WSL2 (gratuit)

1. Activer WSL2 :
   ```powershell
   wsl --install -d Ubuntu-22.04
   ```
2. Dans la console Ubuntu :
   ```bash
   sudo apt update
   sudo apt install -y docker.io docker-compose-plugin
   sudo usermod -aG docker $USER
   sudo systemctl enable --now docker
   ```
3. Travailler depuis la console Ubuntu (les chemins Windows sont accessibles
   via `/mnt/c/...`).

### Option C — Mirantis Container Runtime

Alternative payante "officielle" pour Windows Server. Documentation Mirantis
si l'entreprise impose un support commercial.

---

## 3. Cloner et configurer le projet

Depuis PowerShell (Option A) ou la console WSL (Option B) :

```powershell
cd C:\Apps                       # ou un chemin de votre choix
git clone <url-du-repo> a2project
cd a2project
copy .env.example .env
```

Éditer `.env` — **valeurs critiques à remplacer**.

> ⚠️ **Garde anti-déploiement-bâclé** : avec `APP_ENV=production`, le backend
> refuse de démarrer si l'une de ces conditions est vraie. Le container reste
> en restart-loop tant que ce n'est pas corrigé.

| Variable | Valeur interdite | Action |
|---|---|---|
| `API_KEY` | vide | Générer 32 octets aléatoires |
| `POSTGRES_PASSWORD` | vide ou `supervisor_dev_password` | Mot de passe fort |
| `UISP_POWER_PASSWORD` | `ubnt` (défaut Ubiquiti) | Mot de passe réel du device |
| `LTU_API_PASSWORD` | `ubnt` (défaut Ubiquiti) | Mot de passe réel du device |
| `LTU_LR_SSH_PASSWORD` | vide si `TRANSIT_PROBE_ENABLED=true` | Mot de passe SSH du LTU LR (ou `TRANSIT_PROBE_ENABLED=false`) |

Générer une `API_KEY` :
```powershell
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
-join ($bytes | ForEach-Object { $_.ToString('x2') })
```

Exemple de `.env` minimal en prod :

```
APP_ENV=production
DEBUG=false
LOG_LEVEL=INFO

API_KEY=<sortie du snippet ci-dessus>
POSTGRES_PASSWORD=<mot de passe fort, 24+ caractères>

# Identifiants équipements (Ubiquiti, LTU LR SSH)
LTU_API_PASSWORD=<mot de passe réel>
LTU_LR_SSH_PASSWORD=<mot de passe réel>
UISP_POWER_PASSWORD=<mot de passe réel>

# Si vous n'avez pas de LTU LR ou pas d'accès SSH
# TRANSIT_PROBE_ENABLED=false

# Adapter selon vos canaux réels
SMTP_ENABLED=true
SMTP_HOST=...
NOTIFICATION_EMAILS=...
```

> ⚠️ **Si vous changez `POSTGRES_PASSWORD` après une première initialisation**,
> postgres garde l'ancien dans le volume `postgres_data`. Le backend ne pourra
> plus se connecter. Voir §10 *Dépannage* (« changement de mot de passe DB »).

---

## 4. Générer le certificat TLS

Réseau interne (self-signed) :

```powershell
.\scripts\generate-self-signed-cert.ps1 -CommonName supervisor.local
```

Ou si vous avez un certificat émis par la CA interne (PFX) :

```powershell
openssl pkcs12 -in cert.pfx -nocerts -nodes -out nginx\certs\privkey.pem
openssl pkcs12 -in cert.pfx -clcerts -nokeys -out nginx\certs\fullchain.pem
```

Voir [nginx/certs/README.md](../nginx/certs/README.md) pour les détails.

---

## 5. Ouvrir le pare-feu

```powershell
New-NetFirewallRule -DisplayName "Network Supervisor HTTP"  -Direction Inbound -Protocol TCP -LocalPort 80  -Action Allow
New-NetFirewallRule -DisplayName "Network Supervisor HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow
```

Si l'accès doit être restreint à un sous-réseau :

```powershell
New-NetFirewallRule -DisplayName "Network Supervisor HTTPS" `
  -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow `
  -RemoteAddress 10.0.0.0/24
```

---

## 6. Démarrer en mode production

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Le premier build prend 5–10 min (image frontend Next.js standalone).

Vérifier :

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f backend
curl.exe -k https://localhost/api/v1/health
```

Les 4 services doivent passer en `healthy` (le nginx peut prendre 15–30 s
de plus que les autres à cause de son `start_period`).

Le mode prod (`docker-compose.prod.yml`) :
- backend et frontend ne bindent **aucun** port host (`!reset` les vide). nginx leur parle via le réseau Docker interne
- postgres est exposé en `127.0.0.1:5433` (debug DB depuis l'host uniquement)
- nginx expose 80 (redirect 301 → 443) et 443 (TLS)
- volume bind mounts dev également retirés via `!reset` — l'image embarque le code
- limite mémoire à 512 Mo par container applicatif

---

## 7. Auto-restart au démarrage du serveur

Les containers ont `restart: unless-stopped`, mais Docker doit être démarré
**avant** que les containers reprennent. Deux configurations selon l'option choisie :

### Option A (Docker Desktop)

Docker Desktop ne démarre **pas** automatiquement comme service Windows par défaut.
Créer une tâche planifiée qui le lance :

```powershell
$action  = New-ScheduledTaskAction -Execute 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName "DockerDesktop" -Action $action -Trigger $trigger -Principal $principal
```

### Option B (Docker CE / WSL2)

Activer le démarrage automatique de la distro WSL et du daemon Docker :

```powershell
wsl --set-default Ubuntu-22.04
```

Dans WSL, créer `/etc/wsl.conf` :
```ini
[boot]
systemd=true
command="service docker start"
```

Puis activer le service WSL au démarrage Windows (déjà actif par défaut sur
Windows Server 2022).

### Vérifier la reprise

Redémarrer le serveur et vérifier après 2 minutes :
```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```
Tous les services doivent être `running (healthy)`.

---

## 8. Logs et supervision

### Suivre les logs en direct

```powershell
docker compose logs -f --tail=200 backend
docker compose logs -f --tail=200 nginx
```

### Rotation des logs

Déjà configurée dans `docker-compose.yml` (driver `json-file`, taille max
10–50 Mo, 3–5 fichiers). Les logs vivent dans
`%ProgramData%\Docker\containers\<id>\<id>-json.log`.

### Sauvegarde PostgreSQL

Sauvegarde quotidienne via tâche planifiée :

```powershell
# Créer le script de backup
$script = @'
$date = Get-Date -Format "yyyy-MM-dd"
docker compose -f C:\Apps\a2project\docker-compose.yml exec -T postgres `
  pg_dump -U supervisor network_supervisor | `
  Out-File -Encoding utf8 "C:\Backups\supervisor_$date.sql"
# Garder 14 jours
Get-ChildItem C:\Backups\supervisor_*.sql |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
  Remove-Item
'@
$script | Out-File C:\Apps\a2project\scripts\backup-db.ps1 -Encoding utf8

# Planifier 02:00 chaque jour
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\Apps\a2project\scripts\backup-db.ps1'
$trigger = New-ScheduledTaskTrigger -Daily -At 2am
Register-ScheduledTask -TaskName "SupervisorDbBackup" -Action $action -Trigger $trigger -RunLevel Highest
```

---

## 9. Mise à jour du code

```powershell
cd C:\Apps\a2project
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Les migrations Alembic sont exécutées automatiquement à chaque démarrage du
container backend (cf. `backend/Dockerfile` entrypoint).

---

## 10. Dépannage rapide

| Symptôme | Vérification |
|---|---|
| Page blanche / 502 | `docker compose ps` — un container est `unhealthy` ? Logs nginx : `docker compose logs nginx` |
| Erreur certificat | Le navigateur refuse le self-signed → importer `nginx/certs/fullchain.pem` dans le store Windows "Autorités racines de confiance" |
| Backend `Refusing to start in production with insecure configuration` | Liste des valeurs interdites : voir §3. Corriger `.env` puis `docker compose up -d` |
| Backend `password authentication failed for user "supervisor"` | Vous avez changé `POSTGRES_PASSWORD` après la première init. Voir « changement de mot de passe DB » ci-dessous |
| Frontend `Cannot find module '/app/server.js'` | Bind mount dev hérité du compose de base. Le prod overlay doit utiliser `volumes: !reset []` (déjà fait dans le repo) |
| `failed to bind host port 127.0.0.1:8000` | Bindings hérités du compose de base. Le prod overlay doit utiliser `ports: !reset []` (déjà fait dans le repo) |
| Healthcheck frontend / backend / nginx KO alors que le service répond | Healthcheck doit utiliser `127.0.0.1` (Alpine résout `localhost` en IPv6, mais le serveur n'écoute qu'en IPv4) |
| Redirect FastAPI vers `http://...` au lieu de `https://...` (downgrade) | uvicorn doit tourner avec `--proxy-headers --forwarded-allow-ips='*'` pour faire confiance au `X-Forwarded-Proto` envoyé par nginx. Déjà OK dans `backend/docker-entrypoint.sh` en mode `production` |
| Scheduler en double | `--workers` doit valoir 1 (déjà OK dans Dockerfile prod). Si vous augmentez les workers, configurer un job store APScheduler en DB |
| Port 80/443 déjà occupé | IIS actif ? `Stop-Service W3SVC ; Set-Service W3SVC -StartupType Disabled` |
| Disque saturé | `docker system prune -af --volumes` (⚠️ détruit les volumes non utilisés — pas le volume `postgres_data` s'il est attaché au compose actif) |

### Changement de mot de passe DB

Postgres mémorise le mot de passe lors de l'**init du volume**. Le changer dans
`.env` après coup ne le change pas dans la DB.

**Sur un volume neuf** (déploiement fresh) : pas de problème.

**Sur un volume existant**, deux options :

```powershell
# Option A — Recréer le volume (perte de données)
docker compose -f docker-compose.yml -f docker-compose.prod.yml down -v
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Option B — Mettre à jour le mot de passe en place
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec postgres `
  psql -U supervisor -d network_supervisor `
  -c "ALTER USER supervisor WITH PASSWORD '<nouveau_mdp>';"
# Puis ajuster .env et redémarrer le backend
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart backend
```

---

## 11. Checklist de mise en production

- [ ] `APP_ENV=production` et `DEBUG=false` dans `.env`
- [ ] `API_KEY` et `POSTGRES_PASSWORD` régénérés (jamais les valeurs `.env.example`)
- [ ] Certificat TLS placé dans `nginx/certs/`
- [ ] Pare-feu : 80/443 ouverts, restreints au sous-réseau si nécessaire
- [ ] `docker compose ps` → tous services `healthy`
- [ ] `https://<host>/api/v1/health` → 200 OK
- [ ] Tâche planifiée Docker auto-démarrage configurée
- [ ] Backup PostgreSQL planifié
- [ ] Test d'envoi d'une notification (Slack/email) depuis l'UI
- [ ] Redémarrage du serveur testé : tous les services remontent automatiquement
