<#
.SYNOPSIS
    Recoit une archive de sauvegarde envoyee par le serveur de prod, verifie son
    empreinte, et la range dans le dossier de sauvegarde.

.DESCRIPTION
    Tourne sur le serveur Windows de sauvegarde (10.135.0.210), appele par SSH
    depuis scripts/push-backup.sh sur le serveur de prod. Ce script ne va rien
    chercher : il traite ce qui vient d'etre depose dans le dossier de transit.
    Aucune tache planifiee n'est necessaire sur cette machine.

    Pourquoi un dossier de transit plutot qu'un depot direct : le fichier ne
    doit apparaitre dans le dossier de sauvegarde qu'une fois COMPLET et
    VERIFIE. Si ce dossier est synchronise vers un cloud (client Sync.com par
    exemple), son client televerserait sinon un fichier a moitie ecrit.

    /!\ CE SCRIPT NE DECHIFFRE RIEN. L'archive arrive chiffree (AES-256, faite
        sur la prod). La phrase secrete ne doit PAS exister sur cette machine :
        sinon la donnee et sa cle sont au meme endroit.

    /!\ Dossier de transit et dossier de sauvegarde sur LE MEME VOLUME. Sur un
        meme volume un deplacement est un renommage, instantane et indivisible ;
        d'un volume a l'autre c'est une copie, pendant laquelle le fichier est
        visible incomplet.

    Code de sortie : 0 = publie (ou deja present a l'identique), 1 = refuse.
    push-backup.sh ne marque l'archive comme envoyee que sur 0.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\Backups\receive-backup.ps1 `
        -FileName "supervisor-2026-09-16_050000.tar.enc" -ExpectedHash "a1b2..." `
        -StagingDir C:\Backups\_transit -DestDir C:\Backups\supervisor -KeepDays 30
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $FileName,
    [Parameter(Mandatory = $true)] [string] $ExpectedHash,
    [string] $StagingDir = 'C:\Backups\_transit',
    [string] $DestDir    = 'C:\Backups\supervisor',
    [int]    $KeepDays   = 30,
    [string] $LogFile    = 'C:\Backups\receive-backup.log'
)

$ErrorActionPreference = 'Stop'

function Write-Log {
    param([string] $Message, [string] $Level = 'INFO')
    $line = "{0}  {1}  {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level.PadRight(5), $Message
    # Sur stdout : push-backup.sh reprend cette sortie dans le log du serveur de
    # prod, pour que tout l'incident se lise au meme endroit.
    Write-Host $line
    try { Add-Content -Path $LogFile -Value $line -Encoding utf8 } catch { }
}

try {
    # --- Garde-fou sur le nom recu -------------------------------------------
    # Le nom arrive par SSH. Meme s'il vient de notre propre serveur, on
    # n'accepte qu'un nom de fichier NU : un "..\..\" transformerait ce script
    # en primitive d'ecriture arbitraire sur la machine.
    if ($FileName -match '[\\/]' -or $FileName -match '\.\.') {
        Write-Log "Nom de fichier refuse : '$FileName'" 'ERROR'
        exit 1
    }
    if ($FileName -notmatch '^supervisor-[\w.-]+\.tar\.enc$') {
        Write-Log "Nom hors convention (attendu supervisor-*.tar.enc) : '$FileName'" 'ERROR'
        exit 1
    }

    $staged = Join-Path $StagingDir $FileName
    if (-not (Test-Path $staged)) {
        Write-Log "Fichier absent du dossier de transit : $staged" 'ERROR'
        exit 1
    }

    if (-not (Test-Path $DestDir)) {
        New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
        Write-Log "Dossier de sauvegarde cree : $DestDir"
    }

    $target = Join-Path $DestDir $FileName

    # --- Verification d'integrite --------------------------------------------
    # Une sauvegarde non verifiee n'est pas une sauvegarde : un transfert
    # tronque produit un fichier d'allure normale, que personne ne decouvrira
    # avant le jour ou il faudra restaurer.
    $actual = (Get-FileHash -Path $staged -Algorithm SHA256).Hash.ToLower()
    $wanted = $ExpectedHash.Trim().ToLower()

    if ($actual -ne $wanted) {
        Write-Log "Empreinte NON conforme - fichier rejete et supprime." 'ERROR'
        Write-Log "  attendu : $wanted" 'ERROR'
        Write-Log "  obtenu  : $actual" 'ERROR'
        Remove-Item $staged -Force -ErrorAction SilentlyContinue
        Remove-Item "$staged.sha256" -Force -ErrorAction SilentlyContinue
        exit 1
    }
    Write-Log "Empreinte SHA-256 conforme."

    # --- Deja publiee ? -------------------------------------------------------
    # L'envoi est rejouable (rattrapage, relance manuelle, marqueur perdu cote
    # prod) : republier a l'identique ne sert a rien.
    if (Test-Path $target) {
        $existing = (Get-FileHash -Path $target -Algorithm SHA256).Hash.ToLower()
        if ($existing -eq $wanted) {
            Write-Log "Deja presente a l'identique - transit nettoye, rien a faire."
            Remove-Item $staged -Force -ErrorAction SilentlyContinue
            Remove-Item "$staged.sha256" -Force -ErrorAction SilentlyContinue
            exit 0
        }
        Write-Log "Presente mais differente - remplacement." 'WARN'
    }

    # --- Publication ----------------------------------------------------------
    Move-Item -Path $staged -Destination $target -Force
    if (Test-Path "$staged.sha256") {
        # L'empreinte accompagne l'archive : celui qui restaurera depuis ce
        # dossier pourra verifier son fichier sans rien d'autre.
        Move-Item -Path "$staged.sha256" -Destination "$target.sha256" -Force
    }
    $sizeMb = [math]::Round((Get-Item $target).Length / 1MB, 1)
    Write-Log "Publiee : $FileName ($sizeMb Mo) dans $DestDir"

    # --- Retention ------------------------------------------------------------
    # /!\ Si $DestDir est un dossier synchronise vers un cloud, supprimer ici
    #     supprime AUSSI dans le cloud.
    $cutoff = (Get-Date).AddDays(-$KeepDays)
    $stale = Get-ChildItem -Path $DestDir -Filter 'supervisor-*.tar.enc*' -File -ErrorAction SilentlyContinue |
             Where-Object { $_.LastWriteTime -lt $cutoff }
    foreach ($f in $stale) {
        Remove-Item $f.FullName -Force
        Write-Log "Retention : $($f.Name) supprime (> $KeepDays j)"
    }

    $kept = @(Get-ChildItem -Path $DestDir -Filter 'supervisor-*.tar.enc' -File -ErrorAction SilentlyContinue)
    Write-Log "$($kept.Count) sauvegarde(s) dans $DestDir."
    exit 0
}
catch {
    Write-Log "Echec inattendu : $($_.Exception.Message)" 'ERROR'
    exit 1
}
