# Model servers (8081/8082), API (8000) aur PostgreSQL (5432) ko LAN se band karo; sirf proxy (80/443) khula.
# Administrator PowerShell chahiye.
#
#   .\scripts\lock_ports.ps1 -Verify              (sirf dikhata hai: kaun sa port kahan sun raha hai, kaun se allow rules hain)
#   .\scripts\lock_ports.ps1 -Apply               (default-deny inbound + 80/443 allow + llama/DB ke "Any" allow rules band)
#   .\scripts\lock_ports.ps1 -Apply -DockerMode   (API Docker mein hai: 8081/8082 sirf Docker subnet (172.16.0.0/12) ko)
#
# Sab se pakka tareeqa: services ko 127.0.0.1 par bind rakho (start_all.ps1 / install_services.ps1 default yehi hain). Firewall doosri parat hai.
# Pehle -Verify chalao, phir -Apply. Remote Desktop/SSH jaisi apni zaroori inbound rules khud check kar lo.
param([switch]$Verify, [switch]$Apply, [switch]$DockerMode)
$ErrorActionPreference = "Stop"
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Administrator PowerShell chahiye." -ForegroundColor Red; exit 1
}
if (-not ($Verify -or $Apply)) { Write-Host "-Verify ya -Apply do."; exit 1 }

$private = @(8000, 8081, 8082, 5432)
$problems = 0

Write-Host "== Listeners (127.0.0.1 = LAN se unreachable) =="
foreach ($p in $private) {
    $l = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if (-not $l) { Write-Host ("  {0,-5} koi sun nahi raha" -f $p); continue }
    foreach ($x in $l) {
        $exposed = $x.LocalAddress -notin @("127.0.0.1", "::1")
        $mark = if ($exposed) { "<-- LAN par khula (firewall ya bind badlo)" } else { "ok (loopback)" }
        if ($exposed -and -not $DockerMode) { $problems++ }
        Write-Host ("  {0,-5} {1,-16} {2}" -f $p, $x.LocalAddress, $mark)
    }
}

Write-Host "`n== Inbound ALLOW rules jo in ports ya llama-server ko chhoote hain =="
$risky = @()
foreach ($r in Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True -ErrorAction SilentlyContinue) {
    $pf = $r | Get-NetFirewallPortFilter
    $af = $r | Get-NetFirewallApplicationFilter
    $ports = @($pf.LocalPort) -join ","
    $portHit = @($private | Where-Object { $ports -match "(^|,)$_(,|$)" }).Count -gt 0
    $progHit = [bool]($af.Program -match "llama-server|postgres|python")
    if ($portHit -or $progHit) {
        $risky += $r
        Write-Host ("  {0}  ports={1} program={2}" -f $r.DisplayName, $ports, $af.Program)
    }
}
if (-not $risky) { Write-Host "  koi nahi" }

if ($Verify) {
    $prof = Get-NetFirewallProfile | ForEach-Object { "{0}: inbound={1}" -f $_.Name, $_.DefaultInboundAction }
    Write-Host "`n== Profiles =="; $prof | ForEach-Object { Write-Host "  $_" }
    if ($problems) { Write-Host "`n$problems port(s) LAN par khule hain." -ForegroundColor Yellow; exit 1 }
    Write-Host "`nVerify ok." -ForegroundColor Green; exit 0
}

# ---- Apply ----
Set-NetFirewallProfile -Profile Domain, Private, Public -DefaultInboundAction Block
foreach ($r in $risky) { Disable-NetFirewallRule -Name $r.Name; Write-Host "band kiya: $($r.DisplayName)" }

Remove-NetFirewallRule -DisplayName "CompanyAI proxy 80/443" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "CompanyAI proxy 80/443" -Direction Inbound -Action Allow -Protocol TCP `
    -LocalPort 80, 443 -Profile Domain, Private | Out-Null
Write-Host "allow: 80/443 (Domain/Private profile)"

Remove-NetFirewallRule -DisplayName "CompanyAI llama from Docker" -ErrorAction SilentlyContinue
if ($DockerMode) {
    New-NetFirewallRule -DisplayName "CompanyAI llama from Docker" -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort 8081, 8082 -RemoteAddress "172.16.0.0/12" -Profile Any | Out-Null
    Write-Host "allow: 8081/8082 sirf 172.16.0.0/12 (Docker) se"
}
Write-Host "`nDone. Ab doosri machine se test karo: Test-NetConnection <is-machine-ka-LAN-IP> -Port 8081 -> fail hona chahiye;" -ForegroundColor Green
Write-Host "443 -> succeed. (Is machine se apne hi LAN IP par test bharosemand nahi.)"
