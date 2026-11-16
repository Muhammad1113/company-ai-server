# Restore ka TEST: sab se naye dump ko ek alag scratch database (restore_check) mein bahal karke ginti milata hai.
# Asli database ko haath nahi lagata. Backup tabhi backup hai jab restore chal jaye.
#   .\scripts\pg_restore_test.ps1
#   .\scripts\pg_restore_test.ps1 -File backups\pg-20261114-020000.dump -ComposeFile docker-compose.yml
param([string]$File = "", [string]$ComposeFile = "docker-compose.db.yml", [string]$Dest = "backups")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not $File) {
    $latest = Get-ChildItem $Dest -Filter "pg-*.dump" -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latest) { Write-Host "Koi pg-*.dump nahi mila. Pehle .\scripts\pg_backup.ps1" -ForegroundColor Red; exit 1 }
    $File = $latest.FullName
}
$cid = (docker compose -f $ComposeFile ps -q postgres).Trim()
if (-not $cid) { Write-Host "Postgres container nahi mila." -ForegroundColor Red; exit 1 }
Write-Host "Restore test: $File"

function Cnt([string]$db, [string]$table) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $out = docker exec $cid psql -U company_ai -d $db -tA -c "SELECT count(*) FROM $table" 2>$null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($code -ne 0) { return -1 }            # table maujood nahi
    return [int]("$out".Trim())
}

docker exec $cid psql -U company_ai -d postgres -c "DROP DATABASE IF EXISTS restore_check" | Out-Null
docker exec $cid psql -U company_ai -d postgres -c "CREATE DATABASE restore_check" | Out-Null
docker cp $File "${cid}:/tmp/restore.dump"
docker exec $cid pg_restore -U company_ai -d restore_check --no-owner /tmp/restore.dump
$restoreCode = $LASTEXITCODE

$tables = @("companies", "users", "sessions", "conversations", "messages", "rag_chunks", "reg_versions", "openfda_snapshot", "usage_log")
$bad = 0
"{0,-18} {1,10} {2,10}" -f "table", "live", "restored"
foreach ($t in $tables) {
    $live = Cnt "company_ai" $t
    $rest = Cnt "restore_check" $t
    $flag = ""
    if ($live -gt 0 -and $rest -le 0) { $flag = "  <-- RESTORE KHALI"; $bad++ }
    "{0,-18} {1,10} {2,10}{3}" -f $t, $live, $rest, $flag
}
docker exec $cid psql -U company_ai -d postgres -c "DROP DATABASE IF EXISTS restore_check" | Out-Null
docker exec $cid rm -f /tmp/restore.dump | Out-Null

if ($restoreCode -ne 0) { Write-Host "pg_restore ne errors diye (upar dekho)." -ForegroundColor Red; exit 1 }
if ($bad -gt 0) { Write-Host "RESTORE TEST FAIL." -ForegroundColor Red; exit 1 }
Write-Host "RESTORE TEST PASS (backup ke baad naya data aaya ho to live ginti thori zyada ho sakti hai: normal)." -ForegroundColor Green
exit 0
