param(
    [string]$PostgresImage = 'postgres:16-alpine',
    [string]$BackendImage = 'networkalerte-backend-test'
)

# Disposable PostgreSQL rehearsal. No company .env, logs, data or host ports.
$ErrorActionPreference = 'Stop'
$auditRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$auditRunId = [guid]::NewGuid().ToString('N').Substring(0, 12)
$auditNetwork = "networkalerte-schema-audit-$auditRunId"
$auditDatabase = "$auditNetwork-db"
$auditRunner = "$auditNetwork-runner"
$auditPassword = [guid]::NewGuid().ToString('N')
$auditLabel = "com.networkalerte.schema-audit=$auditRunId"
$auditNetworkCreated = $false
$auditDatabaseCreated = $false

function Invoke-AuditDocker {
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw "Docker audit command failed (exit $LASTEXITCODE)" }
}

try {
    # Never download images or use a production container as the database.
    Invoke-AuditDocker image inspect --format '{{.Id}}' $PostgresImage
    Invoke-AuditDocker image inspect --format '{{.Id}}' $BackendImage
    Invoke-AuditDocker network create --internal --label $auditLabel $auditNetwork
    $auditNetworkCreated = $true
    $auditInternal = Invoke-AuditDocker network inspect --format '{{.Internal}}' $auditNetwork
    if ($auditInternal -ne 'true') { throw 'Audit network is not internal' }

    Invoke-AuditDocker run --pull=never --detach --name $auditDatabase --label $auditLabel `
        --network $auditNetwork --network-alias schema-db `
        --tmpfs '/var/lib/postgresql/data:rw,nosuid' `
        --env POSTGRES_USER=schema_audit --env POSTGRES_DB=schema_audit `
        --env "POSTGRES_PASSWORD=$auditPassword" $PostgresImage
    $auditDatabaseCreated = $true
    $auditReady = $false
    for ($auditAttempt = 0; $auditAttempt -lt 40; $auditAttempt++) {
        & docker exec $auditDatabase pg_isready --username schema_audit --dbname schema_audit *> $null
        if ($LASTEXITCODE -eq 0) { $auditReady = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $auditReady) { throw 'Disposable PostgreSQL did not become ready' }

    # Hide image-baked /app, then mount source files only. In particular, the
    # real backend/.env, backend/data and backend/logs are never mounted.
    $auditMounts = @(
        '--mount', "type=bind,source=$auditRoot\backend\app,target=/app/app,readonly",
        '--mount', "type=bind,source=$auditRoot\backend\alembic,target=/app/alembic,readonly",
        '--mount', "type=bind,source=$auditRoot\backend\alembic.ini,target=/app/alembic.ini,readonly",
        '--mount', "type=bind,source=$auditRoot\backend\scripts\test_fai_migrations.py,target=/app/scripts/test_fai_migrations.py,readonly",
        '--mount', "type=bind,source=$auditRoot\backend\scripts\check_company_database.py,target=/app/scripts/check_company_database.py,readonly"
    )
    Invoke-AuditDocker run --pull=never --rm --name $auditRunner --label $auditLabel `
        --network $auditNetwork --read-only --tmpfs '/app:rw,nosuid' --tmpfs '/tmp:rw,nosuid' `
        --cap-drop ALL --security-opt no-new-privileges --entrypoint python --workdir /app `
        --env PYTHONPATH=/app --env PYTHONDONTWRITEBYTECODE=1 `
        --env SCHEMA_AUDIT_DISPOSABLE=synthetic-data-only `
        --env POSTGRES_HOST=schema-db --env POSTGRES_PORT=5432 `
        --env POSTGRES_USER=schema_audit --env POSTGRES_DB=schema_audit `
        --env "POSTGRES_PASSWORD=$auditPassword" `
        --env SCHEDULER_ENABLED=false --env CLIENT_BLOCK_ENFORCEMENT_ENABLED=false `
        --env MIKROTIK_ENABLED=false --env UISP_SYNC_ENABLED=false --env WHATSAPP_ENABLED=false `
        @auditMounts $BackendImage scripts/test_fai_migrations.py
}
finally {
    # Exact names created during this invocation only; never compose down or prune.
    if ($auditDatabaseCreated) { & docker rm --force --volumes $auditDatabase | Out-Null }
    if ($auditNetworkCreated) { & docker network rm $auditNetwork | Out-Null }
}
