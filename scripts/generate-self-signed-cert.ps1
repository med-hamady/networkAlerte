<#
.SYNOPSIS
  Generates a self-signed certificate for nginx (internal network / lab).

.DESCRIPTION
  Produces nginx/certs/fullchain.pem and nginx/certs/privkey.pem in the format
  expected by the nginx config. Uses OpenSSL if available, otherwise falls
  back to native PowerShell cmdlets (New-SelfSignedCertificate).

.PARAMETER CommonName
  Primary CN / SAN of the certificate (e.g. supervisor.local, 192.168.1.50).

.PARAMETER ValidityDays
  Validity in days (default 825 - upper bound accepted by browsers).

.EXAMPLE
  .\scripts\generate-self-signed-cert.ps1 -CommonName supervisor.local

.EXAMPLE
  .\scripts\generate-self-signed-cert.ps1 -CommonName 10.0.0.50 -ValidityDays 365
#>
param(
  [Parameter(Mandatory = $true)]
  [string]$CommonName,

  [int]$ValidityDays = 825
)

$ErrorActionPreference = "Stop"

$repoRoot  = Split-Path -Parent $PSScriptRoot
$certsDir  = Join-Path $repoRoot "nginx\certs"
$fullchain = Join-Path $certsDir "fullchain.pem"
$privkey   = Join-Path $certsDir "privkey.pem"

if (-not (Test-Path $certsDir)) {
  New-Item -ItemType Directory -Path $certsDir | Out-Null
}

$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $openssl) {
  # Common Git-for-Windows install paths (openssl ships with it)
  $candidates = @(
    "$env:ProgramFiles\Git\usr\bin\openssl.exe",
    "$env:ProgramFiles\Git\mingw64\bin\openssl.exe",
    "${env:ProgramFiles(x86)}\Git\usr\bin\openssl.exe",
    "$env:LOCALAPPDATA\Programs\Git\usr\bin\openssl.exe"
  )
  foreach ($p in $candidates) {
    if (Test-Path $p) { $openssl = Get-Item $p; break }
  }
}

if ($openssl) {
  Write-Host "Generating via OpenSSL ($($openssl.Source))..."

  # SAN: IP if CN looks like an IPv4, otherwise DNS
  $san = if ($CommonName -match '^\d{1,3}(\.\d{1,3}){3}$') { "IP:$CommonName" } else { "DNS:$CommonName" }

  $configPath = Join-Path $env:TEMP "nginx-selfsigned.cnf"
  @"
[req]
distinguished_name = req_distinguished_name
x509_extensions    = v3_req
prompt             = no

[req_distinguished_name]
CN = $CommonName

[v3_req]
keyUsage         = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName   = $san
"@ | Set-Content -Path $configPath -Encoding ascii

  $opensslExe = if ($openssl.Source) { $openssl.Source } else { $openssl.FullName }
  & $opensslExe req -x509 -nodes -newkey rsa:2048 `
    -days $ValidityDays `
    -keyout $privkey `
    -out    $fullchain `
    -config $configPath
  if ($LASTEXITCODE -ne 0) { throw "openssl failed (exit code $LASTEXITCODE)" }

  Remove-Item $configPath -ErrorAction SilentlyContinue
} else {
  throw @"
OpenSSL not found.

Install one of:
  - Git for Windows (recommended; ships openssl) - https://git-scm.com/download/win
  - OpenSSL standalone        - https://slproweb.com/products/Win32OpenSSL.html
  - winget install ShiningLight.OpenSSL.Light

Then re-run this script.
"@
}

Write-Host ""
Write-Host "OK:"
Write-Host "  $fullchain"
Write-Host "  $privkey"
Write-Host ""
Write-Host "Start prod stack:"
Write-Host "  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"
