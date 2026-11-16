# PostgreSQL + pgvector ko is machine par chalao aur asli integration tests (isolation sahit) chalao.
#   .\scripts\setup_postgres.ps1            (DB up + Postgres tests)
#   .\scripts\setup_postgres.ps1 -SkipTests
# Zaroori: Docker Desktop install + chal raha ho. Yeh script .env mein sirf POSTGRES_PASSWORD (agar na ho) jodta hai,
# mojooda lines kabhi nahi badalta. DATABASE_URL / RAG_STORE_BACKEND ko khud NAHI badalta (pehle migration, README "Postgres migration").
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker nahi mila. Docker Desktop install karo (wsl --install, restart), phir dobara chalao." -ForegroundColor Red
    exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker install hai lekin chal nahi raha. Docker Desktop kholo, 'running' dikhne tak ruko, phir dobara chalao." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".env")) { New-Item ".env" -ItemType File | Out-Null }
$raw = Get-Content ".env" -Raw
if ($raw -match '(?m)^POSTGRES_PASSWORD=(\S+)\s*$') {
    $pw = $Matches[1]
    Write-Host "POSTGRES_PASSWORD .env mein pehle se hai (use ho raha hai)."
} else {
    $chars = [char[]](48..57 + 65..90 + 97..122)
    $bytes = New-Object byte[] 24
    $rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
    $rng.GetBytes($bytes)
    $pw = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
    if ($raw -and -not $raw.EndsWith("`n")) { Add-Content ".env" "" }
    Add-Content ".env" "POSTGRES_PASSWORD=$pw"
    Write-Host "Naya POSTGRES_PASSWORD .env mein likh diya (.env kabhi git mein nahi jata)."
}

docker compose -f docker-compose.db.yml up -d
if ($LASTEXITCODE -ne 0) { Write-Host "docker compose up fail hua." -ForegroundColor Red; exit 1 }

Write-Host "PostgreSQL ke healthy hone ka intezar..."
$cid = (docker compose -f docker-compose.db.yml ps -q postgres).Trim()
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    $state = (docker inspect --format "{{.State.Health.Status}}" $cid).Trim()
    if ($state -eq "healthy") { $ok = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $ok) { Write-Host "Database healthy nahi hua: docker compose -f docker-compose.db.yml logs postgres" -ForegroundColor Red; exit 1 }
Write-Host "PostgreSQL healthy." -ForegroundColor Green

if ($SkipTests) { exit 0 }
$env:TEST_DATABASE_URL = "postgresql://company_ai:$pw@127.0.0.1:5432/company_ai"
python -m pytest tests\test_postgres_integration.py tests\test_repo_parity.py tests\test_pg_isolation_http.py -v -rs
$code = $LASTEXITCODE
if ($code -eq 0) {
    Write-Host "`nPostgres tests PASS (koi skip nahi hona chahiye: upar '-rs' summary dekho). Ab README ka 'Postgres migration' section." -ForegroundColor Green
} else {
    Write-Host "`nTests fail hue: poora output bhej do." -ForegroundColor Red
}
exit $code
