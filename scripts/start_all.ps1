# Usage:  .\scripts\start_all.ps1                 (chat model + embedding server + API: 3 windows)
#         .\scripts\start_all.ps1 -NoEmbedding    (embedding server ke baghair)
# BGE-M3 ke liye:  -EmbedModel "models\<bge-m3-file>.gguf" -EmbedPooling cls
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$LlamaServer = "C:\Users\sh\llama.cpp\build\bin\Release\llama-server.exe",
    [string]$ChatModel = "models\Qwen3.5-9B-Q4_K_M.gguf",
    [string]$EmbedModel = "models\Qwen3-Embedding-0.6B-Q8_0.gguf",
    [string]$EmbedPooling = "last",
    [switch]$NoEmbedding
)

$required = @($LlamaServer, (Join-Path $Root $ChatModel))
if (-not $NoEmbedding) { $required += (Join-Path $Root $EmbedModel) }
foreach ($p in $required) {
    if (-not (Test-Path $p)) { throw "Missing file: $p" }
}

function Start-Window($title, $command) {
    $full = "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Location '$Root'; $command"
    Start-Process powershell -ArgumentList @("-NoExit", "-Command", $full)
}

Start-Window "llama CHAT :8081" "& '$LlamaServer' -m '$ChatModel' -ngl 99 -c 4096 -np 1 --host 127.0.0.1 --port 8081"

if (-not $NoEmbedding) {
    Start-Window "llama EMBEDDING :8082" "& '$LlamaServer' -m '$EmbedModel' --embedding --pooling $EmbedPooling -c 4096 -np 1 -b 2048 -ub 2048 -ngl 0 --host 127.0.0.1 --port 8082"
}

Write-Host "Models load ho rahe hain (8 sec)..."
Start-Sleep -Seconds 8

# Production-style: --reload nahi (watcher band), 1 worker (queue/cache per-process hain)
Start-Window "company-ai API :8000" "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass; .\.venv\Scripts\Activate.ps1; uvicorn app.main:app --host 127.0.0.1 --port 8000"

Write-Host "Shuru ho gaya. Check:  Invoke-RestMethod http://127.0.0.1:8000/ready"
