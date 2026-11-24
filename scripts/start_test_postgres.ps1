# Starts a throwaway PostgreSQL cluster for the test suite (X.237). It never touches the live database.
# Usage:  .\scripts\start_test_postgres.ps1 ; then, in the same shell:
#   $env:TEST_DATABASE_URL = "postgresql+psycopg://rev@127.0.0.1:55477/rev_test"
#   pytest -q
# Override the PostgreSQL install with -Bin, the data directory with -Data, the port with -Port.
param(
    [string]$Bin = "C:\Program Files\PostgreSQL\18\bin",
    [string]$Data = "$env:LOCALAPPDATA\research-test-pg",
    [int]$Port = 55477
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path "$Data\PG_VERSION")) {
    & "$Bin\initdb.exe" -D $Data -U rev -A trust -E UTF8 | Out-Null
}
$opts = "-p $Port -c listen_addresses=127.0.0.1 -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_connections=200"
& "$Bin\pg_ctl.exe" -D $Data status *> $null
if ($LASTEXITCODE -ne 0) {
    # Created through WMI so the postmaster is not a child of this shell: a caller that tears down its
    # process tree (CI step, editor task, agent tool) would otherwise kill the database mid-run.
    $cmd = "`"$Bin\pg_ctl.exe`" -D `"$Data`" -o `"$opts`" -l `"$Data\server.log`" start"
    Remove-Item "$Data\postmaster.pid" -ErrorAction SilentlyContinue
    Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd } | Out-Null
    for ($i = 0; $i -lt 60; $i++) {
        & "$Bin\pg_isready.exe" -h 127.0.0.1 -p $Port *> $null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Milliseconds 500
    }
}
$exists = & "$Bin\psql.exe" -h 127.0.0.1 -p $Port -U rev -d postgres -tAc "select 1 from pg_database where datname='rev_test'"
if (-not $exists) { & "$Bin\createdb.exe" -h 127.0.0.1 -p $Port -U rev rev_test }
Write-Output "TEST_DATABASE_URL=postgresql+psycopg://rev@127.0.0.1:$Port/rev_test"
