# Windows services (NSSM): chat model, embedding model, API (aur optional Caddy) boot par khud chalein, crash par khud
# wapas aayein. Administrator PowerShell chahiye.
#
#   .\scripts\install_services.ps1 -Nssm C:\tools\nssm.exe -LlamaServer C:\llama\llama-server.exe -Caddy C:\caddy\caddy.exe
#   .\scripts\install_services.ps1 -Nssm C:\tools\nssm.exe -Uninstall
#   .\scripts\install_services.ps1 -Nssm ... -RunAs ".\sh"      (password poochta hai: GPU/Vulkan session ke liye zaroori ho sakta hai)
#
# Docker Desktop (PostgreSQL) ko Windows login/boot par start hone ke liye set karo. API Postgres na milne par band ho jati
# hai aur NSSM 5 second baad dobara try karta hai, is liye boot ka order khud theek ho jata hai.
# Ye script is sandbox mein chal nahi saki: pehli dafa test machine par chalao, phir ek dafa reboot karke dekho.
param(
    [Parameter(Mandatory)] [string]$Nssm,
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$LlamaServer = "",
    [string]$ChatModel = "models\Qwen3.5-9B-Q4_K_M.gguf",
    [string]$EmbedModel = "models\Qwen3-Embedding-0.6B-Q8_0.gguf",
    [string]$Caddy = "",
    [string]$RunAs = "",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Administrator PowerShell chahiye." -ForegroundColor Red; exit 1
}
if (-not (Test-Path $Nssm)) { Write-Host "nssm.exe nahi mila: $Nssm (https://nssm.cc)" -ForegroundColor Red; exit 1 }

$names = @("CompanyAI-Chat", "CompanyAI-Embed", "CompanyAI-API", "CompanyAI-Caddy")
if ($Uninstall) {
    foreach ($n in $names) {
        & $Nssm stop $n 2>$null | Out-Null
        & $Nssm remove $n confirm 2>$null | Out-Null
    }
    Write-Host "Services hata di gayin."; exit 0
}

$Logs = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Write-Host "venv nahi mila: $python" -ForegroundColor Red; exit 1 }
if (-not $LlamaServer -or -not (Test-Path $LlamaServer)) { Write-Host "-LlamaServer path sahi do." -ForegroundColor Red; exit 1 }
foreach ($m in @($ChatModel, $EmbedModel)) {
    if (-not (Test-Path (Join-Path $Root $m))) { Write-Host "Model nahi mila: $m" -ForegroundColor Red; exit 1 }
}

function Install-Svc([string]$Name, [string]$Exe, [string]$SvcArgs, [string]$Dir, [string[]]$After = @()) {
    & $Nssm stop $Name 2>$null | Out-Null
    & $Nssm remove $Name confirm 2>$null | Out-Null
    & $Nssm install $Name $Exe $SvcArgs | Out-Null
    & $Nssm set $Name AppDirectory $Dir | Out-Null
    & $Nssm set $Name Start SERVICE_AUTO_START | Out-Null
    & $Nssm set $Name AppExit Default Restart | Out-Null          # crash => dobara chalao
    & $Nssm set $Name AppRestartDelay 5000 | Out-Null
    & $Nssm set $Name AppThrottle 10000 | Out-Null
    & $Nssm set $Name AppStdout (Join-Path $Logs "$Name.out.log") | Out-Null
    & $Nssm set $Name AppStderr (Join-Path $Logs "$Name.err.log") | Out-Null
    & $Nssm set $Name AppRotateFiles 1 | Out-Null                  # log rotation: disk na bhare
    & $Nssm set $Name AppRotateOnline 1 | Out-Null
    & $Nssm set $Name AppRotateBytes 10485760 | Out-Null
    if ($After.Count -gt 0) { & $Nssm set $Name DependOnService @After | Out-Null }
    if ($RunAs) {
        $cred = Get-Credential -UserName $RunAs -Message "Service account password ($Name)"
        & $Nssm set $Name ObjectName $cred.UserName ($cred.GetNetworkCredential().Password) | Out-Null
    }
    Write-Host "installed: $Name"
}

Install-Svc "CompanyAI-Chat"  $LlamaServer "-m `"$(Join-Path $Root $ChatModel)`" -ngl 99 -c 4096 -np 1 --host 127.0.0.1 --port 8081" $Root
Install-Svc "CompanyAI-Embed" $LlamaServer "-m `"$(Join-Path $Root $EmbedModel)`" --embedding --pooling last -c 4096 -np 1 -b 2048 -ub 2048 -ngl 0 --host 127.0.0.1 --port 8082" $Root
Install-Svc "CompanyAI-API"   $python "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1" $Root @("CompanyAI-Chat", "CompanyAI-Embed")
if ($Caddy) {
    if (-not (Test-Path $Caddy)) { Write-Host "caddy.exe nahi mila: $Caddy" -ForegroundColor Red; exit 1 }
    Install-Svc "CompanyAI-Caddy" $Caddy "run --config `"$(Join-Path $Root 'deploy\Caddyfile.host')`"" $Root @("CompanyAI-API")
}

foreach ($n in $names) { if (Get-Service $n -ErrorAction SilentlyContinue) { Start-Service $n } }
Write-Host "`nServices chal gayin. Check: Invoke-RestMethod http://127.0.0.1:8000/ready" -ForegroundColor Green
Write-Host "Ab test karo: API process kill karo (Stop-Process -Name python -Force) -> ~10 second mein wapas aana chahiye; phir ek dafa reboot."
