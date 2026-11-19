# PostgreSQL ka backup (pg_dump custom format) + data\ (reg_archive, system_prompt) ka backup.py.
#   .\scripts\pg_backup.ps1                         (docker-compose.db.yml wala Postgres)
#   .\scripts\pg_backup.ps1 -ComposeFile docker-compose.yml -Keep 30
# Backup \backups\pg-YYYYmmdd-HHMMSS.dump mein. Isko kisi DOOSRI drive/machine par bhi copy karo (aur encrypt karo:
# is mein password hashes aur company documents hain). Restore ki jaanch: .\scripts\pg_restore_test.ps1
param([string]$ComposeFile = "docker-compose.db.yml", [int]$Keep = 14, [string]$Dest = "backups")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

$cid = (docker compose -f $ComposeFile ps -q postgres).Trim()
if (-not $cid) { Write-Host "Postgres container nahi mila ($ComposeFile). Pehle chalao." -ForegroundColor Red; exit 1 }

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $Dest "pg-$stamp.dump"
# Container ke andar dump (local socket), phir docker cp: PowerShell pipe binary file kharab kar deta hai
docker exec $cid pg_dump -U company_ai -d company_ai -Fc -f /tmp/backup.dump
if ($LASTEXITCODE -ne 0) { Write-Host "pg_dump fail hua." -ForegroundColor Red; exit 1 }
docker cp "${cid}:/tmp/backup.dump" $out
if ($LASTEXITCODE -ne 0) { Write-Host "docker cp fail hua." -ForegroundColor Red; exit 1 }
docker exec $cid rm -f /tmp/backup.dump | Out-Null

$size = (Get-Item $out).Length
if ($size -lt 1024) { Write-Host "Backup suspicious chhota hai ($size bytes)." -ForegroundColor Red; exit 1 }
Write-Host ("PostgreSQL backup: {0} ({1:N1} KB)" -f $out, ($size / 1KB)) -ForegroundColor Green

if (Test-Path "data") { python scripts\backup.py --dest $Dest --keep $Keep }

Get-ChildItem $Dest -Filter "pg-*.dump" | Sort-Object Name -Descending | Select-Object -Skip $Keep |
    ForEach-Object { Remove-Item $_.FullName -Force; Write-Host "purana backup hataya: $($_.Name)" }
exit 0
