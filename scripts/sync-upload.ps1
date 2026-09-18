<#
.SYNOPSIS
    Rapatrie la derniere sauvegarde du serveur de prod et la depose dans le
    dossier Sync.com, qui se charge de l'envoi vers le cloud.

.DESCRIPTION
    Ce script existe a cause d'une contrainte de Sync.com, pas par gout du
    detour : Sync.com n'a AUCUN client Linux, AUCUNE API publique, pas de
    WebDAV, et n'est pas supporte par rclone. C'est la contrepartie directe de
    leur chiffrement zero-knowledge, ou seule l'application cliente detient les
    cles. Le serveur de prod etant un Ubuntu headless, il ne peut pas parler a
    Sync.com lui-meme : il faut une machine Windows (ou macOS) ou le client
    Sync.com officiel est installe et connecte au compte de l'entreprise.

    Sens du transfert : c'est le RELAIS qui va CHERCHER (scp) sur le serveur.
    Le serveur n'a donc besoin d'aucun acces sortant vers Internet ni vers le
    relais, et le relais n'ouvre aucun port. C'est le sens le plus sur des deux.

    /!\ LA MACHINE RELAIS DOIT ETRE ALLUMEE a l'heure planifiee. Un poste de
        travail eteint la nuit ne sauvegarde rien - et ne le dit pas. Preferer
        un poste qui reste allume, et surveiller le journal.

    /!\ L'archive est chiffree PAR LE SERVEUR (AES-256, cf. backup-db.sh) avant
        d'arriver ici. Elle transite et sejourne donc chiffree de bout en bout.
        La phrase secrete ne doit JAMAIS etre stockee sur ce relais ni dans le
        cloud : sinon la donnee et sa cle voyagent ensemble.

.PARAMETER SyncFolder
    Dossier synchronise par le client Sync.com (ex. C:\Users\<moi>\Sync\Backups\supervisor).

.PARAMETER KeepDays
    Retention DANS LE DOSSIER SYNC. /!\ Supprimer ici supprime AUSSI dans le
    cloud - c'est le principe d'un dossier synchronise. Sync.com conserve les
    fichiers supprimes un certain temps et permet de les restaurer depuis son
    interface web, mais ne comptez pas dessus comme sur une archive.

.EXAMPLE
    .\sync-upload.ps1 -DryRun
    .\sync-upload.ps1 -SyncFolder "C:\Users\pc\Sync\Backups\supervisor"
#>
[CmdletBinding()]
param(
    [string] $ServerHost   = "10.135.3.25",
    [string] $ServerUser   = "a2",
    [string] $RemoteDir    = "/opt/a2project/backups",
    [string] $SyncFolder   = "$env:USERPROFILE\Sync\Backups\supervisor",
    [string] $IdentityFile = "$env:USERPROFILE\.ssh\id_ed25519",
    [int]    $KeepDays     = 30,
    [string] $LogFile      = "$env:USERPROFILE\Sync-backup.log",
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'

function Write-Log {
    param([string] $Message, [string] $Level = 'INFO')
    $line = "{0}  {1}  {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level.PadRight(5), $Message
    Write-Host $line
    # Le journal est la SEULE preuve qu'une nuit s'est bien passee : une tache
    # planifiee qui echoue le fait en silence.
    try { Add-Content -Path $LogFile -Value $line -Encoding utf8 } catch { }
}

function Invoke-Fail {
    param([string] $Message)
    Write-Log $Message 'ERROR'
    exit 1
}

Write-Log "=== Relais de sauvegarde vers Sync.com ==="

# --- Prerequis ---------------------------------------------------------------
foreach ($tool in @('ssh', 'scp')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Invoke-Fail "$tool introuvable. Installer le client OpenSSH : Settings > Apps > Optional features > OpenSSH Client."
    }
}

$sshTarget = "$ServerUser@$ServerHost"
$sshArgs   = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15')
if (Test-Path $IdentityFile) {
    $sshArgs += @('-i', $IdentityFile)
} else {
    # BatchMode interdit toute invite : sans cle, la connexion echouera au lieu
    # de rester bloquee indefiniment sur un prompt que personne ne lira.
    Write-Log "Cle SSH absente ($IdentityFile) - la connexion va probablement echouer." 'WARN'
}

if (-not (Test-Path $SyncFolder)) {
    if ($DryRun) {
        Write-Log "[dry-run] creerait $SyncFolder"
    } else {
        New-Item -ItemType Directory -Path $SyncFolder -Force | Out-Null
        Write-Log "Dossier Sync cree : $SyncFolder"
    }
}

# --- 1. Quel est le fichier le plus recent cote serveur ? --------------------
# On lit le .sha256, qui porte a la fois l'empreinte et le VRAI nom du fichier
# (le symlink `latest` ne le donne pas). Une seule source pour les deux evite
# de rapatrier un fichier et de verifier l'empreinte d'un autre.
Write-Log "Lecture de $RemoteDir/latest.tar.enc.sha256 sur $sshTarget ..."
$shaLine = & ssh @sshArgs $sshTarget "cat '$RemoteDir/latest.tar.enc.sha256'" 2>&1
if ($LASTEXITCODE -ne 0) {
    Invoke-Fail "Connexion ou lecture impossible : $shaLine"
}

$shaLine = ($shaLine | Select-Object -First 1).Trim()
if ($shaLine -notmatch '^([0-9a-fA-F]{64})\s+\*?(.+)$') {
    Invoke-Fail "Format .sha256 inattendu : '$shaLine'"
}
$expectedHash = $Matches[1].ToLower()
$fileName     = $Matches[2].Trim()
Write-Log "Derniere sauvegarde : $fileName"

$destPath = Join-Path $SyncFolder $fileName

# --- 2. Deja rapatriee ? -----------------------------------------------------
# La tache peut se rejouer (rattrapage apres une nuit manquee, lancement
# manuel) : ne pas re-telecharger des dizaines de Mo pour rien, et surtout ne
# pas re-ecrire un fichier que le client Sync.com a deja televerse.
if (Test-Path $destPath) {
    $existing = (Get-FileHash -Path $destPath -Algorithm SHA256).Hash.ToLower()
    if ($existing -eq $expectedHash) {
        Write-Log "Deja presente et conforme - rien a faire."
        $skipDownload = $true
    } else {
        Write-Log "Presente mais empreinte differente - re-telechargement." 'WARN'
        $skipDownload = $false
    }
} else {
    $skipDownload = $false
}

# --- 3. Rapatriement ---------------------------------------------------------
if (-not $skipDownload) {
    if ($DryRun) {
        Write-Log "[dry-run] scp ${sshTarget}:$RemoteDir/$fileName -> $destPath"
    } else {
        # Telechargement sous un nom temporaire : le client Sync.com surveille
        # ce dossier en permanence et televerserait un fichier a moitie ecrit
        # s'il portait deja son nom definitif.
        $tmpPath = "$destPath.part"
        Write-Log "Telechargement..."
        & scp @sshArgs "${sshTarget}:$RemoteDir/$fileName" $tmpPath
        if ($LASTEXITCODE -ne 0) {
            Remove-Item $tmpPath -ErrorAction SilentlyContinue
            Invoke-Fail "scp a echoue (code $LASTEXITCODE)"
        }

        # --- 4. Verification d'integrite ------------------------------------
        # Une sauvegarde qu'on n'a pas verifiee n'est pas une sauvegarde : un
        # transfert tronque produit un fichier d'allure normale, que personne
        # ne decouvrira avant le jour ou il faudra restaurer.
        $actualHash = (Get-FileHash -Path $tmpPath -Algorithm SHA256).Hash.ToLower()
        if ($actualHash -ne $expectedHash) {
            Remove-Item $tmpPath -ErrorAction SilentlyContinue
            Invoke-Fail "Empreinte SHA-256 non conforme (attendu $expectedHash, obtenu $actualHash) - fichier rejete."
        }

        Move-Item -Path $tmpPath -Destination $destPath -Force
        $sizeMb = [math]::Round((Get-Item $destPath).Length / 1MB, 1)
        Write-Log "OK - $fileName ($sizeMb Mo) depose dans le dossier Sync, empreinte verifiee."
    }
}

# --- 5. Retention dans le dossier Sync --------------------------------------
$cutoff = (Get-Date).AddDays(-$KeepDays)
$stale  = Get-ChildItem -Path $SyncFolder -Filter 'supervisor-*.tar*' -File -ErrorAction SilentlyContinue |
          Where-Object { $_.LastWriteTime -lt $cutoff }

if ($stale) {
    foreach ($f in $stale) {
        if ($DryRun) {
            Write-Log "[dry-run] supprimerait $($f.Name)"
        } else {
            # /!\ Supprimer ici supprime AUSSI dans le cloud (dossier synchronise).
            Remove-Item $f.FullName -Force
            Write-Log "Retention : $($f.Name) supprime (> $KeepDays j)"
        }
    }
}

# --- 6. Etat du dossier ------------------------------------------------------
$kept = @(Get-ChildItem -Path $SyncFolder -Filter 'supervisor-*.tar*' -File -ErrorAction SilentlyContinue)
Write-Log "$($kept.Count) sauvegarde(s) dans le dossier Sync."
Write-Log "Le client Sync.com televerse maintenant en arriere-plan - verifier son icone de notification."
Write-Log "=== Termine ==="
