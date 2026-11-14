<#
 watchdog.ps1: llama-server (chat), llama-server (embedding) aur API ko chalta rakhta hai.
 Har 15 second health check; lagataar 3 dafa fail ho to us service ko band karke dobara start karta hai.
 Log: <Root>\logs\watchdog.log  (service ke apne logs bhi wahin)

 SETUP (sirf ek dafa):
   1. Neeche $Root aur har service ke Exe/Args apne asli commands se badlo (start_all.ps1 se copy kar lo).
   2. Test: powershell -ExecutionPolicy Bypass -File scripts\watchdog.ps1
      Phir llama-server ka process band karke dekho: ~45 second mein khud wapas aana chahiye.
   3. Boot par khud chale (apne user account se; SYSTEM account par GPU kabhi kabhi nahi milta):
      schtasks /Create /TN "CompanyAI-Watchdog" /SC ONLOGON /RL HIGHEST /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\company_ai\scripts\watchdog.ps1"
#>
param(
    [int]$IntervalSec = 15,
    [int]$FailLimit = 3
)

# ===================== YAHAN APNE PATH / COMMANDS LIKHO =====================
$Root = "C:\company_ai"        # backend folder (jahan app\ hai)

$Services = @(
    @{ Name = "llama-chat";  Port = 8081; Health = "http://127.0.0.1:8081/health";
       Exe = "C:\llama\llama-server.exe";
       Args = "-m C:\models\Qwen3.5-9B-Q4_K_M.gguf -ngl 99 -c 4096 -np 1 --flash-attn on --port 8081";
       WorkDir = "C:\llama"; WarmupSec = 60 },
    @{ Name = "llama-embed"; Port = 8082; Health = "http://127.0.0.1:8082/health";
       Exe = "C:\llama\llama-server.exe";
       Args = "-m C:\models\Qwen3-Embedding-0.6B-Q8_0.gguf --embedding --pooling last -ngl 0 --port 8082";
       WorkDir = "C:\llama"; WarmupSec = 20 },
    @{ Name = "api";         Port = 8000; Health = "http://127.0.0.1:8000/health";
       Exe = "$Root\.venv\Scripts\python.exe";
       Args = "-m uvicorn app.main:app --host 0.0.0.0 --port 8000";
       WorkDir = $Root; WarmupSec = 15 }
)
# ============================================================================

$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "watchdog.log"

function Write-Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 5MB)) {
        Move-Item $LogFile "$LogFile.old" -Force
    }
    Add-Content -Path $LogFile -Value $line
}

function Test-Health([string]$url) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Stop-ByPort([int]$port) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}

function Start-Svc($s) {
    $out = Join-Path $LogDir ("{0}.out.log" -f $s.Name)
    $err = Join-Path $LogDir ("{0}.err.log" -f $s.Name)
    Start-Process -FilePath $s.Exe -ArgumentList $s.Args -WorkingDirectory $s.WorkDir `
        -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null
}

$fails = @{}
foreach ($s in $Services) { $fails[$s.Name] = 0 }
Write-Log "watchdog start (interval=$IntervalSec s, fail limit=$FailLimit)"

while ($true) {
    foreach ($s in $Services) {
        if (Test-Health $s.Health) {
            if ($fails[$s.Name] -gt 0) { Write-Log ("{0}: wapas theek" -f $s.Name) }
            $fails[$s.Name] = 0
            continue
        }
        $fails[$s.Name]++
        Write-Log ("{0}: health fail {1}/{2}" -f $s.Name, $fails[$s.Name], $FailLimit)
        if ($fails[$s.Name] -ge $FailLimit) {
            Write-Log ("{0}: RESTART" -f $s.Name)
            Stop-ByPort $s.Port
            Start-Sleep -Seconds 2
            Start-Svc $s
            Start-Sleep -Seconds $s.WarmupSec
            $fails[$s.Name] = 0
        }
    }
    Start-Sleep -Seconds $IntervalSec
}
