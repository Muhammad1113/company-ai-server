# Usage:  .\scripts\smoke_test.ps1            (auth off)
#         .\scripts\smoke_test.ps1 -ApiKey YOUR_KEY
param(
    [string]$Base = "http://127.0.0.1:8000",
    [string]$ApiKey = ""
)

$headers = @{}
$curlAuth = @()
if ($ApiKey) {
    $headers["Authorization"] = "Bearer $ApiKey"
    $curlAuth = @("-H", "Authorization: Bearer $ApiKey")
}

function Section($t) { Write-Host "`n=== $t" -ForegroundColor Cyan }
$utf8 = New-Object System.Text.UTF8Encoding $false
$pattern = '^company_ai_(active_inferences|queued_requests|inference_total|inference_cancellations_total|cache_requests_total|rate_limited_total|auth_failures_total)'

function Show-Metrics {
    ((Invoke-WebRequest -UseBasicParsing "$Base/metrics").Content -split "`n") |
        Where-Object { $_ -match $pattern }
}

Section "health / ready"
Invoke-RestMethod "$Base/health"
Invoke-RestMethod "$Base/ready"

Section "completions x2 (2nd should be X-Cache: HIT)"
$body = @{ message = "Explain machine learning in simple words"; temperature = 0.2; max_tokens = 60 } | ConvertTo-Json -Compress
1..2 | ForEach-Object {
    $r = Invoke-WebRequest -UseBasicParsing "$Base/api/v1/chat/completions" -Method Post `
        -ContentType "application/json" -Headers $headers -Body $body
    "request $_ -> HTTP $($r.StatusCode)  X-Cache: $([string]$r.Headers['X-Cache'])  X-Request-ID: $([string]$r.Headers['X-Request-ID'])"
}

Section "stream (full)"
$file = Join-Path $env:TEMP "company_ai_req.json"
$streamBody = @{ message = "Explain AI in two sentences"; temperature = 0.2; max_tokens = 60 } | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($file, $streamBody, $utf8)
curl.exe -s -N "$Base/api/v1/chat/stream" -H "Content-Type: application/json" @curlAuth --data-binary "@$file" |
    Select-Object -Last 4

Section "cancellation test (curl 1 sec baad kat jayega)"
$longBody = @{ message = "Write a very long essay about the history of computers"; temperature = 0.7; max_tokens = 500 } | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($file, $longBody, $utf8)
curl.exe -s -N --max-time 1 "$Base/api/v1/chat/stream" -H "Content-Type: application/json" @curlAuth --data-binary "@$file" | Out-Null
Start-Sleep -Seconds 2
Show-Metrics
Write-Host "`nExpect: active_inferences 0.0 and inference_cancellations_total >= 1.0" -ForegroundColor Yellow

if ($ApiKey) {
    Section "my usage"
    Invoke-RestMethod "$Base/api/v1/usage/me" -Headers $headers | ConvertTo-Json
}
