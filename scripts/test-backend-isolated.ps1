# Offline tests: no company .env, credentials, data, host network or device access.
[CmdletBinding()]
param(
    [string[]]$Tests = @('tests/test_fai_request_execution.py')
)
$ErrorActionPreference = 'Stop'
$backendRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\backend'))
$dockerArgs = @(
    'run', '--pull=never', '--rm', '--network', 'none',
    '--entrypoint', 'python', '--tmpfs', '/app:rw,mode=1777', '-w', '/app',
    '-e', 'PYTHONPATH=/app', '-e', 'PYTHONDONTWRITEBYTECODE=1',
    '-e', 'POSTGRES_HOST=invalid.test', '-e', 'POSTGRES_PORT=5432',
    '-e', 'POSTGRES_DB=networkalerte_test', '-e', 'POSTGRES_USER=test',
    '-e', 'POSTGRES_PASSWORD=dummy-isolated-test',
    '-e', 'COMPANY_READ_ONLY=false', '-e', 'API_KEY=', '-e', 'FAI_API_KEY=',
    '-e', 'LR_VERIFY_API_KEY=', '-e', 'UISP_ASSIGN_API_KEY=',
    '-e', 'CONTENT_BLOCK_API_KEY=', '-e', 'CLIENT_SIGNAL_API_KEY=',
    '-e', 'SCHEDULER_ENABLED=false', '-e', 'CLIENT_BLOCK_ENFORCEMENT_ENABLED=false',
    '-e', 'WHATSAPP_ENABLED=false', '-e', 'UISP_SYNC_ENABLED=false',
    '-e', 'UISP_STATION_SYNC_ENABLED=false', '-e', 'NETFLOW_COLLECTOR_ENABLED=false',
    '-e', 'MIKROTIK_ENABLED=false', '-e', 'TOPOLOGY_SYNC_ENABLED=false',
    '-e', 'SWITCH_PORT_MAPPING_ENABLED=false',
    '-e', 'FAI_LOG_PATH=/tmp/fai-test.log', '-e', 'FAI_EVIDENCE_DIR=/tmp/fai-evidence'
)
foreach ($item in @('app', 'tests', 'scripts', 'alembic', 'alembic.ini', 'pyproject.toml')) {
    $localPath = Join-Path $backendRoot $item
    $dockerArgs += @('--mount', "type=bind,source=$localPath,target=/app/$item,readonly")
}
$dockerArgs += @('networkalerte-backend-test', '-m', 'pytest', '-p', 'no:cacheprovider', '-q')
$dockerArgs += $Tests
& docker @dockerArgs
if ($LASTEXITCODE -ne 0) { throw "Isolated tests failed (exit $LASTEXITCODE)." }
